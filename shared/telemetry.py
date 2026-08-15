"""Span schema, designed once and used everywhere, because spans become binder sections.

Every span carries ``review_id``, ``agent``, a plain-English ``goal`` (what it was trying to
do), a plain-English ``decision`` (what it concluded), ``model``, ``tokens``, ``cost_usd``,
``latency_ms`` and ``policy_events``. The two English attributes are what let the binder's
reasoning appendix read like prose rather than a log dump, so they are set as each agent is
written rather than retrofitted.

**Spans never carry raw external content.** They carry references, hashes and enumerated
verdicts — ``evidence_ref``, ``chunk_id``, ``sha256``, ``pi_and_jailbreak: MATCH_FOUND`` —
never a vendor-authored string. The reason is structural rather than stylistic: a span becomes
a binder section, so hostile content that reaches the trace has escaped the quarantine boundary
by a side door. Where a human genuinely needs the text, the binder resolves the reference
against the screening record, which is inert by construction.

``set_content_refs`` enforces that rule rather than trusting it, because the wrong version of
that call — attaching an answer summary — is the one a hurry would write.

Exporter follows the mode: console locally, Cloud Trace in cloud.

Failure semantics: a span that cannot be exported logs locally and does not fail the work it
was measuring. Telemetry is never on the critical path. An exception inside a span records the
error and re-raises — telemetry observes failures, it never absorbs them.
"""

from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
from opentelemetry.trace import StatusCode

from shared.config import settings

log = logging.getLogger("drawbridge.telemetry")

_TRACER: trace.Tracer | None = None

_REF_PATTERN = re.compile(
    r"""^(
        [0-9a-f]{16,64}                  # a hash
        | (gs://|quarantine/|clean/)\S+  # a storage reference
        | [\w.\-/]+:[\w.\-/]+            # a namespaced id such as chunk:abc or review:r1
        | [\w.\-]+                       # a bare id
    )$""",
    re.VERBOSE,
)

_VERDICT_PATTERN = re.compile(
    r"^[a-z_]+:(MATCH_FOUND|NO_MATCH_FOUND|NO_MATCH|EXECUTION_SUCCESS|EXECUTION_SKIPPED)$"
)


class RawContentInSpan(Exception):
    """An attribute value looked like external content rather than a reference or verdict."""


def init_tracing(service_name: str) -> None:
    """Configure the exporter for this process. Idempotent.

    Raises:
        Nothing. If the exporter cannot be configured, tracing degrades to local logging and
        the process still starts — an unexportable span must not stop a review.
    """
    global _TRACER
    if _TRACER is not None:
        return

    cfg = settings()
    provider = TracerProvider(
        resource=Resource.create(
            {"service.name": service_name, "drawbridge.mode": cfg.mode.value}
        )
    )

    try:
        if cfg.is_cloud:
            from opentelemetry.exporter.cloud_trace import CloudTraceSpanExporter

            exporter: Any = CloudTraceSpanExporter(project_id=cfg.project_id)
        else:
            exporter = ConsoleSpanExporter()
        provider.add_span_processor(BatchSpanProcessor(exporter))
    except Exception as exc:  # noqa: BLE001 — telemetry never blocks the work it measures
        log.warning("tracing exporter unavailable, degrading to logs: %s", exc)

    trace.set_tracer_provider(provider)
    _TRACER = provider.get_tracer("drawbridge")


def tracer() -> trace.Tracer:
    """Return the process tracer, initialising it on first use."""
    if _TRACER is None:
        init_tracing("drawbridge")
    assert _TRACER is not None
    return _TRACER


@contextmanager
def span(name: str, ctx, **attrs) -> Iterator[Any]:
    """Open a span carrying the standard attributes plus ``attrs``.

    On an exception the span records the error string and an error status, then re-raises.
    """
    with tracer().start_as_current_span(name) as s:
        s.set_attribute("review_id", getattr(ctx, "review_id", "") or "")
        s.set_attribute("agent", getattr(ctx, "agent", "") or "")
        for key, value in attrs.items():
            if value is not None:
                s.set_attribute(key, value)
        try:
            yield s
        except Exception as exc:
            s.set_attribute("error", f"{type(exc).__name__}: {exc}")
            s.set_status(trace.Status(StatusCode.ERROR))
            raise


def sha(text: str) -> str:
    """Return the hash that stands in for a piece of content in a span."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def set_content_refs(s, *, ref: str, sha256: str, verdict: str) -> None:
    """Attach content provenance to a span without attaching the content.

    Raises:
        RawContentInSpan: when a value is not a reference, a hash or an enumerated verdict.
            The check exists because the wrong version of this call is the one a hurry would
            write, and the cost of getting it wrong is hostile content in the audit binder.
    """
    for label, value in (("ref", ref), ("sha256", sha256)):
        if not isinstance(value, str) or not _REF_PATTERN.match(value):
            raise RawContentInSpan(
                f"{label}={value!r} is not a reference or hash. Spans carry refs, hashes and "
                "enumerated verdicts only — never vendor-authored text."
            )
    if not _VERDICT_PATTERN.match(verdict):
        raise RawContentInSpan(
            f"verdict={verdict!r} is not an enumerated verdict such as "
            "'pi_and_jailbreak:MATCH_FOUND'."
        )

    s.set_attribute("inputs_ref", ref)
    s.set_attribute("inputs_sha256", sha256)
    s.set_attribute("verdict", verdict)


def record_decision(s, *, goal: str, decision: str) -> None:
    """Record what this step was trying to do and what it concluded, in plain English.

    Both strings are authored by the fleet, never quoted from vendor-supplied content. These
    two attributes are what make the binder's reasoning appendix readable, so they are set on
    every meaningful span rather than only the top-level ones.
    """
    s.set_attribute("goal", goal)
    s.set_attribute("decision", decision)


def record_policy_event(s, *, policy: str, outcome: str, detail: str) -> None:
    """Record a gateway policy decision on the current span.

    ``detail`` names the template and filter that fired, never the content that tripped it.
    """
    s.add_event(
        "policy",
        attributes={"policy": policy, "outcome": outcome, "detail": detail},
    )
