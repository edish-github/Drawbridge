"""Deterministic checks: the findings that are arithmetic rather than judgement.

Certificate expiry, report-period staleness, whether the report's scope covers the service
being bought, and whether an auditor and an opinion are named. A date comparison should never
be a model's job.

Every finding produced here carries ``source="rule"``. Every finding produced by
cross-examination carries ``source="model"``. An auditor cares enormously about that
distinction, and it removes a class of model error from the hero demo: an expired certificate
becomes a guaranteed finding rather than a hoped-for one.

Failure semantics: a document with a missing or unparseable date produces a finding recording
that the date could not be established, rather than being skipped — a missing expiry date on
a certificate is itself a compliance-posture finding. These functions make no model call and
no network call, so they cannot fail for a dependency reason.
"""

from __future__ import annotations

from datetime import date

from agents.evidence.extractors import DocumentFacts
from shared.domain import Finding

STALE_REPORT_MONTHS = 12


def deterministic_checks(
    review_id: str, docs: list[DocumentFacts], *, today: date
) -> list[Finding]:
    """Run every arithmetic check across the review's documents.

    ``today`` is injected rather than read from the system clock so a compressed demo run and
    a test both produce the same findings for the same fixtures.
    """
    raise NotImplementedError


def rule_finding(
    review_id: str,
    domain: str,
    severity: str,
    summary: str,
    *,
    evidence_ref: str | None = None,
) -> Finding:
    """Construct a finding labelled ``source="rule"``.

    Raises:
        ValueError: when ``domain`` is not a rubric domain or ``severity`` is not one of low,
            medium or high. A finding with an unmapped domain would silently fail to reach
            the score.
    """
    raise NotImplementedError


def covers(scope: str | None, service: str) -> bool:
    """Return whether a report's scope covers the service being purchased.

    Raises:
        ValueError: when ``scope`` is absent. An absent scope is a finding, not a pass, and
            the caller records it as one.
    """
    raise NotImplementedError


def age_months(d: date, *, today: date) -> int:
    """Return whole months between ``d`` and ``today``."""
    raise NotImplementedError
