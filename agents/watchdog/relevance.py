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
A signal for a vendor already reopened is deduplicated on signal id.
"""

from __future__ import annotations

from enum import StrEnum

from agents.watchdog.sources import Signal
from shared.domain import Vendor

RELEVANCE_PROMPT = """\
Assess whether this signal is materially relevant to this specific vendor.

You are given the vendor's registered primary domain and legal entity name. A similar
company name is not a match. Return JSON: materially_relevant (bool), confidence (0-1),
reason (one sentence).

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
    """
    raise NotImplementedError


def evaluate(ctx, vendor: Vendor, signal: Signal) -> Action:
    """Decide what to do with one signal.

    Returns ``TRIAGE`` on a relevance call failure — the safe middle, where an analyst sees
    the signal and decides.
    """
    raise NotImplementedError
