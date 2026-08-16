"""Build a one-page PDF whose text is styled to match the page background.

The concealment technique the injection corpus starts with is text coloured to match the page:
invisible to someone reading the rendered document, fully present in the text layer a parser
reads. The vendor fixtures express it as inline HTML because they are Markdown; this builds the
same trick in an actual PDF, so the extractor is tested against the format it will meet in a
real upload rather than only against the format the fixtures happen to use.

Hand-assembled rather than produced by a library. The whole point is control over what lands in
the text layer, and a writer that helpfully normalised the colour operator would remove the
thing under test.
"""

from __future__ import annotations

WHITE_FILL = "1 1 1 rg"
BLACK_FILL = "0 0 0 rg"


def one_page_pdf(visible: str, concealed: str = "") -> bytes:
    """Return a single-page PDF containing ``visible`` text and optional ``concealed`` text.

    ``concealed`` is written with a white fill colour on the default white page, so it is
    invisible when rendered and indistinguishable from any other text to an extractor.
    """
    lines = [f"BT /F1 12 Tf {BLACK_FILL} 72 720 Td ({_escape(visible)}) Tj ET"]
    if concealed:
        lines.append(f"BT /F1 12 Tf {WHITE_FILL} 72 690 Td ({_escape(concealed)}) Tj ET")
    stream = "\n".join(lines).encode("latin-1")

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"

    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_at}\n".encode()
    )
    out += b"%%EOF\n"
    return bytes(out)


def _escape(text: str) -> str:
    return text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
