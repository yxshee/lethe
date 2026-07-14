"""Unit tests for PineconeStore's pure logic: IDs, filters, and credential handling.

These tests never construct a live Pinecone client — they only exercise
physical_id (a classmethod), _metadata_filter (a staticmethod), and the
credential guard in __init__, which raises before any client is constructed.
Live conformance runs in tests/test_vector_conformance.py and is skipped
there unless LETHE_PINECONE_API_KEY is configured.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from lethe_control.connectors.base import ConnectorUnavailable
from lethe_control.pinecone_store import PineconeStore
from lethe_control.qdrant_store import QdrantStore
from lethe_control.vector_store import ChromaStore

SCOPE = {
    "tenant_id": "tenant-a",
    "workspace_id": "workspace",
    "environment_id": "local",
    "version_id": "embedding",
}


def test_physical_id_differs_from_other_backends() -> None:
    pinecone_id = PineconeStore.physical_id(**SCOPE)
    qdrant_id = QdrantStore.physical_id(**SCOPE)
    chroma_id = ChromaStore.physical_id(**SCOPE)

    assert pinecone_id.startswith("vec-")
    assert pinecone_id != qdrant_id
    assert pinecone_id != chroma_id


def test_physical_id_is_cross_scope_collision_resistant() -> None:
    id_a = PineconeStore.physical_id(
        tenant_id="tenant-a", workspace_id="workspace", environment_id="local", version_id="v"
    )
    id_b = PineconeStore.physical_id(
        tenant_id="tenant-b", workspace_id="workspace", environment_id="local", version_id="v"
    )
    assert id_a != id_b


def test_metadata_filter_builds_scope_lifecycle_valid_and_policy_clauses() -> None:
    valid_after = datetime(2026, 7, 14, tzinfo=UTC)
    filter_dict = PineconeStore._metadata_filter(
        "tenant-a",
        "workspace",
        "local",
        active_only=True,
        valid_after=valid_after,
        allowed_policy_bindings=[("acl://allowed", 2)],
    )

    assert filter_dict["tenant_id"] == {"$eq": "tenant-a"}
    assert filter_dict["workspace_id"] == {"$eq": "workspace"}
    assert filter_dict["environment_id"] == {"$eq": "local"}
    assert filter_dict["lifecycle_state"] == {"$eq": "active"}
    assert filter_dict["valid_until_epoch"] == {"$gt": valid_after.timestamp()}
    assert filter_dict["policy_binding"] == {"$in": sorted(filter_dict["policy_binding"]["$in"])}


def test_metadata_filter_omits_optional_clauses_by_default() -> None:
    filter_dict = PineconeStore._metadata_filter("tenant-a", "workspace", "local")

    assert set(filter_dict) == {"tenant_id", "workspace_id", "environment_id"}


def test_metadata_filter_rejects_naive_valid_after() -> None:
    with pytest.raises(ValueError, match="valid_after must include a timezone"):
        PineconeStore._metadata_filter(
            "tenant-a", "workspace", "local", valid_after=datetime(2026, 7, 14)
        )


def test_missing_api_key_raises_connector_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LETHE_PINECONE_API_KEY", raising=False)

    with pytest.raises(ConnectorUnavailable):
        PineconeStore()
