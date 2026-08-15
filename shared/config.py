"""Configuration, loaded and validated once, with one switch that selects every backend.

Every knob in ``.env.example`` is declared here with its type. Validation runs on the first
access and raises immediately if a key is missing or malformed, because a service that boots
half-configured produces the worst class of demo failure — one that only appears under load,
on camera.

**``RUNTIME_MODE`` is the single switch.** Nothing else in the kernel branches on environment:
each module asks ``settings().mode`` once and picks a backend. Scattered conditionals are how a
local-only bug survives into a cloud deployment.

===========  ==================================  ==================================
Component    ``local``                           ``cloud``
===========  ==================================  ==================================
Models       Gemini API key                      Vertex AI
Firestore    emulator, ``FIRESTORE_EMULATOR_HOST``   real
Pub/Sub      emulator, ``PUBSUB_EMULATOR_HOST``      real
Traces       OpenTelemetry console exporter      Cloud Trace
Model Armor  labelled local stub                 real service
Memory       Firestore dossier collection        Memory Bank
===========  ==================================  ==================================

Both modes satisfy the required-technology clause: Gemini 3.5 or newer reached through either
the Gemini API or Vertex AI. Local mode is a development posture, not a substitution.

Validation is mode-aware and stays fail-loud in both directions. In ``cloud`` a missing
``PROJECT_ID`` raises; in ``local`` a missing ``GEMINI_API_KEY`` raises. A key that only one
mode needs is optional in the other, and required in the mode that needs it — an optional-
everywhere key is how a service reaches production with no model credentials.

Two settings are load-bearing rather than tunable. ``armor_fail_closed`` must never be
``False`` outside a local test: Model Armor is a mandatory control, and mandatory controls fail
closed while optional ones degrade. ``cost_ceiling_per_review_usd`` is enforced, not observed.

**Agent modules resolve their model id through here at import**, so importing an agent requires
a configured environment — copy ``.env.example`` to ``.env`` first. That coupling is intended:
a module that imports cleanly with no configuration is how an unresolved placeholder reaches a
deployed model call.

Failure semantics: loading raises ``pydantic.ValidationError`` listing every problem at once,
not the first one found, so one restart surfaces the whole picture. Nothing here reads a secret
value — Secret Manager resource names are configuration, the secrets behind them are not, and
``gemini_api_key`` is the one credential held in process because the Gemini API has no other
way to take it.
"""

from __future__ import annotations

import os
from enum import StrEnum

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, model_validator


class RuntimeMode(StrEnum):
    """Which set of backends the process talks to."""

    LOCAL = "local"
    CLOUD = "cloud"


class Settings(BaseModel):
    """Validated configuration for every Drawbridge process.

    Field names are the lowercase form of the environment variable that supplies them:
    ``MODEL_FAST`` populates ``model_fast``.
    """

    # Several fields begin with "model_", which Pydantic reserves by default. They are named
    # after their environment variables and renaming them would break that correspondence.
    model_config = ConfigDict(protected_namespaces=())

    mode: RuntimeMode = RuntimeMode.LOCAL

    # Required in cloud mode; unused in local mode, where the emulators need no project.
    project_id: str | None = None
    region: str = "us-central1"

    # Required in local mode. Never logged, never written to a span, never sent anywhere but
    # the Gemini API endpoint.
    gemini_api_key: str | None = None

    firestore_emulator_host: str | None = None
    pubsub_emulator_host: str | None = None

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

    @model_validator(mode="after")
    def _require_what_the_mode_needs(self) -> Settings:
        """Fail loudly for the keys this mode cannot run without.

        Raises:
            ValueError: naming the mode and the missing key, because "PROJECT_ID is required"
                without the mode that requires it sends the reader to the wrong file.
        """
        missing: list[str] = []

        if self.mode is RuntimeMode.CLOUD:
            if not self.project_id:
                missing.append("PROJECT_ID")
        else:
            if not self.gemini_api_key:
                missing.append("GEMINI_API_KEY")
            # Without these the Google client libraries fall through to the real API and give
            # no indication that they did. That is not a local-mode inconvenience: it is a
            # development run silently writing to production, which is the single worst
            # failure this mode switch could have. Required, not defaulted.
            if not self.firestore_emulator_host:
                missing.append("FIRESTORE_EMULATOR_HOST")
            if not self.pubsub_emulator_host:
                missing.append("PUBSUB_EMULATOR_HOST")

        if missing:
            raise ValueError(
                f"RUNTIME_MODE={self.mode.value} requires {', '.join(missing)}. "
                "See .env.example for the full key list."
            )
        return self

    @property
    def is_local(self) -> bool:
        return self.mode is RuntimeMode.LOCAL

    @property
    def is_cloud(self) -> bool:
        return self.mode is RuntimeMode.CLOUD

    def emulator_project(self) -> str:
        """Return the project id the emulators are addressed by.

        The emulators accept any non-empty project id and never authenticate it. A fixed local
        value keeps emulator data from colliding with a real project id if one is later set.
        """
        return self.project_id or "drawbridge-local"


_CACHED: Settings | None = None


def load_settings() -> Settings:
    """Read the environment (and ``.env`` when present) into a validated ``Settings``.

    Also exports the emulator host variables back into the process environment when local mode
    supplies them, because the Google client libraries read those variables directly rather
    than taking a parameter. Doing it here means one place decides, rather than every module
    remembering to set them before constructing a client.

    Raises:
        pydantic.ValidationError: with every missing or malformed key reported together, so one
            restart surfaces the whole problem rather than one key per attempt.
    """
    load_dotenv(override=False)

    raw = {
        name: os.environ[name.upper()]
        for name in Settings.model_fields
        if os.environ.get(name.upper())
    }
    resolved = Settings.model_validate(raw)

    if resolved.is_local:
        if resolved.firestore_emulator_host:
            os.environ["FIRESTORE_EMULATOR_HOST"] = resolved.firestore_emulator_host
        if resolved.pubsub_emulator_host:
            os.environ["PUBSUB_EMULATOR_HOST"] = resolved.pubsub_emulator_host
        # The client libraries refuse to start without a project even when pointed at an
        # emulator that ignores it.
        os.environ.setdefault("GOOGLE_CLOUD_PROJECT", resolved.emulator_project())

    return resolved


def settings() -> Settings:
    """Return the process-wide settings, loading them on first call.

    Raises:
        pydantic.ValidationError: propagated from ``load_settings`` on the first call. Later
            calls return the cached object; configuration is never reloaded inside a running
            process, so a mid-flight environment change cannot alter behaviour.
    """
    global _CACHED
    if _CACHED is None:
        _CACHED = load_settings()
    return _CACHED


def reset_settings_cache() -> None:
    """Drop the cached settings. For tests that exercise more than one mode in one process."""
    global _CACHED
    _CACHED = None
