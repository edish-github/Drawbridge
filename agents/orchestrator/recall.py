"""What a second review knows before it has read anything.

The payoff for durable memory being a layer rather than a cache. A review of a vendor this
organisation has bought from before does not start at zero: it opens holding the outcome, the
band, the conditions somebody attached, the certificate that was expiring, the address the
correspondence used, and whether this vendor has previously tried to manipulate the review.

Two things visibly change because of it, and both are computed here rather than judged:

**The tier never falls.** A vendor that reached Tier 1 stays at Tier 1 unless the deterministic
floor puts it higher. That is the same rule the re-tier obeys within a review, applied across
reviews — a vendor whose second intake form is more modest than their first does not get a
lighter review for filling the form in differently.

**Questions in a domain that was clean are not asked again.** Three conditions, all of them
arithmetic, and the third is the one that matters:

1. the answer was rated ``usable`` last time — a weak answer is worth another go and a
   non-answer is worth asking differently;
2. the question has not been reworded since, which a digest comparison decides;
3. **the prior review recorded no finding in that question's domain.**

The third is what keeps this from being a rubber stamp. A domain that produced a finding is
asked again in full, because the finding is the reason the review happened and asking around it
would be the worst possible economy. On the pack's own vendor that leaves one domain carried
out of six — the only one that came back clean.

A vendor carrying a conduct flag carries nothing at all. Somebody who tried to manipulate the
last review does not get the benefit of the doubt on the answers they gave during it.

**Nothing recalled is prose.** Every field below is a term, an identifier or a number, because
the dossier is read into a planning prompt before any screening has run in the new review — the
one place in the system where content from a previous review could reach a model without
passing a detector in this one. The conditions are the exception that proves it: the note
records that they exist and names the review, and the sentences are read from that review's
approval record in the ledger.

Failure semantics: an unavailable dossier returns an empty recall and the review plans from the
intake form alone. Worse-informed is not incorrect, and a review that could not open because a
memory service was down would be a review blocked by the one layer that is context rather than
control.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from shared.memory import Dossier

log = logging.getLogger("drawbridge.recall")


@dataclass
class Recalled:
    """What the dossier says about a vendor, resolved into the things a plan can use."""

    vendor_id: str
    prior_review_id: str | None = None
    prior_tier: int = 0
    prior_outcome: str | None = None
    prior_band: str | None = None
    prior_score: int | None = None
    conditions: list[str] = field(default_factory=list)
    adversarial: bool = False
    cert_expiry: str | None = None
    contact: str | None = None
    answered_well: dict[str, str] = field(default_factory=dict)
    domains_with_findings: set[str] = field(default_factory=set)

    @property
    def is_repeat(self) -> bool:
        """Whether this vendor has been reviewed before."""
        return self.prior_review_id is not None

    def carried_questions(self, bank: dict) -> set[str]:
        """Return the question ids a new review need not ask again.

        Three gates, in the order that makes the cheapest one first. See the module docstring
        for why the domain gate is the one that keeps this honest.
        """
        from agents.orchestrator.closeout import digest

        if self.adversarial:
            log.info(
                "%s carries a conduct flag; nothing is carried forward and every question is "
                "asked again",
                self.vendor_id,
            )
            return set()

        current = {
            question.question_id: (digest(question.text), domain)
            for domain, questions in bank.items()
            for question in questions
        }

        carried, reworded, dirty = set(), set(), set()
        for question_id, recorded in self.answered_well.items():
            known = current.get(question_id)
            if known is None:
                continue
            recorded_digest, domain = known
            if recorded_digest != recorded:
                reworded.add(question_id)
            elif domain in self.domains_with_findings:
                dirty.add(question_id)
            else:
                carried.add(question_id)

        if reworded:
            log.info(
                "%d question(s) were answered well last time but have been reworded since: %s",
                len(reworded),
                ", ".join(sorted(reworded)),
            )
        if dirty:
            log.info(
                "%d question(s) were answered well but sit in a domain that produced a finding, "
                "so they are asked again: %s",
                len(dirty),
                ", ".join(sorted(self.domains_with_findings)),
            )
        return carried

    def summary(self) -> str:
        """One line naming what was recalled, for the timeline entry and the log."""
        if not self.is_repeat:
            return "no prior review of this vendor"

        parts = [
            f"prior review {self.prior_review_id}",
            f"{self.prior_outcome} at Tier {self.prior_tier}",
        ]
        if self.prior_band:
            parts.append(f"band {self.prior_band}")
        if self.conditions:
            parts.append(f"{len(self.conditions)} condition(s) attached")
        if self.adversarial:
            parts.append("ADVERSARIAL CONDUCT on record")
        if self.cert_expiry:
            parts.append(f"certificate expiring {self.cert_expiry}")
        if self.answered_well:
            parts.append(f"{len(self.answered_well)} question(s) answered well")
        if self.domains_with_findings:
            parts.append(f"{len(self.domains_with_findings)} domain(s) with findings")
        return " · ".join(parts)


def recall(vendor_id: str, dossier: Dossier | None = None) -> Recalled:
    """Resolve a vendor's dossier into what a new review can act on.

    Raises:
        Nothing. See the module docstring on why this degrades rather than blocking.
    """
    from shared.memory import recall_dossier

    try:
        notes = (dossier or recall_dossier(vendor_id)).notes
    except Exception as exc:  # noqa: BLE001 — recall is context, not a control
        log.warning("degraded mode: could not recall the dossier for %s: %s", vendor_id, exc)
        return Recalled(vendor_id=vendor_id)

    out = Recalled(vendor_id=vendor_id)
    for note in sorted(notes, key=lambda n: n.at):
        value = note.value or {}
        if note.type == "outcome":
            out.prior_review_id = str(value.get("review_id") or "") or None
            out.prior_outcome = str(value.get("value") or "") or None
            out.prior_tier = int(value.get("tier") or 0)
        elif note.type == "band":
            out.prior_band = str(value.get("value") or "") or None
            out.prior_score = value.get("score")
        elif note.type == "conduct_flag":
            out.adversarial = True
        elif note.type == "cert_expiry":
            out.cert_expiry = str(value.get("expires_at") or value.get("value") or "") or None
        elif note.type == "contact_change":
            out.contact = str(value.get("email") or "") or None
        elif note.type == "question_effectiveness":
            _record_rating(out, value)

    out.conditions = _conditions(out.prior_review_id)
    out.domains_with_findings = _domains_with_findings(out.prior_review_id)
    log.info("recalled for %s: %s", vendor_id, out.summary())
    return out


def _domains_with_findings(prior_review_id: str | None) -> set[str]:
    """Return the domains the prior review recorded a finding in.

    Read from the ledger rather than from durable memory, and deliberately not summarised into
    a note: which domains were clean is derivable from findings that are already recorded, and a
    note that duplicated it would be a second copy to keep in step with the first.

    Raises:
        Nothing. An unreadable ledger returns every domain as dirty, which asks more questions
        rather than fewer — the conservative direction for a control whose failure mode is
        skipping a question that should have been asked.
    """
    if not prior_review_id:
        return set()

    from google.cloud.firestore_v1 import FieldFilter

    from shared.clients import firestore_client
    from shared.domain import RUBRIC_DOMAINS

    try:
        docs = (
            firestore_client()
            .collection("findings")
            .where(filter=FieldFilter("review_id", "==", prior_review_id))
            .stream()
        )
        return {str((d.to_dict() or {}).get("domain", "")) for d in docs} - {""}
    except Exception as exc:  # noqa: BLE001 — see the docstring on the safe direction
        log.warning(
            "could not read the prior review's findings (%s); every domain is treated as "
            "having had one, so nothing is carried",
            exc,
        )
        return set(RUBRIC_DOMAINS)


def _record_rating(out: Recalled, value: dict) -> None:
    """Carry a question only when the last answer was usable.

    A later note supersedes an earlier one for the same question, which is why this runs in
    timestamp order: a question answered well in review one and badly in review two is a
    question to ask again.
    """
    from agents.orchestrator.closeout import RATING_USABLE

    question_id = str(value.get("question_id") or "")
    if not question_id:
        return

    if str(value.get("value")) == RATING_USABLE:
        out.answered_well[question_id] = str(value.get("text_digest") or "")
    else:
        out.answered_well.pop(question_id, None)


def _conditions(prior_review_id: str | None) -> list[str]:
    if not prior_review_id:
        return []
    from shared.approvals import conditions_for

    return conditions_for(prior_review_id)
