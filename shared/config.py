"""Configuration, loaded and validated once at import time.

Every knob in ``.env.example`` is declared here with its type. Validation runs on the first
access and raises immediately if a key is missing or malformed, because a service that boots
half-configured produces the worst class of demo failure — one that only appears under load,
on camera.

Two settings are load-bearing rather than tunable. ``armor_fail_closed`` must never be
``False`` outside a local test: Model Armor is a mandatory control, and mandatory controls
fail closed while optional ones degrade. ``cost_ceiling_per_review_usd`` is enforced, not
observed: exceeding it parks the review.

**Agent modules resolve their model id through here at import.** That means importing an agent
requires a configured environment — copy ``.env.example`` to ``.env`` first. This is the
intended coupling rather than an accident: an agent module that imports cleanly with no
configuration is how an unresolved placeholder reaches a deployed model call.

Failure semantics: loading raises ``pydantic.ValidationError`` listing every missing or invalid
key at once, not the first one found, so one restart surfaces the whole problem. Nothing here
reads a secret value — Secret Manager resource names are configuration, the secrets behind them
are not.
"""

from __future__ import annotations

import os

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field


class Settings(BaseModel):
    """Validated configuration for every Drawbridge process.

    Field names are the lowercase form of the environment variable that supplies them:
    ``MODEL_FAST`` populates ``model_fast``.
    """

    # Several fields begin with "model_", which Pydantic reserves by default. They are named
    # after their environment variables and renaming them would break that correspondence.
    model_config = ConfigDict(protected_namespaces=())

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

    vector_top_k: int = Field(default=6, gt=0)
    chunk_tokens: int = Field(default=400, gt=0)

    plan_version: int = Field(default=1, gt=0)
    followup_cap: int = Field(default=2, ge=0)
    watchdog_confidence_min: float = Field(default=0.75, ge=0.0, le=1.0)
    cost_ceiling_per_review_usd: float = Field(default=0.50, gt=0.0)

    demo_time_compression: int = Field(default=240, ge=1)


_CACHED: Settings | None = None


def load_settings() -> Settings:
    """Read the environment (and ``.env`` when present) into a validated ``Settings``.

    Raises:
        pydantic.ValidationError: with every missing or malformed key reported together,
            so one restart surfaces the whole problem rather than one key per attempt.
    """
    load_dotenv(override=False)

    raw = {
        name: os.environ[name.upper()]
        for name in Settings.model_fields
        if os.environ.get(name.upper())
    }
    return Settings.model_validate(raw)


def settings() -> Settings:
    """Return the process-wide settings, loading them on first call.

    Raises:
        pydantic.ValidationError: propagated from ``load_settings`` on the first call.
            Later calls return the cached object; configuration is never reloaded inside a
            running process, so a mid-flight environment change cannot alter behaviour.
    """
    global _CACHED
    if _CACHED is None:
        _CACHED = load_settings()
    return _CACHED
