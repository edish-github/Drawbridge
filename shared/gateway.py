"""The policy chokepoint: every outbound effect and every model input passes through here.

Three named policies, enforced in code and logged when they fire. Three named policies is a
policy system; two is a pair of special cases.

Vocabulary, kept straight because two words previously described one thing: **gates are
what a person does** (G1 risk acceptance, G2 first outbound contact); **policies are what
this module enforces** (P1, P2, P3). G2 is the human act; P1 is the machine's inability to
skip it.

- **P1** — no outbound email to a new contact without a valid, single-use, scoped approval
  token. Verification only: this process holds the public key and has no signing capability
  at any point in its life, so it can recognise a human decision but cannot manufacture one.
- **P2** — no external content reaches a model without a verified, verdict-bearing
  clean-stamp. Verdict-aware rather than presence-aware: sanitised content is admissible to
  the Evidence agent and inadmissible to the memo-writing deep-model call, because a
  sanitised document is by definition one that tried something.
- **P3** — outbound fetch is restricted to an allowlist of feed domains. An agent with
  unbounded egress is an exfiltration channel and an SSRF surface.

If a module imports an SDK client directly for an external effect, that is a bug: route it
through here. This is what makes the security claim true rather than decorative.

Failure semantics: every policy decision raises ``PolicyViolation`` and writes a structured
log line naming the policy, the template and the filter that fired. Nothing is degraded to a
warning and no call proceeds past a failed check. A tool that is not in ``TOOL_REGISTRY``
raises ``UnknownTool`` rather than being dispatched dynamically.
"""

from __future__ import annotations

from typing import Any

MODEL_INPUT_TOOLS: frozenset[str] = frozenset()
"""Tool names whose arguments reach a generative model. P2 applies to every one of them.
Populated as tools are registered; an empty set means no tool may carry external content.
"""

FEED_ALLOWLIST: frozenset[str] = frozenset()
"""Hosts the Watchdog may fetch from under P3. Everything else is blocked and logged."""

TOOL_REGISTRY: dict[str, Any] = {}
"""Name to callable. The only dispatch table in the fleet."""


class PolicyViolation(Exception):
    """A gateway policy refused the call. Never caught to retry the same call unchanged."""


class UnknownTool(Exception):
    """A tool name that is not registered. A bug, not a runtime condition."""


class CostCeilingExceeded(Exception):
    """The review's accumulated spend passed the configured ceiling."""


def call_tool(tool_name: str, ctx, **kwargs) -> Any:
    """The single funnel for all agent side effects.

    Applies P1, P2 and P3 in that order, then rate and spend guards, then dispatches inside
    a span that records the tool name and result size.

    Raises:
        PolicyViolation: when P1 finds no valid approval token for the target address, when
            P2 finds no stamp or an inadmissible verdict for the destination tool, or when
            P3 finds the host outside the feed allowlist. The caller parks the review; it
            does not retry.
        UnknownTool: when ``tool_name`` is not in ``TOOL_REGISTRY``.
        CostCeilingExceeded: when the review's accumulated spend passed the ceiling. The
            review parks in ``NEEDS_HUMAN`` and further model calls stop.
    """
    raise NotImplementedError


def verify_approval_token(review_id: str, target: str, token: str | None = None) -> bool:
    """Verify a human approval token against the public key. Verification only.

    Checks the signature, that the ``jti`` has not been seen before (single use), that the
    scope matches this review and this target, and that the token has not expired.

    Returns:
        ``False`` for an absent, expired, replayed, mis-scoped or unverifiable token. It
        never raises on a bad token — a bad token is a policy outcome, not an error — so
        the caller's P1 block is the single place the decision is logged.
    """
    raise NotImplementedError


def verify_stamp(stamp: str | None):
    """Verify a clean-stamp signature and return the claim it carries.

    The stamp is a signed claim carrying the reference, review id, Model Armor template id
    and version, the per-filter verdicts, and whether the content was sanitised.

    Returns:
        The parsed stamp, or ``None`` when the stamp is absent, malformed or fails
        signature verification. ``None`` is a P2 block, not an exception.
    """
    raise NotImplementedError


def admissible(stamp, tool_name: str) -> bool:
    """Decide whether content bearing ``stamp`` may reach ``tool_name``.

    Sanitised content is admissible to extraction and cross-examination and inadmissible to
    the memo call. Content whose critical filters did not all execute is admissible
    nowhere: a detector that never ran is not a detector that found nothing.
    """
    raise NotImplementedError


def host_of(url: str) -> str:
    """Return the lowercased host of ``url`` for the P3 allowlist check.

    Raises:
        ValueError: on a URL with no host, an unsupported scheme, or a userinfo section
            that could disguise the host. Parsing failures are blocks, never bypasses.
    """
    raise NotImplementedError


def enforce_limits(ctx) -> None:
    """Apply rate and spend guards before dispatch.

    Raises:
        CostCeilingExceeded: parking the review rather than warning about it. A budget that
            is observed rather than enforced is not a control.
    """
    raise NotImplementedError


def log_policy_block(policy: str, ctx, **attrs) -> None:
    """Write the structured log line and dashboard event for a policy decision.

    The line names the policy, the template and the filter that fired::

        P2 REJECTED · drawbridge-untrusted v3 · pi_and_jailbreak MATCH_FOUND · ref=...

    ``blocked`` reads as a mock; the line above reads as a product. Never raises: a failure
    to log must not swallow the block it was describing.
    """
    raise NotImplementedError
