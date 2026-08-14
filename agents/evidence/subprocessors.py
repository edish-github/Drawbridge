"""The fourth-party chain: your vendor's vendors.

The rubric allocates fifteen points to the subprocessor and fourth-party domain, and this is
the pipeline behind them. Structured extraction on the fast model produces a subprocessor
list; a deterministic set-difference against the approved-vendor register decides which of
them the organisation has never reviewed.

The thesis is *your attack surface is now other companies*; this is that thesis applied
recursively — the vendor processes your customer text, and the vendor's model provider
processes it too, a company you never reviewed and never signed anything with. Durable memory
makes it compound: the second review naming the same model provider recognises it.

Failure semantics: extraction output that does not validate raises and nothing is saved — a
half-extracted subprocessor list would make the set-difference produce false "unknown" rows,
which is a finding against a vendor for a parsing failure. An unavailable approved-vendor
register leaves ``known_to_org`` unset and produces no findings, logged in degraded mode:
naming an unknown fourth party the organisation has in fact already reviewed is a false
positive with a real cost.
"""

from __future__ import annotations

from agents.evidence.extractors import ControlClaim  # noqa: F401  (shared extraction shape)
from shared.domain import Finding, Subprocessor

SUBPROCESSOR_PROMPT = """\
Extract the subprocessor list from the passages below as structured data.

For each subprocessor return: name, purpose, and whether it processes customer data.
Report only what the document states. If the document does not say whether a subprocessor
processes customer data, return null rather than inferring it.

PASSAGES:
{passages}
"""


def extract_chain(ctx, review_id: str, vendor_id: str, doc_refs: list[str]) -> list[Finding]:
    """Extract the subprocessor list, resolve it against the register, and return findings.

    A subprocessor that processes customer data and is unknown to the organisation becomes a
    ``subprocessors`` finding labelled ``source="rule"`` — the set-difference is arithmetic,
    so the finding is too.
    """
    raise NotImplementedError


def approved_vendor_register() -> dict[str, str]:
    """Return approved vendor names mapped to their most recent review id.

    Raises:
        Nothing. An unavailable register returns an empty mapping and the caller produces no
        findings, logged in degraded mode.
    """
    raise NotImplementedError


def save_subprocessor(vendor_id: str, sub: Subprocessor) -> str:
    """Persist one subprocessor and return its id."""
    raise NotImplementedError
