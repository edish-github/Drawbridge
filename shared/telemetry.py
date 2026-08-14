"""Span schema, designed once and used everywhere, because spans become binder sections.

Every span carries ``review_id``, ``agent``, a plain-English ``goal`` (what it was trying to
do), a plain-English ``decision`` (what it concluded), ``model``, ``tokens``, ``cost_usd``,
``latency_ms`` and ``policy_events``. The two English attributes are what let the binder's
reasoning appendix read like prose rather than a log dump, so they are set as each agent is
written rather than retrofitted.

**Spans never carry raw external content.** They carry references, hashes and enumerated
verdicts — ``evidence_ref``, ``chunk_id``, ``sha256``, ``pi_and_jailbreak: MATCH_FOUND`` —
never a vendor-authored string. The reason is structural rather than stylistic: a span
becomes a binder section, so hostile content that reaches the trace has escaped the
quarantine boundary by a side door. Where a human genuinely needs the text, the binder
resolves the reference against the screening record, which is inert by construction.

Failure semantics: a span that cannot be exported logs locally and does not fail the work it
was measuring. Telemetry is never on the critical path. ``set_content_refs`` rejects values
that look like free text rather than a reference or a hash, so the rule is enforced by the
helper instead of by memory.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any


class RawContentInSpan(Exception):
    """An attribute value looked like external content rather than a reference or verdict."""


@contextmanager
def span(name: str, ctx, **attrs) -> Iterator[Any]:
    """Open a span carrying the standard attributes plus ``attrs``.

    On an exception the span records the error string and an error status, then re-raises —
    telemetry observes failures, it never absorbs them.
    """
    raise NotImplementedError


def set_content_refs(s, *, ref: str, sha256: str, verdict: str) -> None:
    """Attach content provenance to a span without attaching the content.

    Raises:
        RawContentInSpan: when a value is not a reference, a hash or an enumerated verdict.
            The check exists because the wrong version of this call — attaching an answer
            summary — is the one a hurry would write.
    """
    raise NotImplementedError


def record_decision(s, *, goal: str, decision: str) -> None:
    """Record what this step was trying to do and what it concluded, in plain English.

    Both strings are authored by the fleet, never quoted from vendor-supplied content.
    """
    raise NotImplementedError


def init_tracing(service_name: str) -> None:
    """Configure the OpenTelemetry exporter for this process.

    Raises:
        Nothing. If the exporter cannot be configured, tracing degrades to local logging and
        the process still starts.
    """
    raise NotImplementedError
