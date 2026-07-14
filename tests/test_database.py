from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from lethe_control.config import Settings
from lethe_control.database import StateStore
from lethe_control.models import (
    KnowledgeEnvelope,
    LifecycleEvent,
    LifecycleState,
    ObjectKind,
    Provenance,
    ScopeKey,
)

NOW = datetime(2026, 7, 14, 10, tzinfo=UTC)
SCOPE = ScopeKey(tenant_id="ten_demo", workspace_id="ws_demo", environment_id="env_local")
OTHER_SCOPE = ScopeKey(tenant_id="ten_demo", workspace_id="ws_other", environment_id="env_local")


@pytest.fixture
def store(settings: Settings) -> StateStore:
    state_store = StateStore(settings.database_path)
    state_store.initialize()
    return state_store


def envelope(
    version_id: str,
    *,
    scope: ScopeKey = SCOPE,
    kind: ObjectKind = ObjectKind.SOURCE,
    parents: list[str] | None = None,
    roots: list[str] | None = None,
) -> KnowledgeEnvelope:
    parent_ids = parents or []
    root_ids = roots if roots is not None else [version_id]
    return KnowledgeEnvelope(
        **scope.model_dump(),
        object_id=f"obj_{version_id}",
        version_id=version_id,
        kind=kind,
        parent_version_ids=parent_ids,
        root_version_ids=root_ids,
        lifecycle_state=LifecycleState.ACTIVE,
        created_at=NOW,
        valid_from=NOW,
        policy_ref="policy://demo/default",
        policy_version=1,
        acl_ref="acl://demo/default/1",
        content_ref=f"local://objects/{version_id}",
        local_content_fingerprint=f"fingerprint-{version_id}",
        provenance=Provenance(
            activity_type="ingest" if kind is ObjectKind.SOURCE else "derive",
            connector_ref="connector://local-files",
            run_id="run_ingest",
        ),
    )


def event(**updates: object) -> LifecycleEvent:
    values: dict[str, object] = {
        **SCOPE.model_dump(),
        "event_id": "evt_delete",
        "idempotency_key": "delete-1",
        "issuer_key_id": "issuer_demo",
        "audience": "agent_demo",
        "authority_ref": "authority://demo/source-admin",
        "nonce": "nonce-delete-1",
        "target_version_id": "ver_source",
        "event_type": "delete",
        "source_sequence": 1,
        "policy_version": 1,
        "occurred_at": NOW,
        "effective_at": NOW,
        "command_expires_at": NOW + timedelta(minutes=5),
        "actor_ref": "actor://demo/admin",
        "reason_code": "source_deleted",
    }
    values.update(updates)
    return LifecycleEvent.model_validate(values)


def test_initialize_applies_complete_schema_and_pragmas(store: StateStore) -> None:
    expected = {
        "knowledge_objects",
        "lineage_edges",
        "knowledge_roots",
        "lifecycle_events",
        "policy_versions",
        "tombstones",
        "summaries",
        "memories",
        "cache_entries",
        "scanner_findings",
        "propagation_runs",
        "propagation_actions",
        "action_outbox",
        "scope_manifests",
        "mutation_approvals",
        "acl_policies",
        "key_enrollments",
        "receipt_chain",
    }
    assert expected <= store.table_names()
    assert store.schema_version() == 3
    with store.connect() as connection:
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert connection.execute("PRAGMA busy_timeout").fetchone()[0] == 5_000


def test_transaction_commits_and_rolls_back(store: StateStore) -> None:
    with pytest.raises(RuntimeError, match="abort"), store.transaction() as connection:
        connection.execute(
            """
            INSERT INTO policy_versions (
                tenant_id, workspace_id, environment_id, policy_ref, policy_version,
                snapshot_hash, effective_at, source_authority_ref, policy_json,
                signature, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                *SCOPE.model_dump().values(),
                "policy://rolled-back",
                1,
                "hash",
                "time",
                "auth",
                "{}",
                "sig",
                "time",
            ),
        )
        raise RuntimeError("abort")
    assert store.fetch_one("SELECT 1 FROM policy_versions") is None


def test_envelopes_persist_with_same_scope_lineage(store: StateStore) -> None:
    store.store_knowledge_object(envelope("ver_source"))
    store.store_knowledge_object(
        envelope(
            "ver_chunk",
            kind=ObjectKind.CHUNK,
            parents=["ver_source"],
            roots=["ver_source"],
        )
    )

    loaded = store.get_knowledge_object(SCOPE, "ver_chunk")
    assert loaded is not None
    assert loaded.parent_version_ids == ["ver_source"]
    assert store.descendant_version_ids(SCOPE, "ver_source") == ["ver_chunk"]


def test_cross_scope_edges_and_cycles_are_rejected(store: StateStore) -> None:
    store.store_knowledge_object(envelope("ver_source"))
    store.store_knowledge_object(envelope("ver_other", scope=OTHER_SCOPE))

    with (
        pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"),
        store.transaction() as connection,
    ):
        connection.execute(
            """
            INSERT INTO lineage_edges (
                tenant_id, workspace_id, environment_id,
                parent_version_id, child_version_id, activity_type, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (*SCOPE.model_dump().values(), "ver_source", "ver_other", "derive", "time"),
        )

    store.store_knowledge_object(
        envelope(
            "ver_chunk",
            kind=ObjectKind.CHUNK,
            parents=["ver_source"],
            roots=["ver_source"],
        )
    )
    with (
        pytest.raises(sqlite3.IntegrityError, match="lineage cycle"),
        store.transaction() as connection,
    ):
        connection.execute(
            """
            INSERT INTO lineage_edges (
                tenant_id, workspace_id, environment_id,
                parent_version_id, child_version_id, activity_type, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (*SCOPE.model_dump().values(), "ver_chunk", "ver_source", "derive", "time"),
        )


def test_same_cache_identifier_remains_isolated_by_composite_scope(
    store: StateStore,
) -> None:
    store.store_knowledge_object(envelope("ver_shared"))
    store.store_knowledge_object(envelope("ver_shared", scope=OTHER_SCOPE))
    with store.transaction() as connection:
        for scope, payload in ((SCOPE, "scope-a"), (OTHER_SCOPE, "scope-b")):
            connection.execute(
                """
                INSERT INTO cache_entries (
                    tenant_id, workspace_id, environment_id, cache_key, version_id,
                    principal_ref, policy_version, payload, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    *scope.model_dump().values(),
                    "cache-shared",
                    "ver_shared",
                    "principal://demo/alice",
                    1,
                    payload,
                    "2026-07-14T10:00:00Z",
                ),
            )

    rows = store.fetch_all(
        "SELECT tenant_id, payload FROM cache_entries WHERE cache_key='cache-shared'"
    )
    assert {(row["tenant_id"], row["payload"]) for row in rows} == {
        (SCOPE.tenant_id, "scope-a"),
        (OTHER_SCOPE.tenant_id, "scope-b"),
    }


def test_event_idempotency_and_stream_uniqueness(store: StateStore) -> None:
    first = event()
    with store.transaction() as connection:
        stored, created = store.record_lifecycle_event(connection, first, received_at=NOW)
    assert created is True
    assert stored["event_id"] == first.event_id

    duplicate = event(event_id="evt_duplicate", nonce="different-nonce")
    with store.transaction() as connection:
        stored, created = store.record_lifecycle_event(connection, duplicate, received_at=NOW)
    assert created is False
    assert stored["event_id"] == first.event_id

    stale_stream = event(
        event_id="evt_stale",
        idempotency_key="delete-2",
        nonce="nonce-delete-2",
    )
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"), store.transaction() as connection:
        store.record_lifecycle_event(connection, stale_stream, received_at=NOW)

    assert store.latest_source_sequence(SCOPE, "authority://demo/source-admin", "ver_source") == 1


def test_tombstone_is_payload_free_and_durable(store: StateStore) -> None:
    store.store_knowledge_object(envelope("ver_source"))
    with store.transaction() as connection:
        store.record_lifecycle_event(connection, event(), received_at=NOW)
        assert store.set_lifecycle_state(connection, SCOPE, "ver_source", LifecycleState.DENIED)
        store.install_tombstone(
            connection,
            scope=SCOPE,
            target_version_id="ver_source",
            event_id="evt_delete",
            object_kind=ObjectKind.SOURCE.value,
            reason_code="source_deleted",
            policy_version=1,
            created_at=NOW,
            fingerprint_key_version="fixture-v1",
            local_content_fingerprint="fingerprint-ver_source",
        )

    assert store.has_tombstone(SCOPE, "ver_source")
    row = store.fetch_one("SELECT * FROM tombstones WHERE target_version_id = ?", ("ver_source",))
    assert row is not None
    assert "content_ref" not in row
    source = store.get_knowledge_object(SCOPE, "ver_source")
    assert source is not None
    assert source.lifecycle_state is LifecycleState.DENIED


def test_receipt_chain_requires_ordered_previous_hash(store: StateStore) -> None:
    scope_values = tuple(SCOPE.model_dump().values())
    with store.transaction() as connection:
        store.record_lifecycle_event(connection, event(), received_at=NOW)
        connection.execute(
            """
            INSERT INTO scope_manifests (
                tenant_id, workspace_id, environment_id, manifest_id, schema_version,
                manifest_hash, selected_by, requested_evidence_level, scan_cutoff,
                registered_store_refs_json, registered_connector_refs_json,
                connector_capability_versions_json, derivative_classes_json,
                freshness_cursors_json, denominators_json, exclusions_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                *scope_values,
                "manifest-1",
                "1",
                "manifest-hash",
                "authority://demo/data-owner",
                "L3",
                "2026-07-14T10:00:00Z",
                "[]",
                "[]",
                "[]",
                "[]",
                "{}",
                "{}",
                "[]",
                "2026-07-14T10:00:00Z",
            ),
        )
        connection.execute(
            """
            INSERT INTO key_enrollments (
                tenant_id, workspace_id, environment_id, key_id, agent_id,
                public_key, signing_role, status, valid_from, enrolled_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                *scope_values,
                "receipt-key",
                "agent-demo",
                "public-key",
                "receipt_signer",
                "active",
                "2026-07-14T10:00:00Z",
                "2026-07-14T10:00:00Z",
            ),
        )
        connection.execute(
            """
            INSERT INTO propagation_runs (
                tenant_id, workspace_id, environment_id, run_id, event_id,
                scope_manifest_id, phase, graph_snapshot_hash, action_plan_hash,
                started_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                *scope_values,
                "run-delete",
                "evt_delete",
                "manifest-1",
                "completed",
                "graph-hash",
                "action-plan-hash",
                "2026-07-14T10:00:00Z",
                "2026-07-14T10:00:01Z",
            ),
        )
        connection.execute(
            """
            INSERT INTO receipt_chain (
                tenant_id, workspace_id, environment_id, agent_id, chain_sequence,
                run_id, event_id, key_id, previous_receipt_hash, entry_hash,
                receipt_json, signed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                *scope_values,
                "agent-demo",
                1,
                "run-delete",
                "evt_delete",
                "receipt-key",
                None,
                "entry-hash-1",
                "{}",
                "2026-07-14T10:00:01Z",
            ),
        )

    with (
        pytest.raises(sqlite3.IntegrityError, match="receipt chain discontinuity"),
        store.transaction() as connection,
    ):
        connection.execute(
            """
            INSERT INTO receipt_chain (
                tenant_id, workspace_id, environment_id, agent_id, chain_sequence,
                run_id, event_id, key_id, previous_receipt_hash, entry_hash,
                receipt_json, signed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                *scope_values,
                "agent-demo",
                2,
                "run-delete",
                "evt_delete",
                "receipt-key",
                "wrong-head",
                "entry-hash-2",
                "{}",
                "2026-07-14T10:00:02Z",
            ),
        )
