"""Signal quality: three guards that keep the Watchdog from crying wolf.

A re-review opened on a false positive costs an analyst an hour and destroys trust in the
feature, which is precisely why real continuous-monitoring products get ignored. The guards
are design rather than tuning:

1. **Match on identity, not name.** A vendor's registered primary domain and legal entity
   name from the dossier, never a bare string match — common company names appear in
   unrelated news constantly.
2. **A confidence threshold on the relevance check.** Below ``WATCHDOG_CONFIDENCE_MIN`` the
   signal is triage, not a re-review.
3. **Only a high-confidence, materially relevant signal auto-opens a review.** Everything
   weaker becomes a card for the analyst.

Failure semantics: the identity match runs before the model call, so an unmatched signal costs
nothing. A relevance call that fails returns ``TRIAGE`` rather than either extreme — failing
open would open reviews on noise, failing closed would silently discard a real breach signal.
A signal for a vendor already reopened is deduplicated on signal id by the agent.
"""

from __future__ import annotations

import logging
import re
from enum import StrEnum

from agents.watchdog.sources import Signal
from shared.domain import RelevanceJudgement, Vendor

log = logging.getLogger("drawbridge.watchdog.relevance")

RELEVANCE_PROMPT = """\
Assess whether this signal is materially relevant to this specific vendor.

You are given the vendor's registered primary domain and legal entity name. A similar
company name is not a match. Return JSON: relevant (bool), confidence (0-1),
reason (one sentence), source_url.

When you are unsure, return low confidence rather than a confident guess.

VENDOR: {vendor}
SIGNAL: {signal}
"""


class Action(StrEnum):
    DISCARD = "discard"
    TRIAGE = "triage"
    OPEN_REREVIEW = "open_rereview"


def matches_identity(
    signal: Signal, primary_domain: str | None, legal_entity_name: str | None
) -> bool:
    """Return whether the signal names this vendor by domain or legal entity.

    Returns ``False`` when the dossier holds neither identifier: an unidentifiable vendor
    produces no automatic re-reviews, which is the conservative direction.

    The legal entity match requires the whole registered name, suffix included, rather than any
    word from it. "Northgate Cloud Infrastructure Ltd" matching an article about "Northgate" is
    exactly the false positive that makes continuous monitoring worth switching off.
    """
    if not primary_domain and not legal_entity_name:
        return False

    haystack = " ".join(
        part for part in (signal.vendor_hint, signal.title, signal.body, signal.url or "") if part
    ).lower()

    if primary_domain and _registrable(primary_domain) in haystack:
        return True
    if legal_entity_name:
        return re.search(rf"\b{re.escape(legal_entity_name.lower())}\b", haystack) is not None
    return False


def evaluate(ctx, vendor: Vendor, signal: Signal) -> Action:
    """Decide what to do with one signal.

    Returns ``TRIAGE`` on a relevance call failure — the safe middle, where an analyst sees the
    signal and decides. A dossier-sourced expiry signal skips the model entirely: the fleet
    computed it from a date it already holds, so there is nothing for a model to assess and
    nothing untrusted to assess it from.
    """
    from shared.config import settings

    if signal.source == "dossier":
        log.info("signal %s is the fleet's own arithmetic; opening a re-review", signal.signal_id)
        return Action.OPEN_REREVIEW

    if not matches_identity(signal, vendor.primary_domain, vendor.legal_entity_name):
        log.info(
            "signal %s does not name %s by domain or legal entity; discarded before the model",
            signal.signal_id,
            vendor.vendor_id,
        )
        return Action.DISCARD

    judgement = judge(ctx, vendor, signal)
    if judgement is None:
        return Action.TRIAGE

    threshold = settings().watchdog_confidence_min
    if judgement.relevant and judgement.confidence >= threshold:
        return Action.OPEN_REREVIEW

    log.info(
        "signal %s for %s: relevant=%s confidence=%.2f (threshold %.2f) — %s",
        signal.signal_id,
        vendor.vendor_id,
        judgement.relevant,
        judgement.confidence,
        threshold,
        judgement.reason,
    )
    return Action.DISCARD if not judgement.relevant else Action.TRIAGE


def judge(ctx, vendor: Vendor, signal: Signal) -> RelevanceJudgement | None:
    """Ask the fast model whether this signal bears on this vendor's security posture.

    The signal carries fetched page content, so this is an external-input task and the router
    applies P2 to it: the stamp written when the feed was screened has to verify before the
    prompt is built. A feed nobody screened cannot reach this model, whatever host it came from.

    Returns ``None`` on any failure, which the caller reads as triage.
    """
    from shared.armor import stamps_for
    from shared.routing import generate

    try:
        result = generate(
            "relevance",
            RELEVANCE_PROMPT.format(vendor=_vendor_line(vendor), signal=_signal_line(signal)),
            ctx,
            response_schema=RelevanceJudgement,
            source_stamps=stamps_for(getattr(ctx, "review_id", "watchdog"), [signal.source]),
        )
    except Exception as exc:  # noqa: BLE001 — triage is the safe middle; see the docstring
        log.warning("relevance call failed for %s: %s — sending to triage", signal.signal_id, exc)
        return None

    parsed = result.parsed
    if isinstance(parsed, RelevanceJudgement):
        return parsed
    try:
        return RelevanceJudgement.model_validate(parsed or {})
    except Exception as exc:  # noqa: BLE001 — an unreadable judgement is not a judgement
        log.warning("relevance output did not validate for %s: %s", signal.signal_id, exc)
        return None


def _vendor_line(vendor: Vendor) -> str:
    return (
        f"legal entity: {vendor.legal_entity_name or 'unknown'}; "
        f"registered domain: {vendor.primary_domain or 'unknown'}; "
        f"category: {vendor.category}"
    )


def _signal_line(signal: Signal) -> str:
    return f"{signal.title}\n{signal.body}".strip()


def _registrable(domain: str) -> str:
    """Return the registrable part of a domain, so a subdomain still matches its owner."""
    parts = [p for p in domain.lower().split(".") if p]
    return ".".join(parts[-2:]) if len(parts) >= 2 else domain.lower()
