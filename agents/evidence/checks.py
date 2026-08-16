"""Deterministic checks: the findings that are arithmetic rather than judgement.

Certificate expiry, report-period staleness, whether the report's scope covers the service
being bought, and whether an auditor and an opinion are named. A date comparison should never
be a model's job.

Every finding produced here carries ``source="rule"``. Every finding produced by
cross-examination carries ``source="model"``. An auditor cares enormously about that
distinction, and it removes a class of model error from the hero demo: an expired certificate
becomes a guaranteed finding rather than a hoped-for one.

A finding that turns on a date also carries ``date_source``, and the two labels answer
different questions. ``rule`` says the conclusion is a comparison the code performed;
``extracted`` says the date it compared was read off a page by a model. Both are true, and
printing only the first would let a date somebody's extractor misread present itself as
arithmetic. The binder prints them side by side.

**These run before the model passes.** The agent's instruction tells it that expiry and
staleness findings will already be present with ``source="rule"`` and that it must not
re-derive them, which is only true if they actually are. Running them first is what makes that
sentence in the prompt a fact rather than a hope, and it stops the same problem being reported
twice with two different provenances.

Failure semantics: a document with a missing or unparseable date produces a finding recording
that the date could not be established, rather than being skipped — a missing expiry date on
a certificate is itself a compliance-posture finding. These functions make no model call and
no network call, so they cannot fail for a dependency reason.
"""

from __future__ import annotations

import logging
import re
from datetime import date

from agents.evidence.extractors import DocumentFacts
from shared.domain import RUBRIC_DOMAINS, Finding

log = logging.getLogger("drawbridge.checks")

STALE_REPORT_MONTHS = 12
SEVERITIES = ("low", "medium", "high")
DATE_SOURCES = ("extracted", "computed", "declared")

STALENESS_GRADES = ((6, "low"), (24, "medium"))
"""Months past the currency threshold, and the severity each overrun earns.

A report one month out of date and one three years out of date are not the same finding, and
charging both at the same severity is the kind of flat rule that makes a score argue badly. The
grade is on the overrun rather than on the age, so the threshold stays the single place the
currency expectation is stated. Beyond the last boundary the severity is high.
"""

_CERTIFICATE_HINTS = ("certificate", "cert", "iso")
_REPORT_HINTS = ("soc", "report", "assurance", "audit")

_STOPWORDS = frozenset(
    {
        "the", "a", "an", "and", "or", "of", "for", "to", "in", "on", "at", "by", "with",
        "its", "our", "their", "this", "that", "platform", "service", "services", "tier",
        "system", "environment", "including", "enterprise", "solution", "product",
    }
)


def deterministic_checks(
    review_id: str, docs: list[DocumentFacts], *, today: date, service: str = ""
) -> list[Finding]:
    """Run every arithmetic check across the review's documents.

    ``today`` is injected rather than read from the system clock so a compressed demo run and
    a test both produce the same findings for the same fixtures.

    Args:
        service: the service being purchased, from the intake form. Scope coverage is checked
            against what is actually being bought rather than against the vendor in general —
            a report covering a different product of the same vendor is the coverage gap this
            check exists to catch.
    """
    findings: list[Finding] = []

    for doc in docs:
        findings.extend(_certificate_checks(review_id, doc, today=today))
        findings.extend(_report_checks(review_id, doc, today=today, service=service))

    log.info("deterministic checks produced %d finding(s) for review=%s", len(findings), review_id)
    return findings


def _certificate_checks(review_id: str, doc: DocumentFacts, *, today: date) -> list[Finding]:
    """Expiry, and the absence of an expiry date, on anything that reads as a certificate."""
    if not _looks_like(doc, _CERTIFICATE_HINTS):
        return []

    if doc.cert_expiry is None:
        return [
            rule_finding(
                review_id,
                "compliance_posture",
                "medium",
                f"No expiry date could be established for the certificate in {doc.name}. "
                "A certificate whose validity cannot be determined is not evidence of one.",
                evidence_ref=doc.doc_ref,
                date_source="extracted",
            )
        ]

    if doc.cert_expiry < today:
        days = (today - doc.cert_expiry).days
        return [
            rule_finding(
                review_id,
                "compliance_posture",
                "high",
                f"The certificate in {doc.name} expired on "
                f"{doc.cert_expiry.isoformat()}, {days} days before this review. "
                "The vendor presented it as current evidence.",
                evidence_ref=doc.doc_ref,
                date_source="extracted",
            )
        ]
    return []


def _report_checks(
    review_id: str, doc: DocumentFacts, *, today: date, service: str
) -> list[Finding]:
    """Staleness, scope coverage, and whether an auditor and an opinion are named."""
    if not _looks_like(doc, _REPORT_HINTS):
        return []

    findings: list[Finding] = []

    if doc.report_period_end is None:
        findings.append(
            rule_finding(
                review_id,
                "compliance_posture",
                "medium",
                f"No report period could be established for {doc.name}. An audit report with "
                "no stated period cannot be shown to be current.",
                evidence_ref=doc.doc_ref,
                date_source="extracted",
            )
        )
    else:
        months = age_months(doc.report_period_end, today=today)
        if months > STALE_REPORT_MONTHS:
            overrun = months - STALE_REPORT_MONTHS
            findings.append(
                rule_finding(
                    review_id,
                    "compliance_posture",
                    staleness_severity(overrun),
                    f"The report period in {doc.name} ended "
                    f"{doc.report_period_end.isoformat()}, {months} months before this review "
                    f"and {overrun} month(s) beyond the {STALE_REPORT_MONTHS}-month currency "
                    "threshold.",
                    evidence_ref=doc.doc_ref,
                    date_source="extracted",
                )
            )

    if service:
        try:
            if not covers(doc.scope, service):
                findings.append(
                    rule_finding(
                        review_id,
                        "compliance_posture",
                        "high",
                        f"The scope of {doc.name} does not name the service being purchased "
                        f"({service}). Assurance that does not cover what is being bought is "
                        "not assurance for this review.",
                        evidence_ref=doc.doc_ref,
                    )
                )
        except ValueError:
            findings.append(
                rule_finding(
                    review_id,
                    "compliance_posture",
                    "medium",
                    f"{doc.name} states no scope, so it cannot be shown to cover the service "
                    "being purchased. An absent scope is a finding, not a pass.",
                    evidence_ref=doc.doc_ref,
                )
            )

    if not doc.auditor or not doc.opinion:
        missing = " and ".join(
            part for part, present in (("auditor", doc.auditor), ("opinion", doc.opinion))
            if not present
        )
        findings.append(
            rule_finding(
                review_id,
                "compliance_posture",
                "medium",
                f"{doc.name} names no {missing}. An audit report without both is not "
                "attributable to anyone.",
                evidence_ref=doc.doc_ref,
            )
        )

    return findings


def rule_finding(
    review_id: str,
    domain: str,
    severity: str,
    summary: str,
    *,
    evidence_ref: str | None = None,
    date_source: str | None = None,
) -> Finding:
    """Construct a finding labelled ``source="rule"``.

    Args:
        date_source: where the date this finding turns on came from, for a finding that turns
            on one. ``source="rule"`` describes the conclusion; on a date comparison the input
            is usually a date a model read off a page, and attributing the two separately is
            what stops the provenance label claiming more than it proves.

    Raises:
        ValueError: when ``domain`` is not a rubric domain, ``severity`` is not one of low,
            medium or high, or ``date_source`` is outside the vocabulary. A finding with an
            unmapped domain would silently fail to reach the score.
    """
    if domain not in RUBRIC_DOMAINS:
        raise ValueError(
            f"{domain!r} is not a rubric domain. A finding the rubric cannot map is a finding "
            f"that never reaches the score. Domains: {sorted(RUBRIC_DOMAINS)}"
        )
    if severity not in SEVERITIES:
        raise ValueError(f"{severity!r} is not a severity; expected one of {SEVERITIES}")
    if date_source is not None and date_source not in DATE_SOURCES:
        raise ValueError(
            f"{date_source!r} is not a date source; expected one of {DATE_SOURCES}"
        )

    return Finding(
        finding_id=f"{review_id}:rule:{_slug(summary)}",
        review_id=review_id,
        domain=domain,
        severity=severity,
        source="rule",
        date_source=date_source,
        contradiction=False,
        summary=summary,
        evidence_ref=evidence_ref,
    )


def covers(scope: str | None, service: str) -> bool:
    """Return whether a report's scope covers the service being purchased.

    A word-overlap test rather than a substring match: a scope reads "the DataDynamo Routing
    Platform, including the enterprise tier" and a purchase reads "DataDynamo Routing Platform,
    enterprise tier", and a substring comparison of those two returns false for a report that
    plainly covers the service.

    Raises:
        ValueError: when ``scope`` is absent. An absent scope is a finding, not a pass, and
            the caller records it as one.
    """
    if not scope or not scope.strip():
        raise ValueError("scope is absent; an absent scope is a finding rather than a pass")

    wanted = _significant_words(service)
    if not wanted:
        return True
    return wanted.issubset(_significant_words(scope))


def staleness_severity(overrun_months: int) -> str:
    """Return the severity for a report period this many months past the threshold."""
    for boundary, severity in STALENESS_GRADES:
        if overrun_months <= boundary:
            return severity
    return "high"


def age_months(d: date, *, today: date) -> int:
    """Return whole months between ``d`` and ``today``."""
    months = (today.year - d.year) * 12 + (today.month - d.month)
    if today.day < d.day:
        months -= 1
    return months


def _looks_like(doc: DocumentFacts, hints: tuple[str, ...]) -> bool:
    """Classify a document by its name and the fields extraction found on it."""
    name = doc.name.lower()
    if any(hint in name for hint in hints):
        return True
    if hints is _CERTIFICATE_HINTS:
        return doc.cert_expiry is not None
    return doc.report_period_end is not None or bool(doc.opinion)


def _significant_words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in _STOPWORDS}


def _slug(summary: str) -> str:
    import hashlib

    return hashlib.sha256(summary.encode("utf-8")).hexdigest()[:12]
