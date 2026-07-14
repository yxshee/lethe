"""App-local cache/summary/memory reference adapter.

Implements the ``StoreAdapter`` contract over the agent state database's
``cache_entries``/``summaries``/``memories`` tables. The adapter owns store
rows only: lifecycle tombstones stay a service-level graph operation applied
after a verified result. A crash between the adapter's delete transaction and
the service's tombstone is recovered by the leased-action retry path —
re-applying a delete on absent rows is idempotent and verifies as absent.

Semantics per the derivative table: cache rows are evicted (optionally
principal/policy-keyed on permission narrowing), summaries and memories are
deleted for rebuild from surviving parents — never edited in place. All other
action codes are rejected as unsupported.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from lethe_control.adapters import (
    AdapterAction,
    AdapterCapabilities,
    AdapterResult,
    InventoryRecord,
)
from lethe_control.models import ActionCode, ObjectKind, ScopeKey

if TYPE_CHECKING:
    from lethe_control.database import StateStore

CONNECTOR_REF = "connector://app-local-sqlite"

KIND_TABLES: dict[ObjectKind, str] = {
    ObjectKind.CACHE: "cache_entries",
    ObjectKind.SUMMARY: "summaries",
    ObjectKind.MEMORY: "memories",
}


def _scope_values(scope: ScopeKey) -> tuple[str, str, str]:
    return (scope.tenant_id, scope.workspace_id, scope.environment_id)


class SqliteAppLocalAdapter:
    def __init__(self, state: StateStore) -> None:
        self._state = state
        self.fail_next_apply = False

    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            connector_ref=CONNECTOR_REF,
            version="1",
            kinds=tuple(KIND_TABLES),
        )

    def inventory(self, scope: ScopeKey) -> list[InventoryRecord]:
        records: list[InventoryRecord] = []
        for kind, table in KIND_TABLES.items():
            rows = self._state.fetch_all(
                f"""
                SELECT DISTINCT version_id FROM {table}
                WHERE tenant_id=? AND workspace_id=? AND environment_id=?
                """,
                _scope_values(scope),
            )
            for row in rows:
                version_id = str(row["version_id"])
                records.append(
                    InventoryRecord(
                        connector_ref=CONNECTOR_REF,
                        opaque_target_ref=version_id,
                        kind=kind,
                        tracked_version_id=version_id,
                        metadata={},
                    )
                )
        return records

    def apply_action(self, scope: ScopeKey, action: AdapterAction) -> AdapterResult:
        if self.fail_next_apply:
            self.fail_next_apply = False
            raise RuntimeError("injected_app_local_apply_failure")
        if action.kind not in KIND_TABLES:
            return AdapterResult(
                applied=False, verified=False, result_code="unsupported_kind", metadata={}
            )
        if action.action_code not in {ActionCode.DELETE, ActionCode.EVICT}:
            return AdapterResult(
                applied=False, verified=False, result_code="unsupported_action", metadata={}
            )
        if (
            action.kind is ObjectKind.CACHE
            and action.action_code is ActionCode.EVICT
            and action.metadata.get("mode") == "principal_evict"
        ):
            return self._principal_evict(scope, action)
        return self._delete_rows(scope, action)

    def read_back(self, scope: ScopeKey, action: AdapterAction) -> AdapterResult:
        if action.kind not in KIND_TABLES:
            return AdapterResult(
                applied=False, verified=False, result_code="unsupported_kind", metadata={}
            )
        remaining = self._state.fetch_one(
            f"""
            SELECT 1 FROM {KIND_TABLES[action.kind]}
            WHERE tenant_id=? AND workspace_id=? AND environment_id=?
              AND version_id=? LIMIT 1
            """,
            (*_scope_values(scope), action.target_version_id),
        )
        absent = remaining is None
        return AdapterResult(
            applied=False,
            verified=absent,
            result_code="payload_absent" if absent else "residual_rows",
            metadata={},
        )

    def _delete_rows(self, scope: ScopeKey, action: AdapterAction) -> AdapterResult:
        table = KIND_TABLES[action.kind]
        with self._state.transaction() as connection:
            connection.execute(
                f"""
                DELETE FROM {table}
                WHERE tenant_id=? AND workspace_id=? AND environment_id=? AND version_id=?
                """,
                (*_scope_values(scope), action.target_version_id),
            )
            remaining = connection.execute(
                f"""
                SELECT 1 FROM {table}
                WHERE tenant_id=? AND workspace_id=? AND environment_id=?
                  AND version_id=? LIMIT 1
                """,
                (*_scope_values(scope), action.target_version_id),
            ).fetchone()
        absent = remaining is None
        return AdapterResult(
            applied=True,
            verified=absent,
            result_code="payload_absent" if absent else "read_back_failed",
            metadata={},
        )

    def _principal_evict(self, scope: ScopeKey, action: AdapterAction) -> AdapterResult:
        allowed_raw = action.metadata.get("allowed_principal_refs")
        new_policy_version = action.metadata.get("new_policy_version")
        if not isinstance(allowed_raw, (list, tuple)) or not isinstance(new_policy_version, int):
            return AdapterResult(
                applied=False,
                verified=False,
                result_code="invalid_principal_evict_request",
                metadata={},
            )
        allowed = [str(item) for item in allowed_raw]
        placeholders = ",".join("?" for _ in allowed)
        version_id = action.target_version_id
        with self._state.transaction() as connection:
            connection.execute(
                f"""
                DELETE FROM cache_entries
                WHERE tenant_id=? AND workspace_id=? AND environment_id=?
                  AND version_id=?
                  AND principal_ref NOT IN ({placeholders})
                """,
                (*_scope_values(scope), version_id, *allowed),
            )
            connection.execute(
                """
                UPDATE cache_entries SET policy_version=?
                WHERE tenant_id=? AND workspace_id=? AND environment_id=?
                  AND version_id=?
                """,
                (new_policy_version, *_scope_values(scope), version_id),
            )
        remaining_removed = self._state.fetch_one(
            f"""
            SELECT 1 FROM cache_entries
            WHERE tenant_id=? AND workspace_id=? AND environment_id=?
              AND version_id=? AND principal_ref NOT IN ({placeholders})
            LIMIT 1
            """,
            (*_scope_values(scope), version_id, *allowed),
        )
        stale_policy = self._state.fetch_one(
            """
            SELECT 1 FROM cache_entries
            WHERE tenant_id=? AND workspace_id=? AND environment_id=?
              AND version_id=? AND policy_version<>? LIMIT 1
            """,
            (*_scope_values(scope), version_id, new_policy_version),
        )
        verified = remaining_removed is None and stale_policy is None
        return AdapterResult(
            applied=True,
            verified=verified,
            result_code="removed_principal_cache_evicted" if verified else "read_back_failed",
            metadata={"policy_version": json.dumps(new_policy_version)},
        )
