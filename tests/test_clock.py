from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from lethe_control.clock import MutableClock
from lethe_control.errors import StateUnavailableError


def test_mutable_clock_advances_and_detects_rollback() -> None:
    start = datetime(2026, 7, 14, tzinfo=UTC)
    clock = MutableClock(start)

    assert clock.advance(timedelta(minutes=5)) == start + timedelta(minutes=5)

    clock.set(start)
    with pytest.raises(StateUnavailableError, match="clock moved backwards"):
        clock.now()


def test_mutable_clock_rejects_naive_values() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        MutableClock(datetime(2026, 7, 14))
