from __future__ import annotations

from pathlib import Path

import pytest

from lethe_control.object_store import LocalObjectStore


def test_object_store_never_uses_caller_identifiers_as_paths(tmp_path: Path) -> None:
    store = LocalObjectStore(tmp_path / "objects")
    content_ref = store.put(
        tenant_id="../tenant",
        workspace_id="../../workspace",
        environment_id="env",
        kind="source",
        storage_id="../../../secret",
        payload=b"bounded payload",
    )

    assert store.get(content_ref) == b"bounded payload"
    assert ".." not in content_ref
    assert store.exists(content_ref)


def test_object_store_rejects_unregistered_or_escaping_refs(tmp_path: Path) -> None:
    store = LocalObjectStore(tmp_path / "objects")

    with pytest.raises(ValueError, match="unregistered"):
        store.get("file:///etc/passwd")
    with pytest.raises(ValueError, match="escapes"):
        store.get("local://objects/../../etc/passwd")


def test_object_store_inventory_and_idempotent_delete(tmp_path: Path) -> None:
    store = LocalObjectStore(tmp_path / "objects")
    ref = store.put(
        tenant_id="ten",
        workspace_id="ws",
        environment_id="env",
        kind="chunk",
        storage_id="ver_chunk",
        payload=b"chunk",
    )

    assert list(store.inventory(tenant_id="ten", workspace_id="ws", environment_id="env")) == [
        (ref, b"chunk")
    ]
    assert store.delete(ref) is True
    assert store.delete(ref) is False
