"""Notion source connector.

Observes page lifecycle changes (trash, edit, expiry, share updates) from
Notion and reports them as payload-free opaque references, per the
SourceConnector contract in ``lethe_control.connectors.base``.
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

_CHANGE_KIND_TO_EVENT_TYPE: Mapping[str, EventType] = {
    "trashed": EventType.DELETE,
    "edited": EventType.CORRECT,
    "expired": EventType.EXPIRE,
    "share_updated": EventType.PERMISSION_CHANGE,
}


def _parse_observed_at(value: str) -> datetime:
    if value.endswith("Z"):
        value = f"{value[:-1]}+00:00"
    return datetime.fromisoformat(value)


class NotionTransport(Protocol):
    def list_changes(self, cursor: str | None) -> Mapping[str, Any]: ...


class NotionSourceConnector:
    """Source connector mapping Notion page changes onto lifecycle events."""

    def __init__(self, transport: NotionTransport) -> None:
        self._transport = transport

    def capabilities(self) -> SourceCapabilities:
        return SourceCapabilities(
            connector_ref="connector://notion",
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
        response = self._transport.list_changes(cursor)
        changes: list[ObservedSourceChange] = []
        for entry in response.get("results", []):
            event_type = _CHANGE_KIND_TO_EVENT_TYPE.get(entry["change"])
            if event_type is None:
                continue
            changes.append(
                ObservedSourceChange(
                    connector_ref="connector://notion",
                    external_ref=stable_id("notionsrc", entry["page_id"]),
                    change_type=event_type,
                    observed_at=_parse_observed_at(entry["observed_at"]),
                    source_sequence=entry["sequence"],
                    acl_changed=event_type is EventType.PERMISSION_CHANGE,
                )
            )
        changes.sort(key=lambda change: change.source_sequence)

        next_cursor = response.get("next_cursor")
        cursor_expired = bool(response.get("cursor_expired", False))
        gaps: tuple[SourceGapCode, ...] = (SourceGapCode.CURSOR_EXPIRED,) if cursor_expired else ()
        freshness_cursor = next_cursor or cursor or "genesis"

        return SourceChangePage(
            changes=tuple(changes),
            next_cursor=next_cursor,
            freshness_cursor=freshness_cursor,
            gaps=gaps,
        )


class HttpNotionTransport:
    """Live Notion transport stub, gated on credentials.

    Fake-transport tests (backed by recorded fixtures) prove contract shape
    only -- they do not prove auth, live pagination, rate limits, or that
    this matches Notion's current API schemas. The live leg is intentionally
    not implemented until credentials exist to exercise it.
    """

    def __init__(self, token: str | None = None) -> None:
        resolved_token = token if token is not None else os.getenv("LETHE_NOTION_TOKEN")
        if not resolved_token:
            raise ConnectorUnavailable("notion token not configured")
        self._token = resolved_token

    def list_changes(self, cursor: str | None) -> Mapping[str, Any]:
        raise NotImplementedError(
            "live Notion transport requires credentials and is exercised only by live conformance"
        )
