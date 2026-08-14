"""Incremental reply parsing.

Replies arrive across days and partially. Each message is screened on arrival through the
same path as an upload, parsed, and merged into ``qa_responses`` keyed by question id, with a
confidence score and the source message recorded against every answer.

An answer below the confidence threshold sets ``needs_human`` rather than being recorded as
an answer. Unparseable content is quoted back to the analyst queue with its source reference.

Failure semantics: screening runs before parsing, so an injection in a reply body is blocked,
recorded, and capable of raising Adversarial Conduct before any model sees it. A model call
failure leaves the message unparsed and retries under its idempotency key; a partial parse is
never merged. A reply for a question id that is not in the current plan is recorded as an
addendum rather than discarded.
"""

from __future__ import annotations

from pydantic import BaseModel

CONFIDENCE_MIN = 0.7
"""Below this an answer is marked for human review rather than recorded as an answer. It is
also the trigger for a targeted follow-up.
"""


class ParsedAnswer(BaseModel):
    question_id: str
    text: str
    confidence: float
    source_msg: str
    needs_human: bool = False


def parse_reply(body: str, review_id: str, ctx) -> list[ParsedAnswer]:
    """Parse a screened reply body into answers keyed by question id.

    Raises:
        ValueError: when the model's output does not validate. Nothing is merged.
    """
    raise NotImplementedError


def merge_responses(review_id: str, answers: list[ParsedAnswer], source: str) -> None:
    """Merge parsed answers into ``qa_responses``, keyed by question id.

    A later answer supersedes an earlier one for the same question and both are retained in
    the ledger, because the superseded answer is part of the audit record.
    """
    raise NotImplementedError


def coverage(review_id: str) -> float:
    """Return the proportion of planned questions answered above the confidence threshold.

    Low-confidence answers do not count toward coverage. The fleet does not score a vendor on
    coverage it does not have.
    """
    raise NotImplementedError
