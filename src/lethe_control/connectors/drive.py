"""Google Drive source connector.

Observes Drive change-feed entries and reports them as payload-free
lifecycle observations per the SourceConnector contract. See
fixtures/connectors/drive.json for the manifest and
fixtures/connectors/drive_changes.json for the fake conformance feed.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from datetime import datetime
from typing import Any, Protocol

from lethe_control.connectors.base import (
    ConnectorUnavailable,
    ObservedSourceChange,
    SourceCapabilities,
    SourceChangePage,
    SourceGapCode,
)
from lethe_control.deterministic import stable_id
from lethe_control.models import EventType

_CONNECTOR_REF = "connector://gdrive"


class DriveTransport(Protocol):
    def list_changes(self, page_token: str | None) -> Mapping[str, Any]: ...


def _parse_observed_at(raw: str) -> datetime:
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    return datetime.fromisoformat(raw)


def _change_type(entry: Mapping[str, Any]) -> EventType | None:
    if entry.get("removed") or entry.get("trashed"):
        return EventType.DELETE
    if entry.get("permissions_changed"):
        return EventType.PERMISSION_CHANGE
    if entry.get("edited"):
        return EventType.CORRECT
    return None


class DriveSourceConnector:
    """Source connector for Google Drive change-feed entries.

    Drive has no native expiry events, so EventType.EXPIRE stays declared in
    capabilities() for future retention integration but is never emitted by
    this transport shape.
    """

    def __init__(self, transport: DriveTransport) -> None:
        self._transport = transport

    def capabilities(self) -> SourceCapabilities:
        return SourceCapabilities(
            connector_ref=_CONNECTOR_REF,
            capability_version="1",
            event_types=(
                EventType.DELETE,
                EventType.CORRECT,
                EventType.EXPIRE,
                EventType.PERMISSION_CHANGE,
            ),
            supports_incremental_cursor=True,
            supports_permission_events=True,
        )

    def poll_changes(self, cursor: str | None) -> SourceChangePage:
        page = self._transport.list_changes(cursor)
        changes: list[ObservedSourceChange] = []
        for entry in page.get("changes", []):
            change_type = _change_type(entry)
            if change_type is None:
                continue
            changes.append(
                ObservedSourceChange(
                    connector_ref=_CONNECTOR_REF,
                    external_ref=stable_id("gdrivesrc", str(entry["file_id"])),
                    change_type=change_type,
                    observed_at=_parse_observed_at(entry["time"]),
                    source_sequence=int(entry["sequence"]),
                    acl_changed=change_type is EventType.PERMISSION_CHANGE,
                )
            )
        changes.sort(key=lambda change: change.source_sequence)

        next_page_token = page.get("next_page_token")
        gaps: tuple[SourceGapCode, ...] = ()
        if page.get("token_expired"):
            gaps = (SourceGapCode.CURSOR_EXPIRED,)

        return SourceChangePage(
            changes=tuple(changes),
            next_cursor=next_page_token,
            freshness_cursor=next_page_token or cursor or "genesis",
            gaps=gaps,
        )


class HttpDriveTransport:
    """Live Drive transport stub gated on OAuth client configuration.

    Fake tests (see fixtures/connectors/drive_changes.json) prove the
    contract shape only — not OAuth, changes.list pagination, rate limits, or
    current Drive API schemas.
    """

    def __init__(self, client_id: str | None = None, client_secret: str | None = None) -> None:
        resolved_client_id = (
            client_id if client_id is not None else os.environ.get("LETHE_GDRIVE_CLIENT_ID")
        )
        resolved_client_secret = (
            client_secret
            if client_secret is not None
            else os.environ.get("LETHE_GDRIVE_CLIENT_SECRET")
        )
        if not resolved_client_id or not resolved_client_secret:
            raise ConnectorUnavailable("drive oauth client not configured")
        self._client_id = resolved_client_id
        self._client_secret = resolved_client_secret

    def list_changes(self, page_token: str | None) -> Mapping[str, Any]:
        raise NotImplementedError(
            "live Drive transport requires OAuth consent and is exercised only by live conformance"
        )
