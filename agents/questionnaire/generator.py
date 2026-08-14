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

Failure semantics: a requested domain with no questions in the bank raises rather than
returning a short set, because a silently short questionnaire produces a review scored on
coverage the analyst never agreed to. If durable memory is unavailable, selection falls back
to the bank's default phrasings and logs a degraded-mode warning.
"""

from __future__ import annotations

from pydantic import BaseModel


class Question(BaseModel):
    question_id: str
    domain: str
    text: str
    evidence_required: list[str] = []
    tiers: list[int] = []


class QuestionBankError(Exception):
    """The bank cannot satisfy the requested tier or domain set."""


def load_bank(path: str = "agents/questionnaire/bank.yaml") -> dict[str, list[Question]]:
    """Load the question bank, keyed by rubric domain.

    Raises:
        QuestionBankError: on a malformed file, an unknown domain key, or a question phrased
            as a yes/no. The style rule is enforced at load time so a bad question cannot
            reach a vendor.
    """
    raise NotImplementedError


def select_questions(
    tier: int, domains: list[str], ctx, *, is_ai_vendor: bool = False
) -> list[Question]:
    """Select and tailor the question set for this tier and these domains.

    Raises:
        QuestionBankError: when a requested domain has no questions at this tier.
    """
    raise NotImplementedError


def render_questionnaire(questions: list[Question]) -> str:
    """Render the selected questions into the outbound message body.

    The body is assembled from internal state, which is why it is screened through the
    output template before it leaves — nothing else checks that it does not carry internal
    notes, another vendor's details, or dossier content.
    """
    raise NotImplementedError
