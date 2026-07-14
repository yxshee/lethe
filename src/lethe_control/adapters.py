from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from lethe_control.models import ActionCode, ObjectKind, ScopeKey


@dataclass(frozen=True, slots=True)
class AdapterCapabilities:
    connector_ref: str
    version: str
    kinds: tuple[ObjectKind, ...]
    supports_inventory: bool = True
    supports_mutation: bool = True
    supports_read_back: bool = True


@dataclass(frozen=True, slots=True)
class InventoryRecord:
    connector_ref: str
    opaque_target_ref: str
    kind: ObjectKind
    tracked_version_id: str | None
    metadata: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class AdapterAction:
    action_id: str
    action_code: ActionCode
    target_version_id: str
    kind: ObjectKind
    content_ref: str | None
    metadata: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class AdapterResult:
    applied: bool
    verified: bool
    result_code: str
    metadata: Mapping[str, Any]


class StoreAdapter(Protocol):
    """Connector boundary used by scanner and propagation orchestration."""

    def capabilities(self) -> AdapterCapabilities: ...

    def inventory(self, scope: ScopeKey) -> list[InventoryRecord]: ...

    def apply_action(self, scope: ScopeKey, action: AdapterAction) -> AdapterResult: ...

    def read_back(self, scope: ScopeKey, action: AdapterAction) -> AdapterResult: ...
