from __future__ import annotations

import uuid
from collections.abc import Sequence
from contextlib import suppress
from datetime import UTC, datetime
from threading import RLock
from typing import Any

from qdrant_client import QdrantClient, models

from lethe_control.deterministic import DEFAULT_EMBEDDING_DIMENSIONS
from lethe_control.vector_common import clean_metadata, policy_binding, scoped_digest


class QdrantStore:
    """Qdrant sink with the same surface and payload shape as ChromaStore.

    Qdrant point IDs must be UUIDs or integers, so the scoped digest is
    truncated into a deterministic UUID instead of a hex string. Qdrant
    returns cosine similarity; results are converted to cosine distance so
    downstream ordering and thresholds match the Chroma backend.
    """

    COLLECTION_NAME = "lethe_derivatives_v1"
    _ID_DOMAIN = b"LETHE-QDRANT-ID-V1\0"

    def __init__(self, url: str, *, api_key: str | None = None) -> None:
        self._client = QdrantClient(url=url, api_key=api_key)
        self._lock = RLock()
        self.fail_next_delete = False
        self._ensure_collection()

    def _ensure_collection(self) -> None:
        if not self._client.collection_exists(self.COLLECTION_NAME):
            self._client.create_collection(
                collection_name=self.COLLECTION_NAME,
                vectors_config=models.VectorParams(
                    size=DEFAULT_EMBEDDING_DIMENSIONS,
                    distance=models.Distance.COSINE,
                ),
            )

    @classmethod
    def physical_id(
        cls,
        *,
        tenant_id: str,
        workspace_id: str,
        environment_id: str,
        version_id: str,
    ) -> str:
        """Return a collision-resistant Qdrant point ID bound to the full logical scope."""

        digest = scoped_digest(
            cls._ID_DOMAIN,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            environment_id=environment_id,
            version_id=version_id,
        )
        return str(uuid.UUID(bytes=digest[:16]))

    @staticmethod
    def _scope_conditions(
        tenant_id: str, workspace_id: str, environment_id: str
    ) -> list[models.FieldCondition]:
        return [
            models.FieldCondition(key="tenant_id", match=models.MatchValue(value=tenant_id)),
            models.FieldCondition(key="workspace_id", match=models.MatchValue(value=workspace_id)),
            models.FieldCondition(
                key="environment_id", match=models.MatchValue(value=environment_id)
            ),
        ]

    @classmethod
    def _filter(
        cls,
        tenant_id: str,
        workspace_id: str,
        environment_id: str,
        *,
        active_only: bool = False,
        valid_after: datetime | None = None,
        allowed_policy_bindings: Sequence[tuple[str, int]] | None = None,
    ) -> models.Filter:
        must = list(cls._scope_conditions(tenant_id, workspace_id, environment_id))
        if active_only:
            must.append(
                models.FieldCondition(
                    key="lifecycle_state", match=models.MatchValue(value="active")
                )
            )
        if valid_after is not None:
            if valid_after.tzinfo is None:
                raise ValueError("valid_after must include a timezone")
            must.append(
                models.FieldCondition(
                    key="valid_until_epoch",
                    range=models.Range(gt=valid_after.astimezone(UTC).timestamp()),
                )
            )
        if allowed_policy_bindings is not None:
            bindings = sorted(
                {
                    policy_binding(acl_ref, policy_version)
                    for acl_ref, policy_version in allowed_policy_bindings
                }
            )
            if bindings:
                must.append(
                    models.FieldCondition(key="policy_binding", match=models.MatchAny(any=bindings))
                )
        return models.Filter(must=list(must))

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
        point_id = self.physical_id(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            environment_id=environment_id,
            version_id=version_id,
        )
        with self._lock:
            self._client.upsert(
                collection_name=self.COLLECTION_NAME,
                points=[models.PointStruct(id=point_id, vector=embedding, payload=clean)],
                wait=True,
            )

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
            response = self._client.query_points(
                collection_name=self.COLLECTION_NAME,
                query=embedding,
                limit=limit,
                query_filter=self._filter(
                    tenant_id,
                    workspace_id,
                    environment_id,
                    active_only=active_only,
                    valid_after=valid_after,
                    allowed_policy_bindings=allowed_policy_bindings,
                ),
                with_payload=True,
            )
        matches: list[tuple[str, float, dict[str, Any]]] = []
        for point in response.points:
            payload = dict(point.payload or {})
            if payload.get("version_id") is None:
                continue
            matches.append((str(payload["version_id"]), 1.0 - float(point.score), payload))
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
            raise RuntimeError("injected_qdrant_delete_failure")
        point_id = self.physical_id(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            environment_id=environment_id,
            version_id=version_id,
        )
        with self._lock:
            self._client.delete(
                collection_name=self.COLLECTION_NAME,
                points_selector=models.PointIdsList(points=[point_id]),
                wait=True,
            )

    def exists(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        environment_id: str,
        version_id: str,
    ) -> bool:
        point_id = self.physical_id(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            environment_id=environment_id,
            version_id=version_id,
        )
        with self._lock:
            records = self._client.retrieve(
                collection_name=self.COLLECTION_NAME, ids=[point_id], with_payload=False
            )
        return bool(records)

    def update_metadata(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        environment_id: str,
        version_id: str,
        metadata: dict[str, Any],
    ) -> None:
        clean = clean_metadata(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            environment_id=environment_id,
            version_id=version_id,
            metadata=metadata,
        )
        point_id = self.physical_id(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            environment_id=environment_id,
            version_id=version_id,
        )
        with self._lock:
            self._client.overwrite_payload(
                collection_name=self.COLLECTION_NAME,
                payload=clean,
                points=[point_id],
                wait=True,
            )

    def inventory(
        self, *, tenant_id: str, workspace_id: str, environment_id: str
    ) -> list[tuple[str, dict[str, Any]]]:
        records: list[tuple[str, dict[str, Any]]] = []
        offset: models.ExtendedPointId | None = None
        with self._lock:
            while True:
                points, offset = self._client.scroll(
                    collection_name=self.COLLECTION_NAME,
                    scroll_filter=self._filter(tenant_id, workspace_id, environment_id),
                    limit=256,
                    offset=offset,
                    with_payload=True,
                )
                for point in points:
                    payload = dict(point.payload or {})
                    if payload.get("version_id") is None:
                        continue
                    records.append((str(payload["version_id"]), payload))
                if offset is None:
                    break
        return records

    def reset(self) -> None:
        with self._lock:
            with suppress(Exception):
                self._client.delete_collection(self.COLLECTION_NAME)
            self._ensure_collection()
