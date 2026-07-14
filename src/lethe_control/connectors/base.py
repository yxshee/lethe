"""Source-connector contract.

Source connectors observe authenticated lifecycle changes (delete, correct,
expire, permission change) in an external system and report them as
payload-free opaque references. A revocation endpoint is a registered
connector reference, never an arbitrary fetchable URL; external refs are
opaque identifiers by construction. Connectors report a freshness cursor and
declare gaps (expired cursors, truncated history) instead of silently
resyncing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from lethe_control.models import EventType


class ConnectorUnavailable(RuntimeError):
    """Raised when a live transport is constructed without working credentials."""


class SourceGapCode(StrEnum):
    CURSOR_EXPIRED = "cursor_expired"
    HISTORY_TRUNCATED = "history_truncated"
    PERMISSION_VISIBILITY_LIMITED = "permission_visibility_limited"


def _require_opaque_ref(value: str, field_name: str) -> None:
    if not value:
        raise ValueError(f"{field_name} must not be empty")
    if "://" in value and not value.startswith("connector://"):
        raise ValueError(f"{field_name} must be an opaque reference, not a URL")
    if value.startswith(("/", "./", "..", "~")):
        raise ValueError(f"{field_name} must be an opaque reference, not a filesystem path")


@dataclass(frozen=True, slots=True)
class SourceCapabilities:
    connector_ref: str
    capability_version: str
    event_types: tuple[EventType, ...]
    supports_incremental_cursor: bool
    supports_permission_events: bool

    def __post_init__(self) -> None:
        if not self.connector_ref.startswith("connector://"):
            raise ValueError("connector_ref must be a registered connector reference")


@dataclass(frozen=True, slots=True)
class ObservedSourceChange:
    """A payload-free observation of one source-system change."""

    connector_ref: str
    external_ref: str
    change_type: EventType
    observed_at: datetime
    source_sequence: int
    acl_changed: bool = False

    def __post_init__(self) -> None:
        if not self.connector_ref.startswith("connector://"):
            raise ValueError("connector_ref must be a registered connector reference")
        _require_opaque_ref(self.external_ref, "external_ref")
        if self.observed_at.tzinfo is None:
            raise ValueError("observed_at must be timezone-aware")
        if self.source_sequence < 1:
            raise ValueError("source_sequence must be positive")


@dataclass(frozen=True, slots=True)
class SourceChangePage:
    changes: tuple[ObservedSourceChange, ...]
    next_cursor: str | None
    freshness_cursor: str
    gaps: tuple[SourceGapCode, ...] = field(default=())

    def __post_init__(self) -> None:
        if not self.freshness_cursor:
            raise ValueError("freshness_cursor must not be empty")


class SourceConnector(Protocol):
    def capabilities(self) -> SourceCapabilities: ...

    def poll_changes(self, cursor: str | None) -> SourceChangePage: ...
