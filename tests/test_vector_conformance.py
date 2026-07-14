"""Backend conformance suite for vector stores.

Every vector backend must expose the same surface, payload shape, scope
isolation, and lifecycle/policy filtering. Chroma always runs; Qdrant runs
when a server is reachable at LETHE_QDRANT_URL (default 127.0.0.1:6333) and
is skipped otherwise.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest

from lethe_control.qdrant_store import QdrantStore
from lethe_control.vector_store import ChromaStore

QDRANT_URL = os.getenv("LETHE_QDRANT_URL", "http://127.0.0.1:6333")
MANIFEST_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "connectors"

VectorBackend = ChromaStore | QdrantStore


@pytest.fixture(params=["chroma", "qdrant"])
def backend(request: pytest.FixtureRequest, tmp_path: Path) -> tuple[str, VectorBackend]:
    if request.param == "chroma":
        return "chroma", ChromaStore(tmp_path / "chroma")
    try:
        store = QdrantStore(QDRANT_URL)
    except Exception:
        pytest.skip(f"qdrant unreachable at {QDRANT_URL}")
    return "qdrant", store


@pytest.fixture
def scope_token() -> str:
    return uuid.uuid4().hex[:12]


def test_scope_derived_ids_prevent_cross_scope_overwrite_and_delete(
    backend: tuple[str, VectorBackend], scope_token: str
) -> None:
    _name, store = backend
    vector = [0.0] * 63 + [1.0]
    scope_a = {
        "tenant_id": f"tenant-a-{scope_token}",
        "workspace_id": "workspace",
        "environment_id": "local",
    }
    scope_b = {
        "tenant_id": f"tenant-b-{scope_token}",
        "workspace_id": "workspace",
        "environment_id": "local",
    }
    for scope, root in ((scope_a, "source-a"), (scope_b, "source-b")):
        store.upsert(
            **scope,
            version_id="shared-version",
            embedding=vector,
            metadata={
                "root_version_ids": [root],
                "derivative_kind": "embedding",
            },
        )

    assert store.physical_id(**scope_a, version_id="shared-version") != (
        store.physical_id(**scope_b, version_id="shared-version")
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

    store.delete(**scope_b, version_id="shared-version")


def test_query_filters_active_valid_and_authorized_policy_metadata(
    backend: tuple[str, VectorBackend], scope_token: str
) -> None:
    _name, store = backend
    scope = {
        "tenant_id": f"tenant-{scope_token}",
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

    assert (
        store.query(
            **scope,
            embedding=vector,
            limit=10,
            allowed_policy_bindings=[],
        )
        == []
    )

    for version_id, *_rest in records:
        store.delete(**scope, version_id=version_id)


def test_update_metadata_lifecycle_transition_is_authoritative(
    backend: tuple[str, VectorBackend], scope_token: str
) -> None:
    _name, store = backend
    scope = {
        "tenant_id": f"tenant-{scope_token}",
        "workspace_id": "workspace",
        "environment_id": "local",
    }
    vector = [1.0] + [0.0] * 63
    store.upsert(
        **scope,
        version_id="embedding",
        embedding=vector,
        metadata={"lifecycle_state": "active", "valid_until": "2099-01-01T00:00:00Z"},
    )
    active = store.query(**scope, embedding=vector, limit=5, active_only=True)
    assert [version_id for version_id, _d, _m in active] == ["embedding"]

    store.update_metadata(
        **scope,
        version_id="embedding",
        metadata={"lifecycle_state": "tombstoned", "valid_until": "2099-01-01T00:00:00Z"},
    )
    assert store.query(**scope, embedding=vector, limit=5, active_only=True) == []
    inventory = store.inventory(**scope)
    assert inventory[0][1]["lifecycle_state"] == "tombstoned"

    store.delete(**scope, version_id="embedding")


def test_failure_injection_keeps_record_for_retry(
    backend: tuple[str, VectorBackend], scope_token: str
) -> None:
    name, store = backend
    scope = {
        "tenant_id": f"tenant-{scope_token}",
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

    with pytest.raises(RuntimeError, match=f"injected_{name}_delete_failure"):
        store.delete(**scope, version_id="embedding")
    assert store.exists(**scope, version_id="embedding") is True
    store.delete(**scope, version_id="embedding")
    assert store.exists(**scope, version_id="embedding") is False


def test_upsert_rejects_scope_metadata_mismatch(
    backend: tuple[str, VectorBackend], scope_token: str
) -> None:
    _name, store = backend

    with pytest.raises(ValueError, match="metadata tenant_id does not match"):
        store.upsert(
            tenant_id=f"tenant-a-{scope_token}",
            workspace_id="workspace",
            environment_id="local",
            version_id="embedding",
            embedding=[1.0] + [0.0] * 63,
            metadata={"tenant_id": "tenant-b"},
        )


def test_capability_manifest_matches_backend(backend: tuple[str, VectorBackend]) -> None:
    name, store = backend
    manifest = json.loads((MANIFEST_DIR / f"{name}.json").read_text())
    assert manifest["connector_ref"] == f"connector://{name}"
    assert manifest["store_ref"] == f"store://{name}"
    assert manifest["kinds"] == ["embedding"]
    for operation, declared in (
        ("inventory", manifest["supports_inventory"]),
        ("upsert", manifest["supports_mutation"]),
        ("exists", manifest["supports_read_back"]),
    ):
        assert declared is True
        assert callable(getattr(store, operation))
