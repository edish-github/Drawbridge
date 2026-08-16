"""Simulated time for the demo, honestly labelled.

A vendor security review takes weeks. A demo takes four minutes. The compression is real events
on an accelerated clock, not faked timestamps: every scheduled action — chase timers, follow-up
windows, certificate-expiry sweeps — is computed against the clock this module returns, so a
compressed run accelerates the schedule rather than pretending time passed.

The user interface displays a ``TIME-COMPRESSED DEMO · 1s = 4min`` badge whenever the factor is
not 1. Stating it on screen is what protects the demo from the one question that could
otherwise sink it.

**Production takes the clock as a dependency and gets a real one.** ``now()`` returns wall time
unless a demo has explicitly installed a compressed clock, and only a demo run ever calls
``install``. That is what keeps a compressed timestamp out of the audit ledger: a review's
recorded times come from ``datetime.now`` at the point of writing, and this clock decides only
when something is *due*.

It lives in ``shared`` rather than in ``scenarios`` because the chaser and the Watchdog both
schedule against it, and an agent package importing the demo package to find out what time it
is would be the wrong dependency in the wrong direction.

Failure semantics: a factor below 1 raises rather than running time backwards.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime


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
        if factor < 1:
            raise ValueError(f"compression factor {factor} would run time backwards")
        self.factor = int(factor)
        self.origin = origin or datetime.now(UTC)
        self._started = time.monotonic()

    def now(self) -> datetime:
        """Return the current simulated time."""
        from datetime import timedelta

        elapsed = time.monotonic() - self._started
        return self.origin + timedelta(seconds=elapsed * self.factor)

    @property
    def compressed(self) -> bool:
        """Whether the badge should be displayed."""
        return self.factor > 1

    def badge_text(self) -> str:
        """Return the on-screen label, for example ``TIME-COMPRESSED DEMO · 1s = 4min``."""
        if not self.compressed:
            return ""
        minutes, seconds = divmod(self.factor, 60)
        rate = f"{minutes}min" if minutes and not seconds else f"{self.factor}s"
        return f"TIME-COMPRESSED DEMO · 1s = {rate}"


class RealClock:
    """Wall time. The default everywhere outside a demo run."""

    def now(self) -> datetime:
        """Return the current time in UTC."""
        return datetime.now(UTC)

    @property
    def compressed(self) -> bool:
        return False

    def badge_text(self) -> str:
        return ""


_CLOCK: DemoClock | RealClock = RealClock()


def install(clock: DemoClock | RealClock) -> None:
    """Install the process clock. Only a demo run calls this."""
    global _CLOCK
    _CLOCK = clock


def reset() -> None:
    """Restore wall time."""
    install(RealClock())


def current() -> DemoClock | RealClock:
    """Return the installed clock, for the badge and for tests."""
    return _CLOCK


def now() -> datetime:
    """Return the current time on the installed clock."""
    return _CLOCK.now()
