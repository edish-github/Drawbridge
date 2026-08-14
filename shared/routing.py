"""Model routing and per-review cost accounting. Fast-first economics.

The fast model is the default for parsing, extraction, classification and chasing. The deep
model is spent in exactly two places — cross-examination and the risk memo — and saying
"exactly two" is only true because nothing else routes to it.

**There is deliberately no scoring entry.** Scoring makes no model call at all: the Evidence
agent assigns severity where it is already reading the passage, and the score is arithmetic
over those severities. A routing entry here would re-open the one hole in the strongest
architectural claim in the project — the model judges severity, the code computes the number,
so no agent holds the pen on its own metric. It must not come back.

Per-review cost accumulates on the review record, which is what produces the per-review
figure. The ceiling is enforced rather than observed: exceeding it parks the review and
stops further model calls.

Failure semantics: a task name with no routing entry raises rather than falling back to a
default model — an unrouted task is a bug, and a silent fallback would make the cost story
untrue. A model call failure propagates after the span records it; the caller decides
between a retry and parking the review. Exceeding the cost ceiling raises
``CostCeilingExceeded`` from inside cost accumulation, so the effect stops at the call that
crossed the line rather than at the next check.
"""

from __future__ import annotations

from pydantic import BaseModel

from shared.gateway import CostCeilingExceeded  # noqa: F401  (re-exported for callers)

ROUTING: dict[str, str] = {
    "parse_reply": "MODEL_FAST",
    "extract_controls": "MODEL_FAST",
    "chase_message": "MODEL_FAST",
    "followup_question": "MODEL_FAST",
    "classify_data_scope": "MODEL_FAST",
    "relevance": "MODEL_FAST",
    "embed_evidence": "MODEL_EMBED",
    "cross_examine": "MODEL_DEEP",
    "risk_memo": "MODEL_DEEP",
    "pii_scrub": "MODEL_LOCAL",
}
"""Task name to the settings key naming the model that serves it. Values are resolved
through ``shared.config`` at call time so the mapping stays declarative and testable.
"""


class UnroutedTask(Exception):
    """A task name with no routing entry. A bug, never a fallback."""


class ModelResult(BaseModel):
    text: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float
    parsed: dict | None = None


def generate(task: str, prompt: str, ctx) -> ModelResult:
    """Run ``task``'s prompt against its routed model inside a cost-recording span.

    Raises:
        UnroutedTask: when ``task`` is not in ``ROUTING``.
        CostCeilingExceeded: when this call's cost pushes the review past its ceiling. The
            review parks in ``NEEDS_HUMAN`` with a cost card and further model calls stop.
        GoogleAPICallError: propagated after the span records the failure.
    """
    raise NotImplementedError


def embed(text: str, ctx) -> list[float]:
    """Return the embedding for ``text`` using the configured embedding model.

    Raises:
        Nothing on failure. Embedding is an optional control: the caller logs a
        degraded-mode warning and cross-examination falls back to whole-document context.
        Retrieval is never on the critical path.
    """
    raise NotImplementedError


def estimate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """Return the USD cost of one call. Pure arithmetic over the published rate card.

    TODO(verify): the current per-token rates for the fast, deep and embedding models. The
    published per-review figure is only as honest as this table.
    """
    raise NotImplementedError


def accumulate_review_cost(review_id: str, cost: float) -> float:
    """Add ``cost`` to the review's running total and return the new total.

    Raises:
        CostCeilingExceeded: when the total passes the configured ceiling, after parking the
            review. A budget that is observed rather than enforced is not a control.
    """
    raise NotImplementedError
