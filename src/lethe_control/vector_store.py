from __future__ import annotations

from collections.abc import Sequence
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import Any

import chromadb

from lethe_control.vector_common import clean_metadata, policy_binding, scoped_digest


class ChromaStore:
    COLLECTION_NAME = "lethe_derivatives_v1"
    _ID_DOMAIN = b"LETHE-CHROMA-ID-V1\0"

    def __init__(self, root: Path) -> None:
        root.mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(path=str(root))
        self._collection = self._client.get_or_create_collection(
            self.COLLECTION_NAME, metadata={"hnsw:space": "cosine"}
        )
        self._lock = RLock()
        self.fail_next_delete = False

    @classmethod
    def physical_id(
        cls,
        *,
        tenant_id: str,
        workspace_id: str,
        environment_id: str,
        version_id: str,
    ) -> str:
        """Return a collision-resistant Chroma key bound to the full logical scope."""

        digest = scoped_digest(
            cls._ID_DOMAIN,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            environment_id=environment_id,
            version_id=version_id,
        )
        return f"vec_{digest.hex()}"

    @staticmethod
    def _policy_binding(acl_ref: str, policy_version: int) -> str:
        return policy_binding(acl_ref, policy_version)

    @staticmethod
    def _clean_metadata(
        *,
        tenant_id: str,
        workspace_id: str,
        environment_id: str,
        version_id: str,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        return clean_metadata(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            environment_id=environment_id,
            version_id=version_id,
            metadata=metadata,
        )

    @classmethod
    def _where(
        cls,
        tenant_id: str,
        workspace_id: str,
        environment_id: str,
        *,
        active_only: bool = False,
        valid_after: datetime | None = None,
        allowed_policy_bindings: Sequence[tuple[str, int]] | None = None,
    ) -> dict[str, Any]:
        clauses: list[dict[str, Any]] = [
            {"tenant_id": {"$eq": tenant_id}},
            {"workspace_id": {"$eq": workspace_id}},
            {"environment_id": {"$eq": environment_id}},
        ]
        if active_only:
            clauses.append({"lifecycle_state": {"$eq": "active"}})
        if valid_after is not None:
            if valid_after.tzinfo is None:
                raise ValueError("valid_after must include a timezone")
            clauses.append({"valid_until_epoch": {"$gt": valid_after.astimezone(UTC).timestamp()}})
        if allowed_policy_bindings is not None:
            bindings = sorted(
                {
                    cls._policy_binding(acl_ref, policy_version)
                    for acl_ref, policy_version in allowed_policy_bindings
                }
            )
            if bindings:
                clauses.append({"policy_binding": {"$in": bindings}})
        return {"$and": clauses}

    def upsert(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        environment_id: str,
        version_id: str,
        embedding: list[float],
        metadata: dict[str, Any],
    ) -> None:
        clean = self._clean_metadata(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            environment_id=environment_id,
            version_id=version_id,
            metadata=metadata,
        )
        physical_id = self.physical_id(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            environment_id=environment_id,
            version_id=version_id,
        )
        embeddings: list[Sequence[float]] = [embedding]
        with self._lock:
            self._collection.upsert(ids=[physical_id], embeddings=embeddings, metadatas=[clean])

    def query(
        self,
        *,
        embedding: list[float],
        tenant_id: str,
        workspace_id: str,
        environment_id: str,
        limit: int = 5,
        active_only: bool = False,
        valid_after: datetime | None = None,
        allowed_policy_bindings: Sequence[tuple[str, int]] | None = None,
    ) -> list[tuple[str, float, dict[str, Any]]]:
        if allowed_policy_bindings is not None and not allowed_policy_bindings:
            return []
        embeddings: list[Sequence[float]] = [embedding]
        with self._lock:
            result = self._collection.query(
                query_embeddings=embeddings,
                n_results=limit,
                where=self._where(
                    tenant_id,
                    workspace_id,
                    environment_id,
                    active_only=active_only,
                    valid_after=valid_after,
                    allowed_policy_bindings=allowed_policy_bindings,
                ),
                include=["metadatas", "distances"],
            )
        distances = (result.get("distances") or [[]])[0]
        metadatas = (result.get("metadatas") or [[]])[0]
        return [
            (str(metadata["version_id"]), float(distance), dict(metadata))
            for distance, raw_metadata in zip(distances, metadatas, strict=True)
            if (metadata := dict(raw_metadata or {})).get("version_id") is not None
        ]

    def delete(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        environment_id: str,
        version_id: str,
    ) -> None:
        if self.fail_next_delete:
            self.fail_next_delete = False
            raise RuntimeError("injected_chroma_delete_failure")
        physical_id = self.physical_id(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            environment_id=environment_id,
            version_id=version_id,
        )
        with self._lock:
            self._collection.delete(ids=[physical_id])

    def exists(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        environment_id: str,
        version_id: str,
    ) -> bool:
        physical_id = self.physical_id(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            environment_id=environment_id,
            version_id=version_id,
        )
        with self._lock:
            result = self._collection.get(ids=[physical_id], include=[])
        return bool(result.get("ids"))

    def update_metadata(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        environment_id: str,
        version_id: str,
        metadata: dict[str, Any],
    ) -> None:
        clean = self._clean_metadata(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            environment_id=environment_id,
            version_id=version_id,
            metadata=metadata,
        )
        physical_id = self.physical_id(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            environment_id=environment_id,
            version_id=version_id,
        )
        with self._lock:
            self._collection.update(ids=[physical_id], metadatas=[clean])

    def inventory(
        self, *, tenant_id: str, workspace_id: str, environment_id: str
    ) -> list[tuple[str, dict[str, Any]]]:
        with self._lock:
            result = self._collection.get(
                where=self._where(tenant_id, workspace_id, environment_id),
                include=["metadatas"],
            )
        ids = result.get("ids") or []
        metadatas = result.get("metadatas") or []
        return [
            (str(metadata["version_id"]), dict(metadata))
            for _physical_id, raw_metadata in zip(ids, metadatas, strict=True)
            if (metadata := dict(raw_metadata or {})).get("version_id") is not None
        ]

    def reset(self) -> None:
        with self._lock:
            with suppress(Exception):
                self._client.delete_collection(self.COLLECTION_NAME)
            self._collection = self._client.get_or_create_collection(
                self.COLLECTION_NAME, metadata={"hnsw:space": "cosine"}
            )
