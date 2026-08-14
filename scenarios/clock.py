"""Simulated time for the demo, honestly labelled.

A vendor security review takes weeks. A demo takes four minutes. The compression is real
events on an accelerated clock, not faked timestamps: every timestamp written during a demo
run comes from the injected clock, and every scheduled action — reply delivery, chase timers,
follow-up windows — is scheduled against it.

The user interface displays a "TIME-COMPRESSED DEMO · 1s = 4min" badge whenever the factor is
not 1. Stating it on screen is what protects the demo from the one question that could
otherwise sink it.

Failure semantics: a factor below 1 raises rather than running time backwards. Nothing outside
a demo run may construct a compressed clock — production code takes the clock as a dependency
and gets a real one, which is what keeps a compressed timestamp out of the audit ledger.
"""

from __future__ import annotations

from datetime import datetime


class DemoClock:
    """A clock that runs faster than wall time by a fixed factor.

    Args:
        factor: simulated seconds per real second. 240 means one real second is four simulated
            minutes. A factor of 1 is real time and disables the badge.
        origin: the simulated time the run starts at.

    Raises:
        ValueError: when ``factor`` is less than 1.
    """

    def __init__(self, factor: int, origin: datetime | None = None) -> None:
        raise NotImplementedError

    def now(self) -> datetime:
        """Return the current simulated time."""
        raise NotImplementedError

    @property
    def compressed(self) -> bool:
        """Whether the badge should be displayed."""
        raise NotImplementedError

    def badge_text(self) -> str:
        """Return the on-screen label, for example ``TIME-COMPRESSED DEMO · 1s = 4min``."""
        raise NotImplementedError


class RealClock:
    """Wall time. The default everywhere outside a demo run."""

    def now(self) -> datetime:
        """Return the current time in UTC."""
        raise NotImplementedError

    @property
    def compressed(self) -> bool:
        return False
