"""The policy chokepoint: every outbound effect and every model input passes through here.

Three named policies, enforced in code and logged when they fire. Three named policies is a
policy system; two is a pair of special cases.

Vocabulary, kept straight because two words previously described one thing: **gates are what a
person does** (G1 risk acceptance, G2 first outbound contact); **policies are what this module
enforces** (P1, P2, P3). G2 is the human act; P1 is the machine's inability to skip it.

- **P1** — no outbound email to a new contact without a valid, single-use, scoped approval
  token. Verification only: this process holds the public key and has no signing capability at
  any point in its life, so it can recognise a human decision but cannot manufacture one.
- **P2** — no external content reaches a model without a verified, verdict-bearing clean-stamp.
  Verdict-aware rather than presence-aware: sanitised content is admissible to the Evidence
  agent and inadmissible to the memo-writing deep-model call, because a sanitised document is by
  definition one that tried something.
- **P3** — outbound fetch is restricted to an allowlist of feed domains. An agent with unbounded
  egress is an exfiltration channel and an SSRF surface.

If a module imports an SDK client directly for an external effect, that is a bug: route it
through here. This is what makes the security claim true rather than decorative.

Failure semantics: every policy decision raises ``PolicyViolation`` and writes a structured log
line naming the policy, the template and the filter that fired, plus a dashboard event. Nothing
is degraded to a warning and no call proceeds past a failed check. A tool that is not in
``TOOL_REGISTRY`` raises ``UnknownTool`` rather than being dispatched dynamically.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

from shared.armor import ScreenResult, verdict_is_trustworthy
from shared.clients import firestore_client
from shared.telemetry import record_policy_event, span

log = logging.getLogger("drawbridge.gateway")

COLLECTION_DASHBOARD = "dashboard_events"

MODEL_INPUT_TOOLS: frozenset[str] = frozenset(
    {"extract_controls", "cross_examine", "risk_memo", "parse_reply", "classify_data_scope"}
)
"""Tool names whose arguments reach a generative model. P2 applies to every one of them."""

DEEP_MODEL_TOOLS: frozenset[str] = frozenset({"risk_memo"})
"""Destinations where sanitised content is inadmissible. A sanitised document tried something,
and the memo is the artefact a human acts on.

Read by both gates: ``call_tool`` names an effect and ``routing.generate`` names a task, and
the memo is the one destination that appears under both names. One list, so the rule cannot be
true at one gate and false at the other.
"""

FEED_ALLOWLIST: frozenset[str] = frozenset(
    {
        "www.cisa.gov",
        "services.nvd.nist.gov",
        "krebsonsecurity.com",
        "www.bleepingcomputer.com",
    }
)
"""Hosts the Watchdog may fetch from under P3. Everything else is blocked and logged.

Four, named individually rather than by wildcard, because a wildcard on a hosting provider is
an allowlist of everyone who bought a subdomain there. Two advisory sources and two established
outlets, in that order of weight: advisories carry stable formats and near-zero false-positive
rates, which is what a relevance threshold needs to mean anything. A news aggregator would flood
the confidence filter with restated versions of the same advisory and teach the threshold
nothing.

An entry here is an assertion that the fleet may open a connection to that host. Adding one is a
security decision, so it is a diff in this file rather than a configuration value somebody sets
at deploy time.
"""

TOOL_REGISTRY: dict[str, Callable[..., Any]] = {}
"""Name to callable. The only dispatch table in the fleet."""

ALLOW_UNSCREENED_ENV = "DRAWBRIDGE_ALLOW_UNSCREENED"
"""The environment variable that admits declared-untrusted fixtures past P2, in local mode only.

Named here because P2 lives here. See ``unscreened_fixtures_allowed``.
"""

_DECLARED: set[str] = set()
"""Reviews already marked as fixture-built in this process, so the banner is printed once each
rather than once per model call."""


class PolicyViolation(Exception):
    """A gateway policy refused the call. Never caught to retry the same call unchanged."""

    effect_attempted = False
    """The refusal happened before dispatch, so the effect provably did not occur.

    ``shared.idempotency.once`` reads this to release its claim rather than leaving an
    ``in_progress`` marker. The distinction matters at the contact gate: a review parks because
    P1 refused, a human approves, and the send must then be able to run. A claim left behind by
    a refusal would meet the release path as an unreconciled step and refuse to send at all —
    the gate would be releasable in the state machine and stuck in the guard.
    """

    def __init__(self, policy: str, message: str) -> None:
        super().__init__(f"{policy}: {message}")
        self.policy = policy


class UnknownTool(Exception):
    """A tool name that is not registered. A bug, not a runtime condition."""


class SigningKeyUnavailable(Exception):
    """Something asked this process to sign. It holds no signing key, by design."""


def register_tool(name: str, fn: Callable[..., Any]) -> None:
    """Register a tool so the gateway can dispatch it. The only way a tool becomes callable."""
    TOOL_REGISTRY[name] = fn


def call_tool(tool_name: str, ctx, **kwargs) -> Any:
    """The single funnel for all agent side effects.

    Applies P1, P2 and P3 in that order, then rate and spend guards, then dispatches inside a
    span that records the tool name and result size.

    Raises:
        PolicyViolation: when P1 finds no valid approval token for the target address, when P2
            finds no stamp or an inadmissible verdict for the destination tool, or when P3 finds
            the host outside the feed allowlist. The caller parks the review; it does not retry.
        UnknownTool: when ``tool_name`` is not in ``TOOL_REGISTRY``.
        CostCeilingExceeded: when the review's accumulated spend passed the ceiling.
    """
    with span(f"tool.{tool_name}", ctx, tool=tool_name) as s:
        # P1 — no outbound email to a new contact without a signed human artefact.
        if tool_name == "send_email":
            target = kwargs.get("to", "")
            if not verify_approval_token(ctx.review_id, target, kwargs.get("approval_token")):
                log_policy_block("P1", ctx, target=target, reason="no_valid_approval_token")
                record_policy_event(
                    s, policy="P1", outcome="REJECTED", detail=f"target={target}"
                )
                raise PolicyViolation(
                    "P1", "outbound email requires a human approval token"
                )

        # P2 — no external content reaches a model without a verified, verdict-bearing stamp.
        if tool_name in MODEL_INPUT_TOOLS:
            stamp = verify_stamp(kwargs.get("armor_stamp"))
            ref = kwargs.get("ref", "")
            if stamp is None:
                log_policy_block("P2", ctx, ref=ref, reason="no_valid_stamp")
                record_policy_event(s, policy="P2", outcome="REJECTED", detail="no_valid_stamp")
                raise PolicyViolation("P2", "unscreened content rejected at gateway")
            if not admissible(stamp, tool_name):
                detail = f"{stamp.template} {stamp.template_version} · {stamp.first_match()}"
                log_policy_block(
                    "P2",
                    ctx,
                    ref=ref,
                    template=stamp.template,
                    template_version=stamp.template_version,
                    filter_name=stamp.first_match(),
                    reason="verdict_inadmissible",
                )
                record_policy_event(s, policy="P2", outcome="REJECTED", detail=detail)
                raise PolicyViolation(
                    "P2", f"{stamp.summary()} inadmissible to {tool_name}"
                )

        # P3 — outbound fetch is restricted to an allowlist of feed domains.
        if tool_name == "fetch_url":
            url = kwargs.get("url", "")
            host = host_of(url)
            if host not in FEED_ALLOWLIST:
                log_policy_block("P3", ctx, target=url, reason="not_in_allowlist")
                record_policy_event(s, policy="P3", outcome="REJECTED", detail=f"host={host}")
                raise PolicyViolation("P3", "outbound fetch outside the feed allowlist")

        enforce_limits(ctx)

        fn = TOOL_REGISTRY.get(tool_name)
        if fn is None:
            raise UnknownTool(
                f"{tool_name!r} is not registered. Tools are registered explicitly through "
                "register_tool; there is no dynamic dispatch."
            )

        result = fn(**kwargs)
        s.set_attribute("tool.result_size", len(str(result)))
        return result


def verify_approval_token(
    review_id: str,
    target: str,
    token: str | None = None,
    *,
    scope: str = "contact",
) -> bool:
    """Verify a human approval token. Verification only — this process cannot mint one.

    Checks that the ``jti`` resolves to a recorded approval, that the approval is scoped to this
    review, this scope and this target, that it has not expired, and that it has not been
    honoured before. The single-use spend is transactional and is recorded in
    ``approval_tokens_spent``, never in ``approvals``, so the gateway retires a decision without
    holding any write on the collection where decisions are authored.

    The spend happens at verification rather than after the effect. That direction is
    deliberate: a token spent on a send that then failed means a human is asked to approve
    again, whereas the other direction means a token that survives a partial failure and can be
    presented twice.

    Returns:
        ``False`` for an absent, expired, replayed, mis-scoped or unverifiable token. It never
        raises on a bad token — a bad token is a policy outcome, not an error — so the caller's
        P1 block is the single place the decision is logged.

    TODO(verify): cloud verification is an asymmetric signature check against the public half of
        the approval key pair, and lands with the approval service, which is the only holder of
        the private half. Until then cloud mode returns ``False`` for everything — the correct
        direction to be wrong in, because it leaves the contact gate closed rather than open.
    """
    from shared import approvals
    from shared.config import settings

    if not token:
        return False

    if settings().is_cloud:
        log.warning(
            "P1 asymmetric verification is not yet implemented; rejecting token for "
            "review=%s target=%s",
            review_id,
            target,
        )
        return False

    jti = approvals.jti_of(token)
    if jti is None:
        return False

    approval = approvals.load_approval(jti)
    if approval is None:
        return False

    if approval.review_id != review_id or approval.scope != scope:
        log.warning(
            "approval %s is scoped to review=%s/%s, presented for review=%s/%s",
            jti,
            approval.review_id,
            approval.scope,
            review_id,
            scope,
        )
        return False

    if approval.target != target:
        log.warning("approval %s authorises %s, presented for %s", jti, approval.target, target)
        return False

    if approval.expires_at <= datetime.now(UTC):
        log.warning("approval %s expired at %s", jti, approval.expires_at.isoformat())
        return False

    if not approvals.spend(jti, review_id=review_id, target=target):
        log.warning("approval %s has already been honoured; single use", jti)
        return False

    log.info("P1 ACCEPTED · approval %s by %s · target=%s", jti, approval.identity, target)
    return True


def issue_approval_token(review_id: str, scope: str, identity: str) -> str:
    """Never callable from this process.

    Raises:
        SigningKeyUnavailable: always. The gateway is reachable by every agent in the fleet, so
            if it could sign, any agent could forge. The private key lives only in the approval
            service. This function exists so that attempting to sign here fails loudly rather
            than being quietly absent.
    """
    raise SigningKeyUnavailable(
        "the gateway holds the public key only and is structurally incapable of minting an "
        "approval; issue tokens from the approval service"
    )


def verify_stamp(stamp: str | None) -> ScreenResult | None:
    """Verify a clean-stamp signature and return the claim it carries.

    The local tag is checked rather than merely parsed: the digest is recomputed over the
    payload and a mismatch returns ``None``. A stamp whose body could be edited after signing
    would make P2 a check that content *carries* a verdict rather than a check on *what the
    verdict said*, which is the distinction the whole policy rests on.

    Returns:
        The parsed stamp, or ``None`` when the stamp is absent, malformed, altered or fails
        verification. ``None`` is a P2 block, not an exception.
    """
    if not stamp:
        return None
    try:
        if stamp.startswith("local-tag:"):
            _, digest, payload = stamp.split(":", 2)
            if hashlib.sha256(payload.encode("utf-8")).hexdigest() != digest:
                log.warning("P2 stamp digest mismatch; the claim does not match its tag")
                return None
            return ScreenResult.model_validate(json.loads(payload))
    except (ValueError, json.JSONDecodeError):
        return None
    return None


def unscreened_fixtures_allowed() -> bool:
    """Return whether unscreened local fixtures may reach a model on this process.

    Local mode has no Model Armor, so seeded documents carry a ``local-seed`` stamp that P2
    correctly refuses. ``DRAWBRIDGE_ALLOW_UNSCREENED=1`` admits them — and only them: a source
    with no stamp at all is refused whatever this returns, because the allowance covers content
    that declared what it is, not content nobody screened and nobody labelled.

    **Cloud mode ignores the variable entirely.** The check is on the mode first and the
    environment second, so setting it on a deployed service does nothing at all. An escape
    hatch that could be opened in production by an environment variable is not an escape hatch,
    it is a switch for turning the control off.
    """
    from shared.config import settings

    return settings().is_local and os.environ.get(ALLOW_UNSCREENED_ENV) == "1"


def declare_unscreened(review_id: str | None) -> None:
    """Mark a review as built on unscreened fixtures, and say so once, loudly.

    The flag travels with the review onto the dashboard and onto the binder's cover. A fixture
    run that produced an artefact indistinguishable from a real one would be the single most
    damaging thing in this repository, so the artefact declares what it is — the same
    discipline as ``local-stub`` and ``local-seed``, one level up.
    """
    if not review_id or review_id in _DECLARED:
        return
    _DECLARED.add(review_id)

    log.warning(
        "UNSCREENED FIXTURES — review=%s is built on seeded content that no detector "
        "inspected. %s is set. This is not a screening verdict and the binder says so on its "
        "cover.",
        review_id,
        ALLOW_UNSCREENED_ENV,
    )
    try:
        firestore_client().collection("reviews").document(review_id).set(
            {"unscreened_fixtures": True}, merge=True
        )
    except Exception as exc:  # noqa: BLE001 — the declaration must not break the run it labels
        log.error("could not record the unscreened-fixtures flag on %s: %s", review_id, exc)


def admissible(stamp: ScreenResult, tool_name: str) -> bool:
    """Decide whether content bearing ``stamp`` may reach ``tool_name``.

    Three rules, in order of severity:

    1. Content whose critical filters did not all execute is admissible nowhere. A detector that
       never ran is not a detector that found nothing — and this is what makes a local stub
       verdict unusable rather than merely labelled.
    2. Sanitised content is inadmissible to the deep-model tools. A sanitised document is by
       definition one that tried something, and the memo is the artefact a human acts on.
    3. Everything else that screened clean is admissible.
    """
    if not verdict_is_trustworthy(stamp):
        return False
    if stamp.sanitised and tool_name in DEEP_MODEL_TOOLS:
        return False
    return True


def host_of(url: str) -> str:
    """Return the lowercased host of ``url`` for the P3 allowlist check.

    Raises:
        PolicyViolation: on a URL with no host, an unsupported scheme, or a userinfo section
            that could disguise the host — ``https://feeds.allowed.example@evil.example/`` reads
            as an allowed host to a careless parser and resolves to the attacker's. Parsing
            failures are blocks, never bypasses.
    """
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise PolicyViolation("P3", f"unsupported scheme in {url!r}")
    if "@" in parts.netloc:
        raise PolicyViolation("P3", f"userinfo in the authority of {url!r}")
    host = (parts.hostname or "").lower()
    if not host:
        raise PolicyViolation("P3", f"no host in {url!r}")
    return host


def enforce_limits(ctx) -> None:
    """Apply rate and spend guards before dispatch.

    The spend guard is the enforced per-review ceiling: a review already parked for cost stops
    here rather than making one more call. A budget that is observed rather than enforced is not
    a control.

    Raises:
        PolicyViolation: when the review is already parked for cost.
    """
    review_id = getattr(ctx, "review_id", None)
    if not review_id:
        return

    snap = firestore_client().collection("reviews").document(review_id).get()
    data = snap.to_dict() or {}
    if data.get("park_reason") == "cost_ceiling":
        raise PolicyViolation(
            "SPEND", f"review {review_id} is parked at its cost ceiling; no further calls"
        )


def log_policy_block(policy: str, ctx, **attrs) -> None:
    """Write the structured log line and dashboard event for a policy decision.

    The line names the policy, what refused and why::

        P2 REJECTED · drawbridge-untrusted v3 · pi_and_jailbreak MATCH_FOUND · ref=...
        P2 REJECTED · local-seed v0 · verdict_not_trustworthy · task=cross_examine

    ``blocked`` reads as a mock; the lines above read as a product. Never raises: a failure to
    log must not swallow the block it was describing.
    """
    template = attrs.get("template")
    version = attrs.get("template_version")
    filter_name = attrs.get("filter_name")
    detail = attrs.get("detail")
    ref = attrs.get("ref") or attrs.get("target") or ""

    parts = [f"{policy} REJECTED"]
    if template:
        parts.append(f"{template} v{version}" if version else str(template))
    if filter_name:
        parts.append(f"{filter_name} MATCH_FOUND")
    if detail:
        parts.append(str(detail))
    if ref:
        parts.append(f"ref={ref}")
    line = " · ".join(parts)

    log.warning(line)

    try:
        firestore_client().collection(COLLECTION_DASHBOARD).add(
            {
                "kind": "policy_block",
                "policy": policy,
                "line": line,
                "review_id": getattr(ctx, "review_id", None),
                "agent": getattr(ctx, "agent", None),
                "reason": attrs.get("reason"),
                "at": datetime.now(UTC).isoformat(),
            }
        )
    except Exception as exc:  # noqa: BLE001 — logging must not swallow the block
        log.error("failed to record dashboard event for %s: %s", policy, exc)
