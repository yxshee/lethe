from __future__ import annotations

from datetime import UTC, datetime, timedelta
from threading import RLock
from typing import Protocol

from lethe_control.errors import StateUnavailableError


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class MutableClock:
    """Injectable acceptance-test clock with rollback detection."""

    def __init__(self, value: datetime) -> None:
        if value.tzinfo is None:
            raise ValueError("clock value must be timezone-aware")
        self._value = value.astimezone(UTC)
        self._healthy = True
        self._lock = RLock()

    def now(self) -> datetime:
        with self._lock:
            if not self._healthy:
                raise StateUnavailableError("clock_rollback", "clock moved backwards")
            return self._value

    def advance(self, delta: timedelta) -> datetime:
        if delta.total_seconds() < 0:
            raise ValueError("advance delta cannot be negative")
        with self._lock:
            self._value += delta
            return self._value

    def set(self, value: datetime) -> None:
        if value.tzinfo is None:
            raise ValueError("clock value must be timezone-aware")
        value = value.astimezone(UTC)
        with self._lock:
            if value < self._value:
                self._healthy = False
                return
            self._value = value
