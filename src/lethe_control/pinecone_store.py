from __future__ import annotations

import os
from collections.abc import Sequence
from contextlib import suppress
from datetime import UTC, datetime
from threading import RLock
from typing import Any, cast

from pinecone import Index, Pinecone, ServerlessSpec

from lethe_control.connectors.base import ConnectorUnavailable
from lethe_control.deterministic import DEFAULT_EMBEDDING_DIMENSIONS
from lethe_control.vector_common import clean_metadata, policy_binding, scoped_digest


class PineconeStore:
    """Pinecone sink with the same surface and payload shape as QdrantStore/ChromaStore.

    This scaffold is exercised against live Pinecone only when
    LETHE_PINECONE_API_KEY is set. The unit tests in tests/test_pinecone_store.py
    cover pure logic (physical IDs, metadata filters, credential handling) without
    ever making a network call, so they prove contract shape, not live
    integration. Live conformance runs in tests/test_vector_conformance.py and
    is skipped there unless LETHE_PINECONE_API_KEY is configured.

    Pinecone serverless indexes are eventually consistent: a write may not be
    immediately visible to a subsequent read on the same or another connection,
    so live conformance assertions may need retries around upsert/query/delete
    ordering that other backends satisfy immediately.
    """

    INDEX_NAME = "lethe-derivatives-v1"
    _ID_DOMAIN = b"LETHE-PINECONE-ID-V1\0"

    def __init__(self, api_key: str | None = None, *, index_host: str | None = None) -> None:
        resolved_api_key = api_key or os.environ.get("LETHE_PINECONE_API_KEY")
        if not resolved_api_key:
            raise ConnectorUnavailable("LETHE_PINECONE_API_KEY is not set")
        self._client = Pinecone(api_key=resolved_api_key)
        self._cloud = os.environ.get("LETHE_PINECONE_CLOUD", "aws")
        self._region = os.environ.get("LETHE_PINECONE_REGION", "us-east-1")
        self._index_host = index_host
        self._lock = RLock()
        self.fail_next_delete = False
        self._ensure_index()
        self._index = self._connect_index()

    def _ensure_index(self) -> None:
        if not self._client.has_index(self.INDEX_NAME):
            self._client.create_index(
                name=self.INDEX_NAME,
                dimension=DEFAULT_EMBEDDING_DIMENSIONS,
                metric="cosine",
                spec=ServerlessSpec(cloud=self._cloud, region=self._region),
            )

    def _connect_index(self) -> Index:
        if self._index_host:
            handle = self._client.index(host=self._index_host)
        else:
            handle = self._client.index(name=self.INDEX_NAME)
        return cast(Index, handle)

    @classmethod
    def physical_id(
        cls,
        *,
        tenant_id: str,
        workspace_id: str,
        environment_id: str,
        version_id: str,
    ) -> str:
        """Return a collision-resistant Pinecone vector ID bound to the full logical scope."""

        digest = scoped_digest(
            cls._ID_DOMAIN,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            environment_id=environment_id,
            version_id=version_id,
        )
        return f"vec-{digest.hex()}"

    @staticmethod
    def _metadata_filter(
        tenant_id: str,
        workspace_id: str,
        environment_id: str,
        *,
        active_only: bool = False,
        valid_after: datetime | None = None,
        allowed_policy_bindings: Sequence[tuple[str, int]] | None = None,
    ) -> dict[str, Any]:
        filter_dict: dict[str, Any] = {
            "tenant_id": {"$eq": tenant_id},
            "workspace_id": {"$eq": workspace_id},
            "environment_id": {"$eq": environment_id},
        }
        if active_only:
            filter_dict["lifecycle_state"] = {"$eq": "active"}
        if valid_after is not None:
            if valid_after.tzinfo is None:
                raise ValueError("valid_after must include a timezone")
            filter_dict["valid_until_epoch"] = {"$gt": valid_after.astimezone(UTC).timestamp()}
        if allowed_policy_bindings is not None:
            bindings = sorted(
                {
                    policy_binding(acl_ref, policy_version)
                    for acl_ref, policy_version in allowed_policy_bindings
                }
            )
            if bindings:
                filter_dict["policy_binding"] = {"$in": bindings}
        return filter_dict

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
        clean = clean_metadata(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            environment_id=environment_id,
            version_id=version_id,
            metadata=metadata,
        )
        # clean_metadata already drops None values; filter again defensively since
        # Pinecone metadata values must be str/number/bool/list-of-str only.
        safe_metadata = {key: value for key, value in clean.items() if value is not None}
        vector_id = self.physical_id(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            environment_id=environment_id,
            version_id=version_id,
        )
        vectors: list[dict[str, Any]] = [
            {"id": vector_id, "values": embedding, "metadata": safe_metadata}
        ]
        with self._lock:
            self._index.upsert(vectors=vectors)

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
        with self._lock:
            response = self._index.query(
                vector=embedding,
                top_k=limit,
                filter=self._metadata_filter(
                    tenant_id,
                    workspace_id,
                    environment_id,
                    active_only=active_only,
                    valid_after=valid_after,
                    allowed_policy_bindings=allowed_policy_bindings,
                ),
                include_metadata=True,
            )
        matches: list[tuple[str, float, dict[str, Any]]] = []
        for match in response.matches:
            payload = dict(match.metadata or {})
            if payload.get("version_id") is None:
                continue
            matches.append((str(payload["version_id"]), 1.0 - float(match.score), payload))
        return matches

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
            raise RuntimeError("injected_pinecone_delete_failure")
        vector_id = self.physical_id(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            environment_id=environment_id,
            version_id=version_id,
        )
        with self._lock:
            self._index.delete(ids=[vector_id])

    def exists(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        environment_id: str,
        version_id: str,
    ) -> bool:
        vector_id = self.physical_id(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            environment_id=environment_id,
            version_id=version_id,
        )
        with self._lock:
            response = self._index.fetch(ids=[vector_id])
        return bool(response.vectors)

    def update_metadata(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        environment_id: str,
        version_id: str,
        metadata: dict[str, Any],
    ) -> None:
        vector_id = self.physical_id(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            environment_id=environment_id,
            version_id=version_id,
        )
        with self._lock:
            existing = self._index.fetch(ids=[vector_id])
            vector = existing.vectors.get(vector_id)
            if vector is None:
                return
            clean = clean_metadata(
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                environment_id=environment_id,
                version_id=version_id,
                metadata=metadata,
            )
            vectors: list[dict[str, Any]] = [
                {"id": vector_id, "values": vector.values, "metadata": clean}
            ]
            self._index.upsert(vectors=vectors)

    def inventory(
        self, *, tenant_id: str, workspace_id: str, environment_id: str
    ) -> list[tuple[str, dict[str, Any]]]:
        records: list[tuple[str, dict[str, Any]]] = []
        with self._lock:
            ids: list[str] = []
            for page in self._index.list():
                ids.extend(item.id for item in page.vectors if item.id is not None)
            for offset in range(0, len(ids), 100):
                batch = ids[offset : offset + 100]
                fetched = self._index.fetch(ids=batch)
                for vector in fetched.vectors.values():
                    payload = dict(vector.metadata or {})
                    if payload.get("version_id") is None:
                        continue
                    if (
                        payload.get("tenant_id") != tenant_id
                        or payload.get("workspace_id") != workspace_id
                        or payload.get("environment_id") != environment_id
                    ):
                        continue
                    records.append((str(payload["version_id"]), payload))
        return records

    def reset(self) -> None:
        with self._lock:
            with suppress(Exception):
                self._client.delete_index(self.INDEX_NAME)
            self._ensure_index()
            self._index = self._connect_index()
