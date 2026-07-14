from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from lethe_control.vector_store import ChromaStore


def test_scope_derived_ids_prevent_cross_scope_overwrite_and_delete(tmp_path: Path) -> None:
    store = ChromaStore(tmp_path / "chroma")
    vector = [0.0] * 63 + [1.0]
    scope_a = {
        "tenant_id": "tenant-a",
        "workspace_id": "workspace",
        "environment_id": "local",
    }
    scope_b = {
        "tenant_id": "tenant-b",
        "workspace_id": "workspace",
        "environment_id": "local",
    }
    store.upsert(
        **scope_a,
        version_id="shared-version",
        embedding=vector,
        metadata={
            "root_version_ids": ["source-a"],
            "derivative_kind": "embedding",
        },
    )
    store.upsert(
        **scope_b,
        version_id="shared-version",
        embedding=vector,
        metadata={
            "root_version_ids": ["source-b"],
            "derivative_kind": "embedding",
        },
    )

    assert ChromaStore.physical_id(**scope_a, version_id="shared-version") != (
        ChromaStore.physical_id(**scope_b, version_id="shared-version")
    )
    inventory_a = store.inventory(**scope_a)
    inventory_b = store.inventory(**scope_b)
    assert inventory_a[0][0] == "shared-version"
    assert inventory_a[0][1]["root_version_ids"] == '["source-a"]'
    assert inventory_b[0][0] == "shared-version"
    assert inventory_b[0][1]["root_version_ids"] == '["source-b"]'

    store.delete(**scope_a, version_id="shared-version")
    assert not store.exists(**scope_a, version_id="shared-version")
    assert store.exists(**scope_b, version_id="shared-version")
    assert store.inventory(**scope_b)[0][1]["root_version_ids"] == '["source-b"]'


def test_query_filters_active_valid_and_authorized_policy_metadata(tmp_path: Path) -> None:
    store = ChromaStore(tmp_path / "chroma")
    scope = {
        "tenant_id": "tenant",
        "workspace_id": "workspace",
        "environment_id": "local",
    }
    vector = [1.0] + [0.0] * 63
    records = [
        ("allowed", "active", "2099-01-01T00:00:00Z", "acl://allowed", 2),
        ("inactive", "tombstoned", "2099-01-01T00:00:00Z", "acl://allowed", 2),
        ("expired", "active", "2026-01-01T00:00:00Z", "acl://allowed", 2),
        ("wrong-policy", "active", "2099-01-01T00:00:00Z", "acl://other", 7),
    ]
    for version_id, lifecycle_state, valid_until, acl_ref, policy_version in records:
        store.upsert(
            **scope,
            version_id=version_id,
            embedding=vector,
            metadata={
                "lifecycle_state": lifecycle_state,
                "valid_until": valid_until,
                "acl_ref": acl_ref,
                "policy_version": policy_version,
            },
        )

    matches = store.query(
        **scope,
        embedding=vector,
        limit=10,
        active_only=True,
        valid_after=datetime(2026, 7, 14, tzinfo=UTC),
        allowed_policy_bindings=[("acl://allowed", 2)],
    )

    assert [version_id for version_id, _distance, _metadata in matches] == ["allowed"]


def test_chroma_failure_injection_keeps_record_for_retry(tmp_path: Path) -> None:
    store = ChromaStore(tmp_path / "chroma")
    scope = {
        "tenant_id": "tenant",
        "workspace_id": "workspace",
        "environment_id": "local",
    }
    store.upsert(
        **scope,
        version_id="embedding",
        embedding=[1.0] + [0.0] * 63,
        metadata={},
    )
    store.fail_next_delete = True

    with pytest.raises(RuntimeError, match="injected_chroma_delete_failure"):
        store.delete(**scope, version_id="embedding")
    assert store.exists(**scope, version_id="embedding") is True
    store.delete(**scope, version_id="embedding")
    assert store.exists(**scope, version_id="embedding") is False


def test_upsert_rejects_scope_metadata_mismatch(tmp_path: Path) -> None:
    store = ChromaStore(tmp_path / "chroma")

    with pytest.raises(ValueError, match="metadata tenant_id does not match"):
        store.upsert(
            tenant_id="tenant-a",
            workspace_id="workspace",
            environment_id="local",
            version_id="embedding",
            embedding=[1.0] + [0.0] * 63,
            metadata={"tenant_id": "tenant-b"},
        )
