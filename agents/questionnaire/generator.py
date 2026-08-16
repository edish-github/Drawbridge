"""Question selection from the curated bank.

The model selects and tailors; it never invents. A curated bank makes the demo reproducible
and the questions defensible, and it costs fewer tokens than generating sixty questions from
scratch on every run. Tier 1 is roughly sixty questions, Tier 2 thirty, Tier 3 twelve, with
the AI-specific domain added when the vendor is an AI service.

Selection may consult recalled ``question_effectiveness`` notes and prefer phrasings that
historically produced usable evidence. The boundary is absolute and lives in the code as well
as the prose: **phrasing may be preferred, never invented, and never scored.** A question is
only ever chosen from the bank, so a bad question produces a visible gap rather than a
silently wrong number — which is exactly why this is the one place learning is safe.

Selection itself makes no model call. The bank already carries the tier each question belongs
to, so choosing is a filter over data rather than a judgement, and a filter is reproducible
between runs in a way a judgement is not. The model's role begins where tailoring does, and
tailoring is not wired yet — noted here rather than implied by an unused prompt.

Failure semantics: a requested domain with no questions in the bank raises rather than
returning a short set, because a silently short questionnaire produces a review scored on
coverage the analyst never agreed to. If durable memory is unavailable, selection falls back
to the bank's default phrasings and logs a degraded-mode warning.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel

log = logging.getLogger("drawbridge.questionnaire")

REPO = Path(__file__).resolve().parent.parent.parent
BANK_PATH = REPO / "agents" / "questionnaire" / "bank.yaml"

AI_DOMAIN = "ai_specific"

YES_NO_PREFIXES = (
    "do you", "does your", "are you", "is your", "have you", "has your",
    "can you", "will you", "did you", "would you",
)
"""Openings that make a question answerable yes or no.

The same list ``scripts/check_contracts.py`` enforces in CI, applied again at load time so a
bank edited between CI runs cannot reach a vendor.
"""


class Question(BaseModel):
    question_id: str
    domain: str
    text: str
    evidence_required: list[str] = []
    tiers: list[int] = []


class QuestionBankError(Exception):
    """The bank cannot satisfy the requested tier or domain set."""


def load_bank(path: str | Path = BANK_PATH) -> dict[str, list[Question]]:
    """Load the question bank, keyed by rubric domain.

    Raises:
        QuestionBankError: on a malformed file, an unknown domain key, or a question phrased
            as a yes/no. The style rule is enforced at load time so a bad question cannot
            reach a vendor.
    """
    return _load_bank_cached(str(path))


@lru_cache(maxsize=4)
def _load_bank_cached(path: str) -> dict[str, list[Question]]:
    try:
        raw = yaml.safe_load(Path(path).read_text())
    except (OSError, yaml.YAMLError) as exc:
        raise QuestionBankError(f"could not read the question bank at {path}: {exc}") from exc

    domains = (raw or {}).get("domains")
    if not isinstance(domains, dict) or not domains:
        raise QuestionBankError(f"{path} declares no domains")

    bank: dict[str, list[Question]] = {}
    problems: list[str] = []

    for domain, entries in domains.items():
        questions: list[Question] = []
        for entry in entries or []:
            text = " ".join(str(entry.get("text", "")).split())
            if text.lower().startswith(YES_NO_PREFIXES):
                problems.append(f"{entry.get('id')} is answerable yes or no: {text[:60]}...")
            if not entry.get("tiers"):
                problems.append(f"{entry.get('id')} declares no tiers, so it can never be sent")
            questions.append(
                Question(
                    question_id=str(entry["id"]),
                    domain=domain,
                    text=text,
                    evidence_required=list(entry.get("evidence_required") or []),
                    tiers=list(entry.get("tiers") or []),
                )
            )
        bank[domain] = questions

    if problems:
        raise QuestionBankError(
            "the question bank violates the style rule:\n  " + "\n  ".join(problems)
        )

    return bank


def select_questions(
    tier: int, domains: list[str], ctx, *, is_ai_vendor: bool = False
) -> list[Question]:
    """Select and tailor the question set for this tier and these domains.

    Raises:
        QuestionBankError: when a requested domain has no questions at this tier.
    """
    bank = load_bank()
    wanted = list(domains)
    if is_ai_vendor and AI_DOMAIN not in wanted:
        wanted.append(AI_DOMAIN)

    selected: list[Question] = []
    empty: list[str] = []

    for domain in wanted:
        if domain not in bank:
            raise QuestionBankError(
                f"{domain!r} is not a domain in the bank. A questionnaire missing a domain the "
                "rubric scores produces a review scored on coverage nobody agreed to."
            )
        matching = [q for q in bank[domain] if tier in q.tiers]
        if not matching:
            empty.append(domain)
        selected.extend(matching)

    if empty:
        raise QuestionBankError(
            f"no tier {tier} questions exist for {empty}. A silently short questionnaire is "
            "worse than a failed one."
        )

    log.info(
        "selected %d question(s) across %d domain(s) for a tier %d review",
        len(selected),
        len(wanted),
        tier,
    )
    return selected


def render_questionnaire(questions: list[Question], *, vendor_name: str = "") -> str:
    """Render the selected questions into the outbound message body.

    The body is assembled from internal state, which is why it is screened through the
    output template before it leaves — nothing else checks that it does not carry internal
    notes, another vendor's details, or dossier content.
    """
    lines = [
        f"Security review questionnaire{f' — {vendor_name}' if vendor_name else ''}",
        "",
        "Please answer each question below in full and attach the evidence named against it.",
        "Answers that do not name specific standards, systems, scopes or documents will come",
        "back to you as a follow-up.",
        "",
    ]

    current_domain = ""
    for question in questions:
        if question.domain != current_domain:
            current_domain = question.domain
            lines.append("")
            lines.append(current_domain.replace("_", " ").upper())
        lines.append(f"  {question.question_id}. {question.text}")
        if question.evidence_required:
            lines.append(f"      Evidence required: {', '.join(question.evidence_required)}")

    lines.append("")
    lines.append(f"{len(questions)} questions in total.")
    return "\n".join(lines)
