"""Configuration, loaded and validated once at import time.

Every knob in ``.env.example`` is declared here with its type. Validation runs on import
and raises immediately if a key is missing or malformed, because a service that boots
half-configured produces the worst class of demo failure — one that only appears under
load, on camera.

Two settings are load-bearing rather than tunable. ``armor_fail_closed`` must never be
``False`` outside a local test: Model Armor is a mandatory control, and mandatory controls
fail closed while optional ones degrade. ``cost_ceiling_per_review_usd`` is enforced, not
observed: exceeding it parks the review.

Failure semantics: import raises ``pydantic.ValidationError`` listing every missing or
invalid key at once, not the first one found. Nothing in this module reads a secret value —
Secret Manager resource names are configuration, the secrets behind them are not.
"""

from __future__ import annotations

from pydantic import BaseModel


class Settings(BaseModel):
    """Validated configuration for every Drawbridge process."""

    project_id: str
    region: str

    model_fast: str
    model_deep: str
    model_embed: str
    model_local: str

    bucket_quarantine: str
    bucket_clean: str
    bucket_binders: str

    approval_private_key_secret: str
    approval_public_key: str

    model_armor_template_untrusted: str
    model_armor_template_output: str
    armor_fail_closed: bool = True

    vector_top_k: int = 6
    chunk_tokens: int = 400

    plan_version: int = 1
    followup_cap: int = 2
    watchdog_confidence_min: float = 0.75
    cost_ceiling_per_review_usd: float = 0.50

    demo_time_compression: int = 240


def load_settings() -> Settings:
    """Read the environment (and ``.env`` when present) into a validated ``Settings``.

    Raises:
        pydantic.ValidationError: with every missing or malformed key reported together,
            so one restart surfaces the whole problem rather than one key per attempt.
    """
    raise NotImplementedError


def settings() -> Settings:
    """Return the process-wide settings, loading them on first call.

    Raises:
        pydantic.ValidationError: propagated from ``load_settings`` on the first call.
            Later calls return the cached object; configuration is never reloaded inside a
            running process, so a mid-flight environment change cannot alter behaviour.
    """
    raise NotImplementedError
