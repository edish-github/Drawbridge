"""Pass one: structured control claims out of a screened document.

Control name, stated implementation, scope, exceptions, dates, and the auditor where one is
named. Structured extraction on the fast model, because this is a shape problem rather than a
judgement problem.

Failure semantics: a document that cannot be read or whose text layer is empty produces a
``needs_human`` finding carrying the file reference, never a silent skip. A document
containing images with no extractable text alongside them is flagged for human review rather
than silently passed — text extraction reads the text layer, so an instruction rendered as an
image has nothing to extract, and that blind spot is bounded rather than denied. Extraction
output that does not validate raises; nothing partial is written.
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel


class ControlClaim(BaseModel):
    control: str
    stated_implementation: str
    scope: str | None = None
    exceptions: list[str] = []
    effective_from: date | None = None
    effective_to: date | None = None


class DocumentFacts(BaseModel):
    """The structured fields the deterministic checks operate on."""

    doc_ref: str
    name: str
    auditor: str | None = None
    opinion: str | None = None
    scope: str | None = None
    cert_expiry: date | None = None
    report_period_end: date | None = None
    has_unextractable_images: bool = False


def extract_controls(doc_ref: str, review_id: str, ctx) -> list[ControlClaim]:
    """Extract control claims from one screened document.

    Raises:
        ValueError: when the model's output does not validate against ``ControlClaim``.
    """
    raise NotImplementedError


def extract_document_facts(doc_ref: str, review_id: str, ctx) -> DocumentFacts:
    """Extract the dated, named fields the deterministic checks compare.

    Dates are extracted here and compared in ``checks.py``. A date comparison is never a
    model's job.
    """
    raise NotImplementedError
