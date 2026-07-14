from __future__ import annotations

import json
from pathlib import Path

import pytest

from lethe_control.adapters import AdapterAction
from lethe_control.app_local_adapter import CONNECTOR_REF, SqliteAppLocalAdapter
from lethe_control.models import ActionCode, LifecycleState, ObjectKind, ScopeKey
from lethe_control.service import LetheService

MANIFEST_PATH = Path(__file__).resolve().parents[1] / "fixtures/connectors/app-local-sqlite.json"


@pytest.fixture
def adapter(service: LetheService) -> SqliteAppLocalAdapter:
    return SqliteAppLocalAdapter(service.state)


def _version_of_kind(service: LetheService, kind: ObjectKind) -> str:
    version_ids = service.state.descendant_version_ids(
        service.scope, "ver_demo_canary_001", include_self=True
    )
    for version_id in sorted(version_ids):
        envelope = service.state.get_knowledge_object(service.scope, version_id)
        if envelope is not None and envelope.kind is kind:
            return envelope.version_id
    raise AssertionError(f"no seeded envelope of kind {kind}")


def _action(
    kind: ObjectKind, version_id: str, code: ActionCode, **metadata: object
) -> AdapterAction:
    return AdapterAction(
        action_id=f"action-{kind.value}",
        action_code=code,
        target_version_id=version_id,
        kind=kind,
        content_ref=None,
        metadata=metadata,
    )


def test_capabilities_match_manifest(adapter: SqliteAppLocalAdapter) -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    capabilities = adapter.capabilities()
    assert manifest["connector_ref"] == CONNECTOR_REF == capabilities.connector_ref
    assert manifest["kinds"] == [kind.value for kind in capabilities.kinds]
    for declared, operation in (
        (manifest["supports_inventory"], "inventory"),
        (manifest["supports_mutation"], "apply_action"),
        (manifest["supports_read_back"], "read_back"),
    ):
        assert declared is True
        assert callable(getattr(adapter, operation))


def test_inventory_is_scope_isolated(adapter: SqliteAppLocalAdapter, service: LetheService) -> None:
    records = adapter.inventory(service.scope)
    assert records
    assert {record.kind for record in records} <= set(
        (ObjectKind.CACHE, ObjectKind.SUMMARY, ObjectKind.MEMORY)
    )
    assert all(record.connector_ref == CONNECTOR_REF for record in records)

    foreign = ScopeKey(tenant_id="ten_other", workspace_id="ws_other", environment_id="env_other")
    assert adapter.inventory(foreign) == []


def test_delete_verifies_absence_and_never_tombstones(
    adapter: SqliteAppLocalAdapter, service: LetheService
) -> None:
    version_id = _version_of_kind(service, ObjectKind.SUMMARY)

    result = adapter.apply_action(
        service.scope, _action(ObjectKind.SUMMARY, version_id, ActionCode.DELETE)
    )
    assert result.applied and result.verified
    assert result.result_code == "payload_absent"

    read_back = adapter.read_back(
        service.scope, _action(ObjectKind.SUMMARY, version_id, ActionCode.DELETE)
    )
    assert read_back.verified and read_back.result_code == "payload_absent"

    envelope = service.state.get_knowledge_object(service.scope, version_id)
    assert envelope is not None
    assert envelope.lifecycle_state is LifecycleState.ACTIVE


def test_principal_evict_is_policy_keyed(
    adapter: SqliteAppLocalAdapter, service: LetheService
) -> None:
    version_id = _version_of_kind(service, ObjectKind.CACHE)
    rows = service.state.fetch_all(
        """
        SELECT principal_ref FROM cache_entries
        WHERE tenant_id=? AND workspace_id=? AND environment_id=? AND version_id=?
        """,
        (
            service.scope.tenant_id,
            service.scope.workspace_id,
            service.scope.environment_id,
            version_id,
        ),
    )
    principals = {str(row["principal_ref"]) for row in rows}
    assert len(principals) >= 2, "seeded cache must be multi-principal for this test"
    kept = sorted(principals)[0]

    result = adapter.apply_action(
        service.scope,
        _action(
            ObjectKind.CACHE,
            version_id,
            ActionCode.EVICT,
            mode="principal_evict",
            allowed_principal_refs=[kept],
            new_policy_version=4,
        ),
    )
    assert result.applied and result.verified
    assert result.result_code == "removed_principal_cache_evicted"

    remaining = service.state.fetch_all(
        """
        SELECT principal_ref, policy_version FROM cache_entries
        WHERE tenant_id=? AND workspace_id=? AND environment_id=? AND version_id=?
        """,
        (
            service.scope.tenant_id,
            service.scope.workspace_id,
            service.scope.environment_id,
            version_id,
        ),
    )
    assert {str(row["principal_ref"]) for row in remaining} == {kept}
    assert {int(row["policy_version"]) for row in remaining} == {4}


def test_unsupported_action_is_rejected(
    adapter: SqliteAppLocalAdapter, service: LetheService
) -> None:
    version_id = _version_of_kind(service, ObjectKind.SUMMARY)
    result = adapter.apply_action(
        service.scope, _action(ObjectKind.SUMMARY, version_id, ActionCode.REBUILD)
    )
    assert not result.applied and not result.verified
    assert result.result_code == "unsupported_action"


def test_read_back_reports_residual_rows(
    adapter: SqliteAppLocalAdapter, service: LetheService
) -> None:
    version_id = _version_of_kind(service, ObjectKind.MEMORY)
    result = adapter.read_back(
        service.scope, _action(ObjectKind.MEMORY, version_id, ActionCode.DELETE)
    )
    assert not result.verified
    assert result.result_code == "residual_rows"


def test_failure_injection_keeps_rows_for_retry(
    adapter: SqliteAppLocalAdapter, service: LetheService
) -> None:
    version_id = _version_of_kind(service, ObjectKind.MEMORY)
    adapter.fail_next_apply = True

    with pytest.raises(RuntimeError, match="injected_app_local_apply_failure"):
        adapter.apply_action(
            service.scope, _action(ObjectKind.MEMORY, version_id, ActionCode.DELETE)
        )
    assert not adapter.read_back(
        service.scope, _action(ObjectKind.MEMORY, version_id, ActionCode.DELETE)
    ).verified

    retry = adapter.apply_action(
        service.scope, _action(ObjectKind.MEMORY, version_id, ActionCode.DELETE)
    )
    assert retry.applied and retry.verified


def test_crash_window_retry_is_idempotent(
    adapter: SqliteAppLocalAdapter, service: LetheService
) -> None:
    version_id = _version_of_kind(service, ObjectKind.CACHE)
    action = _action(ObjectKind.CACHE, version_id, ActionCode.EVICT)

    first = adapter.apply_action(service.scope, action)
    assert first.applied and first.verified

    retry = adapter.apply_action(service.scope, action)
    assert retry.applied and retry.verified
    assert retry.result_code == "payload_absent"
