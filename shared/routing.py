"""Model routing and per-review cost accounting. Fast-first economics, one code path.

The fast model is the default for parsing, extraction, classification and chasing. The deep
model is spent in exactly two places — cross-examination and the risk memo — and saying
"exactly two" is only true because nothing else routes to it.

**There is deliberately no scoring entry.** Scoring makes no model call at all: the Evidence
agent assigns severity where it is already reading the passage, and the score is arithmetic
over those severities. A routing entry here would re-open the one hole in the strongest
architectural claim in the project — the model judges severity, the code computes the number,
so no agent holds the pen on its own metric. It must not come back.

Both backends are reached through one client surface: ``google-genai`` takes either an API key
or a Vertex AI project, and ``models.generate_content`` is identical either way. Local mode is
therefore the same code path as cloud with a different credential, not a parallel
implementation that has to be kept in step.

Per-review cost accumulates on the review record. **The ceiling is enforced, not observed:**
crossing it parks the review in ``NEEDS_HUMAN`` and stops further model calls. A budget that is
observed rather than enforced is not a control, and more practically it is what stands between
the credits and a runaway retry loop at two in the morning.

Failure semantics: a task name with no routing entry raises rather than falling back to a
default model — an unrouted task is a bug, and a silent fallback would make the cost story
untrue. Transient capacity failures are retried here, below every caller, with a bound and a
backoff; past the bound the error is raised and the review parks. Cost is accumulated *after*
the call returns, because a call that failed still consumed
tokens if it produced usage metadata and consumed none if it did not. Exceeding the ceiling
raises from inside accumulation, so the effect stops at the call that crossed the line rather
than at the next check.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel

from shared.clients import firestore_client, genai_client
from shared.config import settings
from shared.telemetry import span

log = logging.getLogger("drawbridge.routing")

FAST = "MODEL_FAST"
DEEP = "MODEL_DEEP"
EMBED = "MODEL_EMBED"
LOCAL = "MODEL_LOCAL"

ROUTING: dict[str, str] = {
    "plan_review": FAST,
    "parse_reply": FAST,
    "extract_controls": FAST,
    "chase_message": FAST,
    "classify_data_scope": FAST,
    "followup_question": FAST,
    "relevance": FAST,
    "cross_examine": DEEP,
    "risk_memo": DEEP,
    "embed_evidence": EMBED,
}
"""Task name to the settings key naming the model that serves it.

No ``score_rubric``. Scoring is arithmetic; if an entry for it ever appears here, the claim
that no agent holds the pen on its own metric has quietly stopped being true.
"""

# USD per million tokens. Source: Google's published Gemini API pricing page, read 16 Aug 2026.
# TODO(verify): confirm these against the billing console once real spend exists — the
# sub-$0.50-per-review headline is only as honest as this table, and a rate that moved is a
# number said out loud on camera that is wrong.
RATES_USD_PER_MTOK: dict[str, dict[str, float]] = {
    FAST: {"input": 0.30, "output": 2.50},
    DEEP: {"input": 1.25, "output": 10.00},
    EMBED: {"input": 0.15, "output": 0.0},
    LOCAL: {"input": 0.0, "output": 0.0},  # self-hosted; compute cost is not per token
}

COLLECTION_REVIEWS = "reviews"

RETRY_ATTEMPTS = 4
"""Total attempts per call, including the first. Bounded, never open-ended.

Roughly a third of calls to the deep model returned a transient capacity error during
development, and the deep model is what cross-examination runs on — inside a single-take demo
segment. An unretried transient failure there is a review that parks on camera for a reason
that has nothing to do with the vendor. Past the bound the error is raised and the caller parks
the review: retrying forever turns a capacity problem into a spend problem.
"""

RETRY_BASE_DELAY_SECONDS = 2.0
"""First backoff interval. Doubles per attempt: 2s, 4s, 8s."""

_TRANSIENT_MARKERS = ("503", "UNAVAILABLE", "500", "INTERNAL", "deadline exceeded")
"""Substrings that identify a retryable failure.

Capacity and transport only. A quota error is deliberately absent: 429 means the answer will
not change for a while, and retrying it burns the ceiling rather than the backlog.
"""


class UnroutedTask(Exception):
    """A task name with no routing entry. A bug, never a fallback."""


class CostCeilingExceeded(Exception):
    """The review's accumulated spend passed the configured ceiling."""

    def __init__(self, review_id: str, total: float, ceiling: float) -> None:
        super().__init__(
            f"review {review_id} reached ${total:.4f} against a ${ceiling:.2f} ceiling"
        )
        self.review_id = review_id
        self.total = total
        self.ceiling = ceiling


class ModelResult(BaseModel):
    text: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float
    parsed: Any = None


def _is_transient(exc: Exception) -> bool:
    """Return whether an exception is a capacity or transport failure worth retrying."""
    text = f"{type(exc).__name__}: {exc}".lower()
    return any(marker.lower() in text for marker in _TRANSIENT_MARKERS)


def _with_retry(label: str, s, call: Callable[[], Any]) -> Any:
    """Run ``call``, retrying transient failures with exponential backoff.

    Every caller inherits this because it sits below ``generate`` and ``embed`` rather than
    beside them. The attempt count is recorded on the span whether or not a retry happened, so
    a rising retry rate is visible in the trace before it is visible as a stalled review.

    Raises:
        Exception: the last failure, unchanged, once the bound is reached or when the failure
            is not transient. The caller parks the review.
    """
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            result = call()
            s.set_attribute("model.attempts", attempt)
            if attempt > 1:
                log.info("%s succeeded on attempt %d of %d", label, attempt, RETRY_ATTEMPTS)
            return result
        except Exception as exc:
            if attempt == RETRY_ATTEMPTS or not _is_transient(exc):
                s.set_attribute("model.attempts", attempt)
                raise
            delay = RETRY_BASE_DELAY_SECONDS * (2 ** (attempt - 1))
            log.warning(
                "%s: transient %s on attempt %d of %d, retrying in %.0fs",
                label,
                type(exc).__name__,
                attempt,
                RETRY_ATTEMPTS,
                delay,
            )
            time.sleep(delay)


def model_for(task: str) -> tuple[str, str]:
    """Return the ``(settings_key, model_id)`` serving ``task``.

    Raises:
        UnroutedTask: when ``task`` is not in ``ROUTING``.
    """
    key = ROUTING.get(task)
    if key is None:
        raise UnroutedTask(
            f"{task!r} has no routing entry. Add one to ROUTING — there is deliberately no "
            "default model, because a silent fallback makes the cost figure untrue."
        )
    cfg = settings()
    return key, {
        FAST: cfg.model_fast,
        DEEP: cfg.model_deep,
        EMBED: cfg.model_embed,
        LOCAL: cfg.model_local,
    }[key]


def estimate_cost(rate_key: str, prompt_tokens: int, completion_tokens: int) -> float:
    """Return the USD cost of one call. Pure arithmetic over the rate table."""
    rates = RATES_USD_PER_MTOK[rate_key]
    return (prompt_tokens * rates["input"] + completion_tokens * rates["output"]) / 1_000_000


def generate(
    task: str,
    prompt: str,
    ctx,
    *,
    response_schema: Any = None,
    temperature: float = 0.0,
) -> ModelResult:
    """Run ``task``'s prompt against its routed model inside a cost-recording span.

    Args:
        task: a key in ``ROUTING``.
        prompt: the fully rendered prompt.
        ctx: carries ``review_id`` and ``agent`` for the span and the cost record.
        response_schema: a Pydantic model or ``list[Model]`` to enforce structured output.
        temperature: defaults to 0. Severity judgements feed an arithmetic score, so the same
            evidence must produce the same answer every run; sampling would put noise directly
            into the number.

    Raises:
        UnroutedTask: when ``task`` has no routing entry.
        CostCeilingExceeded: when this call pushed the review past its ceiling. The review is
            parked before the exception is raised.
        google.genai.errors.APIError: propagated after the span records the failure.
    """
    rate_key, model_id = model_for(task)

    with span(f"model.{task}", ctx, model=model_id, task=task) as s:
        config: dict[str, Any] = {"temperature": temperature}
        if response_schema is not None:
            config["response_mime_type"] = "application/json"
            config["response_schema"] = response_schema

        response = _with_retry(
            f"generate_content({task})",
            s,
            lambda: genai_client().models.generate_content(
                model=model_id, contents=prompt, config=config
            ),
        )

        usage = response.usage_metadata
        prompt_tokens = getattr(usage, "prompt_token_count", 0) or 0
        answer_tokens = getattr(usage, "candidates_token_count", 0) or 0
        # Thinking tokens are billed at the output rate and are not included in
        # candidates_token_count. On short prompts they dominate: a one-word answer measured
        # 8 in, 1 out and 105 thinking. Omitting them understates the per-review figure by an
        # order of magnitude, and that figure is said out loud on camera.
        thinking_tokens = getattr(usage, "thoughts_token_count", 0) or 0
        completion_tokens = answer_tokens + thinking_tokens

        cost = estimate_cost(rate_key, prompt_tokens, completion_tokens)

        s.set_attribute("tokens.prompt", prompt_tokens)
        s.set_attribute("tokens.answer", answer_tokens)
        s.set_attribute("tokens.thinking", thinking_tokens)
        s.set_attribute("tokens.completion", completion_tokens)
        s.set_attribute("cost_usd", cost)

        text = response.text or ""
        parsed = None
        if response_schema is not None and text:
            parsed = getattr(response, "parsed", None)
            if parsed is None:
                # The SDK populates `parsed` when it can; falling back to the raw JSON keeps a
                # schema-constrained call usable rather than silently returning nothing.
                parsed = json.loads(text)

        result = ModelResult(
            text=text,
            model=model_id,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd=cost,
            parsed=parsed,
        )

        review_id = getattr(ctx, "review_id", None)
        if review_id:
            accumulate_review_cost(review_id, cost)

        return result


def embed(text: str, ctx) -> list[float]:
    """Return the embedding for ``text`` using the configured embedding model.

    Raises:
        Nothing on failure. Embedding is an optional control: this logs a degraded-mode
        warning and returns an empty list, and cross-examination falls back to whole-document
        context. Retrieval is never on the critical path.
    """
    rate_key, model_id = model_for("embed_evidence")
    try:
        with span("model.embed_evidence", ctx, model=model_id) as s:
            response = _with_retry(
                "embed_content",
                s,
                lambda: genai_client().models.embed_content(model=model_id, contents=text),
            )
            values = list(response.embeddings[0].values)
            s.set_attribute("embedding.dimensions", len(values))

            # Embedding usage is billed on input characters rather than reported tokens on
            # every backend, so this is an estimate flagged as one rather than a silent zero.
            approx_tokens = max(1, len(text) // 4)
            cost = estimate_cost(rate_key, approx_tokens, 0)
            s.set_attribute("cost_usd", cost)
            review_id = getattr(ctx, "review_id", None)
            if review_id:
                accumulate_review_cost(review_id, cost)
            return values
    except Exception as exc:  # noqa: BLE001 — optional control, degrades rather than blocks
        log.warning(
            "degraded mode: embedding unavailable, falling back to whole-document context: %s",
            exc,
        )
        return []


def accumulate_review_cost(review_id: str, cost: float) -> float:
    """Add ``cost`` to the review's running total and return the new total.

    Raises:
        CostCeilingExceeded: when the total passes the configured ceiling, after parking the
            review. Retries and model calls stop at the call that crossed the line.
    """
    from google.cloud import firestore

    cfg = settings()
    ref = firestore_client().collection(COLLECTION_REVIEWS).document(review_id)
    ref.set({"cost_usd": firestore.Increment(cost)}, merge=True)

    snap = ref.get()
    total = float((snap.to_dict() or {}).get("cost_usd", 0.0))

    if total > cfg.cost_ceiling_per_review_usd:
        park(review_id, reason="cost_ceiling")
        raise CostCeilingExceeded(review_id, total, cfg.cost_ceiling_per_review_usd)
    return total


def park(review_id: str, *, reason: str) -> None:
    """Move a review to ``NEEDS_HUMAN`` with a stated reason and surface it on the dashboard.

    Lives here rather than in a service module because three kernel modules need it — the cost
    ceiling, the screening pipeline and output screening all park — and a review that is
    parked by one path and not another is the kind of inconsistency nobody finds until a demo.
    """
    from shared.domain import ReviewState

    firestore_client().collection(COLLECTION_REVIEWS).document(review_id).set(
        {
            "state": ReviewState.NEEDS_HUMAN.value,
            "park_reason": reason,
        },
        merge=True,
    )
    log.warning("PARKED review=%s reason=%s", review_id, reason)
