from __future__ import annotations

from datetime import UTC, datetime

import pytest

from lethe_control.connectors.base import (
    ObservedSourceChange,
    SourceCapabilities,
    SourceChangePage,
    SourceGapCode,
)
from lethe_control.models import EventType

NOW = datetime(2026, 7, 15, 9, tzinfo=UTC)


def _change(external_ref: str) -> ObservedSourceChange:
    return ObservedSourceChange(
        connector_ref="connector://notion",
        external_ref=external_ref,
        change_type=EventType.DELETE,
        observed_at=NOW,
        source_sequence=1,
    )


def test_opaque_external_refs_accepted() -> None:
    change = _change("src_3fk2m9q0")
    assert change.external_ref == "src_3fk2m9q0"


@pytest.mark.parametrize(
    "bad_ref",
    [
        "https://notion.so/page/123",
        "http://drive.google.com/file/x",
        "/etc/passwd",
        "../secrets.txt",
        "~/Documents/report.docx",
        "",
    ],
)
def test_url_and_path_external_refs_rejected(bad_ref: str) -> None:
    with pytest.raises(ValueError):
        _change(bad_ref)


def test_connector_ref_must_be_registered_reference() -> None:
    with pytest.raises(ValueError, match="registered connector reference"):
        ObservedSourceChange(
            connector_ref="https://example.com/hook",
            external_ref="src_x",
            change_type=EventType.DELETE,
            observed_at=NOW,
            source_sequence=1,
        )
    with pytest.raises(ValueError, match="registered connector reference"):
        SourceCapabilities(
            connector_ref="notion",
            capability_version="1",
            event_types=(EventType.DELETE,),
            supports_incremental_cursor=True,
            supports_permission_events=True,
        )


def test_naive_timestamps_and_bad_sequences_rejected() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        ObservedSourceChange(
            connector_ref="connector://notion",
            external_ref="src_x",
            change_type=EventType.CORRECT,
            observed_at=datetime(2026, 7, 15, 9),
            source_sequence=1,
        )
    with pytest.raises(ValueError, match="positive"):
        ObservedSourceChange(
            connector_ref="connector://notion",
            external_ref="src_x",
            change_type=EventType.CORRECT,
            observed_at=NOW,
            source_sequence=0,
        )


def test_change_page_requires_freshness_cursor_and_declares_gaps() -> None:
    with pytest.raises(ValueError, match="freshness_cursor"):
        SourceChangePage(changes=(), next_cursor=None, freshness_cursor="")

    page = SourceChangePage(
        changes=(_change("src_a"),),
        next_cursor="cursor-2",
        freshness_cursor="cursor-2",
        gaps=(SourceGapCode.CURSOR_EXPIRED,),
    )
    assert page.gaps == (SourceGapCode.CURSOR_EXPIRED,)
