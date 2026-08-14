"""Targeted follow-ups for answers that arrive but are useless.

Chasing handles *missing* answers. It does nothing about *present but vague* ones, and "we
follow best practices" is the exact pain that justifies this project existing. The confidence
score the parser already computes is the trigger: below threshold, one targeted follow-up
quotes the vendor's own answer back, names the specific evidence required, and asks once.

    "Your answer to Q14 states that encryption follows industry best practice. The review
    requires the specific standards used for data at rest and in transit, and your
    key-management policy as an attachment. Could you provide those two items?"

Capped at ``FOLLOWUP_CAP`` per question so politeness cannot become an infinite loop. This is
the difference between an agent that collects and an agent that interrogates.

Failure semantics: the cap is checked before the model call, not after, so a retry storm
cannot spend tokens generating follow-ups that will never be sent. The send goes through the
gateway under the same P1 thread delegation as any other outbound message, and carries its
own idempotency key (``followup:q14:v1``). A generation failure leaves the answer marked
``needs_human`` and sends nothing.
"""

from __future__ import annotations

from agents.questionnaire.parser import ParsedAnswer


def followups_sent(review_id: str, question_id: str) -> int:
    """Return how many follow-ups have already gone out for this question."""
    raise NotImplementedError


def maybe_followup(ctx, review_id: str, question_id: str, answer: ParsedAnswer) -> str | None:
    """Send one targeted follow-up if the answer is weak and the cap allows it.

    Returns:
        The message id, or ``None`` when the answer is above threshold or the cap is reached.
    """
    raise NotImplementedError
