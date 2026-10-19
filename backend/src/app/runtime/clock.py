"""Injectable time source.

Agents and tools never call ``datetime.now()`` or ``time.sleep()`` directly; they use a
``Clock``. Production code uses ``SystemClock``. Tests and fixtures use ``ManualClock``, where
``sleep`` advances virtual time instantly, so timeouts and backoff are deterministic and fast.
"""

import time
from datetime import datetime, timedelta
from typing import Protocol

from app.domain.timestamps import utc_now


class Clock(Protocol):
    def now(self) -> datetime:
        """Current time as a UTC-aware datetime."""
        ...

    def sleep(self, seconds: float) -> None:
        """Wait for ``seconds``."""
        ...


class SystemClock:
    def now(self) -> datetime:
        return utc_now()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


class ManualClock:
    """Virtual clock: time only moves when ``sleep``/``advance`` is called."""

    def __init__(self, start: datetime) -> None:
        if start.tzinfo is None:
            raise ValueError("ManualClock requires a timezone-aware start time")
        self._now = start

    def now(self) -> datetime:
        return self._now

    def sleep(self, seconds: float) -> None:
        self.advance(seconds)

    def advance(self, seconds: float) -> None:
        if seconds < 0:
            raise ValueError("cannot move a clock backwards")
        self._now += timedelta(seconds=seconds)
