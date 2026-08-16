"""Local text extraction. Raw bytes never reach a generative model.

This is the seam the whole trust boundary rests on. Vendor uploads are parsed here, in
process, by a parser that has no network and no model behind it; what comes out is what Model
Armor screens and what the clean bucket eventually holds. If extraction quietly dropped part
of a document, screening would return a clean verdict on text that is not the document.

**Concealed text is extracted, not discarded.** The technique the injection corpus starts with
is styling text to match the page background: invisible to a person reading the rendered
document, fully present in the layer a parser reads. So markup is unwrapped and its text kept —
a parser that stripped styled spans along with their styling would be the vulnerability, not the
defence. The only markup whose content is dropped is ``<script>`` and ``<style>``, which carry
no document text.

Format is sniffed from the content rather than trusted from a filename, because the name of an
uploaded object is vendor-supplied.

Failure semantics: a document with no extractable text raises ``UnextractableDocument`` and the
caller flags it for human review. That is the documented blind spot — an instruction rendered as
an image has nothing to extract — and it is bounded rather than denied: nothing with no
extractable text is silently passed as screened-clean.
"""

from __future__ import annotations

import logging
import re
from html.parser import HTMLParser
from io import BytesIO

log = logging.getLogger("drawbridge.extraction")

PDF_MAGIC = b"%PDF-"

DROPPED_ELEMENTS = frozenset({"script", "style"})
"""Elements whose text content is not document text. Everything else is unwrapped and kept."""

_BLOCK_ELEMENTS = frozenset(
    {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section", "table"}
)


class UnextractableDocument(Exception):
    """No text could be extracted. The caller flags for human review, never passes it on."""

    def __init__(self, reason: str) -> None:
        super().__init__(
            f"{reason}. Flagged for human review rather than passed as screened-clean."
        )
        self.reason = reason


def extract(raw: bytes, *, ref: str = "") -> str:
    """Extract the text of a document from its raw bytes.

    Args:
        raw: the object exactly as it was uploaded.
        ref: the object reference, for the log line only. Never used to choose a parser.

    Raises:
        UnextractableDocument: when the document yields no text — an empty object, or a PDF
            whose pages carry images and no text layer.
    """
    if not raw:
        raise UnextractableDocument(f"{ref or 'the object'} is empty")

    text = _extract_pdf(raw) if raw.startswith(PDF_MAGIC) else _extract_markup(raw)

    if not text.strip():
        raise UnextractableDocument(
            f"{ref or 'the object'} yielded no text; it may be images with no text layer"
        )

    log.debug("extracted %d characters from %s", len(text), ref or "an object")
    return text


def _extract_pdf(raw: bytes) -> str:
    """Extract a PDF's text layer, page by page.

    Only the text layer. Text rendered as an image is not read, which is the stated blind spot
    rather than a discovered one.
    """
    from pypdf import PdfReader

    reader = PdfReader(BytesIO(raw))
    pages = [(page.extract_text() or "") for page in reader.pages]
    return "\n\n".join(p.strip() for p in pages if p.strip())


def _extract_markup(raw: bytes) -> str:
    """Decode text and unwrap any markup in it, keeping the text the markup contained.

    Serves plain text, Markdown and HTML through one path, because a Markdown document may
    carry inline HTML and the concealment technique lives in exactly that inline HTML.
    """
    text = raw.decode("utf-8", errors="replace")
    if "<" not in text:
        return text
    return _Unwrapper().unwrap(text)


class _Unwrapper(HTMLParser):
    """Strip markup, keep its text. Styling is discarded; what the styling hid is not."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._suppress = 0

    def unwrap(self, text: str) -> str:
        self._parts = []
        self._suppress = 0
        self.feed(text)
        self.close()
        return re.sub(r"\n{3,}", "\n\n", "".join(self._parts))

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in DROPPED_ELEMENTS:
            self._suppress += 1
        elif tag in _BLOCK_ELEMENTS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in DROPPED_ELEMENTS:
            self._suppress = max(0, self._suppress - 1)
        elif tag in _BLOCK_ELEMENTS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._suppress:
            self._parts.append(data)
