"""Build the PDF evidence fixtures from their Markdown sources.

    python synthetic-vendors/build_pdf_fixtures.py

A real vendor uploads a PDF. The Markdown sources stay as the readable form of each document —
they are what a person reviews when checking that a fixture says what it claims to — and this
generates the PDF the pipeline actually receives.

**The concealment travels.** ``security-overview.md`` hides its planted payload in an inline
HTML span styled to match the page background; the PDF hides the same sentences behind a white
fill colour in the text layer. Both are invisible to a reader and fully present to an extractor,
which is the property the screening claim depends on. Testing concealment only against the
Markdown would have tested the HTML unwrapper and nothing else.

Regenerate rather than edit: the PDFs are build output, and hand-editing one would let it drift
from the Markdown a reader is checking.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PACK = REPO / "synthetic-vendors"

CONCEALED_SPAN = re.compile(r'<span style="color:#ffffff[^"]*">(.*?)</span>', re.S | re.I)

SOURCES = (
    "nimbuswrite/evidence/security-overview.md",
    "injection-corpus/variant-01/payload.md",
)


def build(source: Path) -> Path:
    """Render one Markdown document to a PDF, preserving its concealed text as white fill."""
    sys.path.insert(0, str(REPO))
    from tests.support.pdf import multi_line_pdf

    raw = source.read_text()
    concealed = [" ".join(m.split()) for m in CONCEALED_SPAN.findall(raw)]
    visible = CONCEALED_SPAN.sub("", raw)

    lines = [
        " ".join(line.split())
        for line in _strip_markdown(visible).splitlines()
        if line.strip()
    ]

    target = source.with_suffix(".pdf")
    target.write_bytes(multi_line_pdf(lines, concealed=concealed))
    return target


def _strip_markdown(text: str) -> str:
    """Reduce Markdown to the plain text a PDF page would show."""
    text = re.sub(r"^#+\s*", "", text, flags=re.M)
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text, flags=re.S)
    text = re.sub(r"\|", " ", text)
    return re.sub(r"^\s*[-:]+\s*$", "", text, flags=re.M)


def main() -> int:
    for relative in SOURCES:
        source = PACK / relative
        if not source.is_file():
            print(f"missing source: {source}", file=sys.stderr)
            return 1
        target = build(source)
        print(f"built {target.relative_to(REPO)} from {source.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
