from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from lethe_control.clock import MutableClock
from lethe_control.config import Settings
from lethe_control.service import LetheService


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(state_dir=tmp_path / ".lethe")


@pytest.fixture
def service(settings: Settings) -> LetheService:
    lifecycle = LetheService(
        settings,
        clock=MutableClock(datetime(2026, 7, 14, 10, tzinfo=UTC)),
    )
    lifecycle.initialize()
    return lifecycle
