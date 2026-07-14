"""Source-connector conformance suite.

Fake legs replay recorded change feeds and prove CONTRACT SHAPE ONLY:
classification, cursor monotonicity, opaque-ref hygiene, gap declaration,
and manifest agreement. They do not prove auth flows, live pagination,
rate limits, or current provider API schemas — that is what the live legs
are for, and those skip until credentials exist.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from lethe_control.connectors.base import (
    ConnectorUnavailable,
    SourceConnector,
    SourceGapCode,
)
from lethe_control.connectors.drive import (
    DriveSourceConnector,
    HttpDriveTransport,
)
from lethe_control.connectors.notion import (
    HttpNotionTransport,
    NotionSourceConnector,
)
from lethe_control.models import EventType

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "connectors"

URL_OR_PATH = re.compile(r"^(https?://|/|\./|\.\./|~)")


class ReplayTransport:
    """Dict-backed transport replaying a recorded change feed."""

    def __init__(self, fixture_name: str) -> None:
        self._pages: dict[str, Any] = json.loads(
            (FIXTURES / fixture_name).read_text(encoding="utf-8")
        )["pages"]

    def list_changes(self, cursor: str | None) -> Mapping[str, Any]:
        return dict(self._pages[cursor or "genesis"])


CONNECTOR_CONFIG = {
    "notion": {
        "manifest": "notion.json",
        "fixture": "notion_changes.json",
        "factory": lambda transport: NotionSourceConnector(transport),
        "live": HttpNotionTransport,
        "second_cursor": "cursor-2",
        "expired_cursor": "expired-cursor",
        "expected_first_page": [
            EventType.DELETE,
            EventType.CORRECT,
            EventType.EXPIRE,
            EventType.PERMISSION_CHANGE,
        ],
    },
    "gdrive": {
        "manifest": "drive.json",
        "fixture": "drive_changes.json",
        "factory": lambda transport: DriveSourceConnector(transport),
        "live": HttpDriveTransport,
        "second_cursor": "token-2",
        "expired_cursor": "expired-token",
        "expected_first_page": [
            EventType.DELETE,
            EventType.DELETE,
            EventType.PERMISSION_CHANGE,
            EventType.CORRECT,
        ],
    },
}


@pytest.fixture(params=sorted(CONNECTOR_CONFIG))
def connector_case(request: pytest.FixtureRequest) -> tuple[str, SourceConnector, dict[str, Any]]:
    name = request.param
    config = CONNECTOR_CONFIG[name]
    connector = config["factory"](ReplayTransport(config["fixture"]))
    return name, connector, config


def test_classification_matrix(
    connector_case: tuple[str, SourceConnector, dict[str, Any]],
) -> None:
    _name, connector, config = connector_case
    page = connector.poll_changes(None)
    assert [change.change_type for change in page.changes] == config["expected_first_page"]
    permission_changes = [
        change for change in page.changes if change.change_type is EventType.PERMISSION_CHANGE
    ]
    assert all(change.acl_changed for change in permission_changes)


def test_cursor_monotonic_and_idempotent(
    connector_case: tuple[str, SourceConnector, dict[str, Any]],
) -> None:
    _name, connector, config = connector_case
    first = connector.poll_changes(None)
    again = connector.poll_changes(None)
    assert first == again
    sequences = [change.source_sequence for change in first.changes]
    assert sequences == sorted(sequences)
    assert first.next_cursor == config["second_cursor"]

    second = connector.poll_changes(config["second_cursor"])
    assert all(change.source_sequence > sequences[-1] for change in second.changes), (
        "second page sequences must continue past the first page"
    )
    assert second.freshness_cursor  # falls back to the input cursor when feed ends


def test_external_refs_are_opaque(
    connector_case: tuple[str, SourceConnector, dict[str, Any]],
) -> None:
    _name, connector, config = connector_case
    for cursor in (None, config["second_cursor"]):
        for change in connector.poll_changes(cursor).changes:
            assert not URL_OR_PATH.match(change.external_ref)
            assert "://" not in change.external_ref


def test_expired_cursor_is_a_declared_gap(
    connector_case: tuple[str, SourceConnector, dict[str, Any]],
) -> None:
    _name, connector, config = connector_case
    page = connector.poll_changes(config["expired_cursor"])
    assert SourceGapCode.CURSOR_EXPIRED in page.gaps
    assert page.changes == ()


def test_manifest_matches_capabilities(
    connector_case: tuple[str, SourceConnector, dict[str, Any]],
) -> None:
    _name, connector, config = connector_case
    manifest = json.loads((FIXTURES / config["manifest"]).read_text(encoding="utf-8"))
    capabilities = connector.capabilities()
    assert manifest["role"] == "source"
    assert manifest["connector_ref"] == capabilities.connector_ref
    assert sorted(manifest["event_types"]) == sorted(
        event.value for event in capabilities.event_types
    )
    assert manifest["supports_incremental_cursor"] == capabilities.supports_incremental_cursor
    assert manifest["supports_permission_events"] == capabilities.supports_permission_events


@pytest.mark.parametrize("name", sorted(CONNECTOR_CONFIG))
def test_live_transport_conformance(name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    for env in ("LETHE_NOTION_TOKEN", "LETHE_GDRIVE_CLIENT_ID", "LETHE_GDRIVE_CLIENT_SECRET"):
        monkeypatch.delenv(env, raising=False)
    config = CONNECTOR_CONFIG[name]
    try:
        config["live"]()
    except ConnectorUnavailable as exc:
        pytest.skip(f"{name} live conformance requires credentials: {exc}")
    pytest.fail("live transport constructed without credentials — gate is broken")
