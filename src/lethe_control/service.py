from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import threading
import time
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from lethe_control.clock import Clock, SystemClock
from lethe_control.config import Settings
from lethe_control.crypto import (
    b64url_decode,
    b64url_encode,
    canonical_json_bytes,
    content_fingerprint,
    derive_purpose_key,
    load_demo_key_fixture,
    load_key_enrollment,
    receipt_entry_hash,
    sign_acl_policy,
    sign_event,
    sign_receipt,
    verify_acl_policy_signature,
    verify_event_signature,
    verify_receipt_signature,
)
from lethe_control.database import StateStore
from lethe_control.deterministic import (
    answer_from_contexts,
    chunk_text,
    feature_hash_embedding,
    fixture_manifest_hash,
    load_fixture_manifest,
    load_probe_suite,
    memory_from_text,
    semantic_score,
    stable_id,
    summarize_text,
)
from lethe_control.errors import AuthorizationError, ConflictError, LetheError, NotFoundError
from lethe_control.models import (
    ACLPolicy,
    ActionCode,
    ActionState,
    ConnectorCapabilityVersion,
    EventAccepted,
    EventType,
    EvidenceLevel,
    ExecutionReceipt,
    FindingClass,
    GovernanceAnnotations,
    GraphEdge,
    GraphNode,
    GraphResponse,
    HealthResponse,
    KnowledgeEnvelope,
    LifecycleEvent,
    LifecycleState,
    MutationApproval,
    ObjectKind,
    PermissionChangeKind,
    PolicyAction,
    PostureAssessmentReport,
    Provenance,
    QueryRequest,
    QueryResponse,
    ReceiptAction,
    ReceiptCounts,
    ReceiptExclusion,
    ReceiptReasonCode,
    ReceiptScope,
    ReceiptVerificationResult,
    ReceiptVerifyRequest,
    ReceiptVerifyResponse,
    ResidualRisk,
    RunOutcome,
    RunStatusResponse,
    ScanFinding,
    ScanRequest,
    ScanResponse,
    ScopeKey,
    ScopeManifest,
    VerificationCode,
    VerificationStatus,
)
from lethe_control.object_store import LocalObjectStore
from lethe_control.policy import evaluate_contributors
from lethe_control.qdrant_store import QdrantStore
from lethe_control.vector_store import ChromaStore

DEMO_AGENT_ID = "agent_demo_01"
DEMO_POLICY_REF = "policy://demo/default"
DEMO_PURPOSE = "purpose://demo/qa"
FINGERPRINT_KEY_VERSION = "demo-v1"
CONNECTORS: dict[ObjectKind, str] = {
    ObjectKind.SOURCE: "connector://local-objects",
    ObjectKind.CHUNK: "connector://local-objects",
    ObjectKind.EMBEDDING: "connector://chroma",
    ObjectKind.CACHE: "connector://sqlite-cache",
    ObjectKind.SUMMARY: "connector://sqlite-summaries",
    ObjectKind.MEMORY: "connector://sqlite-memories",
}
REGISTERED_STORES = [
    "store://local-objects",
    "store://chroma",
    "store://sqlite-cache",
    "store://sqlite-summaries",
    "store://sqlite-memories",
]


def _utc_text(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def _json(value: Any) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json", by_alias=True)
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _hash_json(value: Any) -> str:
    return b64url_encode(hashlib.sha256(canonical_json_bytes(value)).digest())


class LetheService:
    def __init__(
        self,
        settings: Settings,
        *,
        clock: Clock | None = None,
        max_attempts: int = 3,
    ) -> None:
        self.settings = settings
        self.clock = clock or SystemClock()
        self.max_attempts = max_attempts
        self.settings.ensure_directories()
        self.state = StateStore(settings.database_path)
        self.objects = LocalObjectStore(settings.objects_dir)
        self.vectors: ChromaStore | QdrantStore
        if settings.vector_backend == "chroma":
            self.vectors = ChromaStore(settings.chroma_dir)
        elif settings.vector_backend == "qdrant":
            self.vectors = QdrantStore(settings.qdrant_url)
        else:
            raise ValueError(f"unknown vector backend: {settings.vector_backend}")
        vector_connector = f"connector://{settings.vector_backend}"
        self.connectors: dict[ObjectKind, str] = {
            **CONNECTORS,
            ObjectKind.EMBEDDING: vector_connector,
        }
        self.registered_stores: list[str] = [
            f"store://{settings.vector_backend}" if store == "store://chroma" else store
            for store in REGISTERED_STORES
        ]
        self.manifest = load_fixture_manifest()
        self.scope = ScopeKey(
            tenant_id=self.manifest["scope"]["tenant_id"],
            workspace_id=self.manifest["scope"]["workspace_id"],
            environment_id=self.manifest["scope"]["environment_id"],
        )
        self._fingerprint_key = derive_purpose_key(
            b"\x44" * 32,
            tenant_id=self.scope.tenant_id,
            key_version=FINGERPRINT_KEY_VERSION,
            purpose="content-fingerprint",
        )
        self._stop_worker = threading.Event()
        self._worker: threading.Thread | None = None
        self._validated_approval_runs: set[str] = set()
        self._run_events: dict[str, LifecycleEvent] = {}
        self._receipt_lock = threading.RLock()

    def initialize(self, *, seed_if_empty: bool = True) -> None:
        self.state.initialize()
        self._enroll_fixture_keys()
        self.reconcile_actions()
        self.reconcile_terminal_runs()
        if (
            seed_if_empty
            and self.state.get_knowledge_object(self.scope, "ver_demo_canary_001") is None
        ):
            self.seed_demo()

    def health(self) -> HealthResponse:
        checks: dict[str, str] = {}
        try:
            checks["sqlite"] = "ok" if self.state.schema_version() >= 1 else "failed"
        except Exception:
            checks["sqlite"] = "failed"
        try:
            self.clock.now()
            checks["clock"] = "ok"
        except Exception:
            checks["clock"] = "failed"
        try:
            self.vectors.inventory(
                tenant_id=self.scope.tenant_id,
                workspace_id=self.scope.workspace_id,
                environment_id=self.scope.environment_id,
            )
            checks["chroma"] = "ok"
        except Exception:
            checks["chroma"] = "failed"
        status = "ok" if all(value == "ok" for value in checks.values()) else "failed"
        return HealthResponse(status=status, checks=checks)  # type: ignore[arg-type]

    def reset_demo(self) -> None:
        self.stop_worker()
        self._validated_approval_runs.clear()
        self._run_events.clear()
        for suffix in ("", "-wal", "-shm"):
            path = Path(f"{self.settings.database_path}{suffix}")
            if path.exists():
                path.unlink()
        if self.settings.objects_dir.exists():
            shutil.rmtree(self.settings.objects_dir)
        self.settings.ensure_directories()
        self.objects = LocalObjectStore(self.settings.objects_dir)
        self.vectors.reset()
        self.state.initialize()
        self._enroll_fixture_keys()
        self.seed_demo()

    def _enroll_fixture_keys(self) -> None:
        enrollments = [
            load_key_enrollment("issuer_demo_01"),
            load_key_enrollment("key_demo_01"),
        ]
        with self.state.transaction() as connection:
            for enrollment in enrollments:
                role = (
                    "event_issuer"
                    if "event_issuer" in enrollment.allowed_roles
                    else "receipt_signer"
                )
                connection.execute(
                    """
                    INSERT OR IGNORE INTO key_enrollments (
                        tenant_id, workspace_id, environment_id, key_id, agent_id,
                        public_key, signing_role, status, valid_from, valid_until, enrolled_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        enrollment.tenant_id,
                        enrollment.workspace_id,
                        enrollment.environment_id,
                        enrollment.key_id,
                        enrollment.agent_id,
                        b64url_encode(enrollment.public_key),
                        role,
                        enrollment.status,
                        enrollment.valid_from,
                        enrollment.valid_until,
                        enrollment.valid_from,
                    ),
                )

    def _acl_policy(
        self,
        *,
        acl_ref: str,
        policy_version: int,
        allowed_principals: list[str],
        valid_until: datetime | None = None,
    ) -> ACLPolicy:
        now = _parse_time(self.manifest["clock"]["initial_time"])
        unsigned = {
            "schema_version": "1",
            **self.scope.model_dump(),
            "acl_ref": acl_ref,
            "policy_version": policy_version,
            "allowed_principal_refs": [f"principal://demo/{name}" for name in allowed_principals],
            "denied_principal_refs": [],
            "allowed_group_refs": [],
            "denied_group_refs": [],
            "permitted_purpose_refs": [DEMO_PURPOSE],
            "permitted_actions": [
                PolicyAction.RETRIEVE.value,
                PolicyAction.GENERATE.value,
                PolicyAction.CACHE.value,
                PolicyAction.READ.value,
            ],
            "valid_from": _utc_text(now),
            "valid_until": _utc_text(valid_until) if valid_until else None,
            "source_authority_ref": "authority://demo/source-admin",
            "group_snapshot_version": "groups-demo-v1",
            "group_snapshot_expires_at": "2098-01-01T00:00:00Z",
            "created_at": _utc_text(now),
        }
        approval_key = load_demo_key_fixture("mutation_approver")
        signature = sign_acl_policy(unsigned, approval_key.private_seed)
        return ACLPolicy.model_validate(unsigned | {"signature": signature})

    def _store_acl(self, policy: ACLPolicy, connection: sqlite3.Connection | None = None) -> None:
        if connection is not None:
            connection.execute(
                """
                DELETE FROM acl_policies
                WHERE tenant_id=? AND workspace_id=? AND environment_id=?
                  AND acl_ref=? AND policy_version=?
                """,
                (*self._scope_values(policy.scope), policy.acl_ref, policy.policy_version),
            )
            self.state.insert_acl_policy(connection, policy)
            return
        with self.state.transaction() as transaction:
            self._store_acl(policy, transaction)

    def seed_demo(self) -> None:
        if self.state.get_knowledge_object(self.scope, "ver_demo_canary_001") is not None:
            return
        initial = _parse_time(self.manifest["clock"]["initial_time"])
        canary_until = _parse_time(self.manifest["clock"]["canary_source_valid_until"])
        parent_until = _parse_time(self.manifest["clock"]["policy_parent_valid_until"])
        with self.state.transaction() as connection:
            self.state.insert_acl_policy(
                connection,
                self._acl_policy(
                    acl_ref="acl://demo/nightjar/1",
                    policy_version=1,
                    allowed_principals=["alice", "bob"],
                    valid_until=canary_until,
                ),
            )
            self.state.insert_acl_policy(
                connection,
                self._acl_policy(
                    acl_ref="acl://demo/nightjar-policy/1",
                    policy_version=1,
                    allowed_principals=["alice"],
                    valid_until=parent_until,
                ),
            )

        canary = (
            Path(__file__).resolve().parents[2] / "fixtures/documents/canary_source.md"
        ).read_text()
        policy_parent = (
            Path(__file__).resolve().parents[2] / "fixtures/documents/policy_parent.md"
        ).read_text()
        canary_envelope = self._publish_source(
            object_id="obj_demo_canary",
            version_id="ver_demo_canary_001",
            text=canary,
            acl_ref="acl://demo/nightjar/1",
            policy_version=1,
            valid_from=initial,
            valid_until=canary_until,
            supersedes=None,
        )
        parent_envelope = self._publish_source(
            object_id="obj_demo_policy_parent",
            version_id="ver_demo_policy_parent_001",
            text=policy_parent,
            acl_ref="acl://demo/nightjar-policy/1",
            policy_version=1,
            valid_from=initial,
            valid_until=parent_until,
            supersedes=None,
        )
        self._derive_source(canary_envelope, canary, full=True)
        self._derive_source(parent_envelope, policy_parent, full=False)
        self._publish_multi_parent_summary(canary_envelope, parent_envelope)
        self._seed_untracked_objects(canary)

    def _fingerprint(self, kind: ObjectKind, payload: Any) -> str:
        kwargs: dict[str, Any] = {}
        if kind is ObjectKind.EMBEDDING:
            kwargs = {
                "embedding_model": "signed-feature-hash",
                "embedding_version": "signed-feature-hash-v1",
            }
        return content_fingerprint(
            kind=kind.value,
            key_version=FINGERPRINT_KEY_VERSION,
            tenant_key=self._fingerprint_key,
            payload=payload,
            **kwargs,
        )

    def _envelope(
        self,
        *,
        object_id: str,
        version_id: str,
        kind: ObjectKind,
        parents: list[str],
        roots: list[str],
        acl_ref: str,
        policy_version: int,
        valid_from: datetime,
        valid_until: datetime | None,
        content_ref: str | None,
        payload: Any,
        activity: str,
        supersedes: str | None = None,
    ) -> KnowledgeEnvelope:
        return KnowledgeEnvelope(
            **self.scope.model_dump(),
            object_id=object_id,
            version_id=version_id,
            kind=kind,
            parent_version_ids=parents,
            root_version_ids=roots,
            lifecycle_state=LifecycleState.ACTIVE,
            created_at=valid_from,
            valid_from=valid_from,
            valid_until=valid_until,
            policy_ref=DEMO_POLICY_REF,
            policy_version=policy_version,
            acl_ref=acl_ref,
            supersedes_version_id=supersedes,
            content_ref=content_ref,
            local_content_fingerprint=self._fingerprint(kind, payload),
            provenance=Provenance(
                activity_type=activity,
                connector_ref=self.connectors[kind],
                run_id="run_demo_seed",
            ),
            governance=GovernanceAnnotations(purpose_refs=[DEMO_PURPOSE]),
        )

    def _assert_publishable_roots(self, envelope: KnowledgeEnvelope) -> None:
        now = self.clock.now()
        approval_key = load_demo_key_fixture("mutation_approver")
        for root_version_id in envelope.root_version_ids:
            root = self.state.get_knowledge_object(envelope.scope, root_version_id)
            if (
                root is None
                or root.lifecycle_state is not LifecycleState.ACTIVE
                or now < root.valid_from
                or (root.valid_until is not None and now >= root.valid_until)
                or self.state.has_tombstone(envelope.scope, root_version_id)
            ):
                raise ConflictError(
                    "publish_root_denied", "derivative root is not currently publishable"
                )
            policy_row = self.state.fetch_one(
                """
                SELECT * FROM acl_policies
                WHERE tenant_id=? AND workspace_id=? AND environment_id=?
                  AND acl_ref=? AND policy_version=?
                """,
                (
                    *self._scope_values(envelope.scope),
                    root.acl_ref,
                    root.policy_version,
                ),
            )
            if policy_row is None:
                raise ConflictError("publish_policy_stale", "derivative root policy is unavailable")
            policy = self._acl_from_row(policy_row)
            if not verify_acl_policy_signature(
                policy.model_dump(mode="json"), approval_key.public_key
            ):
                raise ConflictError(
                    "publish_policy_invalid", "derivative root policy signature is invalid"
                )

    def _publish_source(
        self,
        *,
        object_id: str,
        version_id: str,
        text: str,
        acl_ref: str,
        policy_version: int,
        valid_from: datetime,
        valid_until: datetime,
        supersedes: str | None,
    ) -> KnowledgeEnvelope:
        fingerprint = self._fingerprint(ObjectKind.SOURCE, text)
        resurrection = self.state.fetch_one(
            """
            SELECT target_version_id FROM tombstones
            WHERE tenant_id=? AND workspace_id=? AND environment_id=?
              AND object_kind='source' AND fingerprint_key_version=?
              AND local_content_fingerprint=?
            LIMIT 1
            """,
            (
                *self._scope_values(self.scope),
                FINGERPRINT_KEY_VERSION,
                fingerprint,
            ),
        )
        if resurrection is not None:
            raise ConflictError(
                "resurrection_quarantined",
                "source fingerprint matches a durable tombstone",
            )
        content_ref = self.objects.content_ref_for(
            **self.scope.model_dump(), kind=ObjectKind.SOURCE.value, storage_id=version_id
        )
        envelope = self._envelope(
            object_id=object_id,
            version_id=version_id,
            kind=ObjectKind.SOURCE,
            parents=[],
            roots=[version_id],
            acl_ref=acl_ref,
            policy_version=policy_version,
            valid_from=valid_from,
            valid_until=valid_until,
            content_ref=content_ref,
            payload=text,
            activity="ingest",
            supersedes=supersedes,
        )
        existing = self.state.get_knowledge_object(self.scope, version_id)
        if existing is None:
            self.state.store_knowledge_object(envelope)
        elif existing.model_dump(mode="json") != envelope.model_dump(mode="json"):
            raise ConflictError("immutable_version_conflict", "source version already differs")
        self._assert_publishable_roots(envelope)
        self.objects.put(
            **self.scope.model_dump(),
            kind=ObjectKind.SOURCE.value,
            storage_id=version_id,
            payload=text.encode(),
        )
        return envelope

    def _publish_object_payload(self, envelope: KnowledgeEnvelope, payload: str) -> None:
        existing = self.state.get_knowledge_object(envelope.scope, envelope.version_id)
        if existing is None:
            self.state.store_knowledge_object(envelope)
        elif existing.model_dump(mode="json") != envelope.model_dump(mode="json"):
            raise ConflictError("immutable_version_conflict", "derived version already differs")
        self._assert_publishable_roots(envelope)
        self.objects.put(
            **envelope.scope.model_dump(),
            kind=envelope.kind.value,
            storage_id=envelope.version_id,
            payload=payload.encode(),
        )

    def _publish_sql_payload(
        self,
        envelope: KnowledgeEnvelope,
        *,
        table: str,
        payload: str,
        cache_key: str | None = None,
    ) -> None:
        existing = self.state.get_knowledge_object(envelope.scope, envelope.version_id)
        if existing is not None and existing.model_dump(mode="json") != envelope.model_dump(
            mode="json"
        ):
            raise ConflictError("immutable_version_conflict", "derived version already differs")
        if existing is None:
            self.state.store_knowledge_object(envelope)
        self._assert_publishable_roots(envelope)
        with self.state.transaction() as connection:
            if table == "cache_entries":
                connection.executemany(
                    """
                    INSERT OR IGNORE INTO cache_entries (
                        tenant_id, workspace_id, environment_id, cache_key, version_id,
                        principal_ref, policy_version, payload, created_at, expires_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            *self._scope_values(envelope.scope),
                            principal_cache_key,
                            envelope.version_id,
                            principal_ref,
                            envelope.policy_version,
                            payload,
                            _utc_text(envelope.created_at),
                            _utc_text(envelope.valid_until) if envelope.valid_until else None,
                        )
                        for principal_cache_key, principal_ref in (
                            (cache_key, "principal://demo/alice"),
                            (
                                stable_id("cache", envelope.version_id, "bob"),
                                "principal://demo/bob",
                            ),
                        )
                    ],
                )
            else:
                connection.execute(
                    f"""
                    INSERT OR IGNORE INTO {table} (
                        tenant_id, workspace_id, environment_id,
                        version_id, payload, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        *self._scope_values(envelope.scope),
                        envelope.version_id,
                        payload,
                        _utc_text(envelope.created_at),
                    ),
                )

    def _derive_source(self, source: KnowledgeEnvelope, text: str, *, full: bool) -> None:
        chunks = chunk_text(
            source.version_id,
            text,
            size=int(self.manifest["chunking"]["size"]),
            overlap=int(self.manifest["chunking"]["overlap"]),
        )
        selected_chunks = chunks if full else chunks[:1]
        for chunk in selected_chunks:
            content_ref = self.objects.content_ref_for(
                **source.scope.model_dump(),
                kind=ObjectKind.CHUNK.value,
                storage_id=chunk.version_id,
            )
            chunk_envelope = self._envelope(
                object_id=chunk.object_id,
                version_id=chunk.version_id,
                kind=ObjectKind.CHUNK,
                parents=[source.version_id],
                roots=source.root_version_ids,
                acl_ref=source.acl_ref,
                policy_version=source.policy_version,
                valid_from=source.valid_from,
                valid_until=source.valid_until,
                content_ref=content_ref,
                payload=chunk.text,
                activity="chunk",
            )
            self._publish_object_payload(chunk_envelope, chunk.text)
            vector = list(feature_hash_embedding(chunk.text))
            vector_version = stable_id("ver", chunk.version_id, "embedding")
            vector_envelope = self._envelope(
                object_id=stable_id("obj", chunk.object_id, "embedding"),
                version_id=vector_version,
                kind=ObjectKind.EMBEDDING,
                parents=[chunk.version_id],
                roots=source.root_version_ids,
                acl_ref=source.acl_ref,
                policy_version=source.policy_version,
                valid_from=source.valid_from,
                valid_until=source.valid_until,
                content_ref=(
                    "chroma://"
                    + self.vectors.physical_id(
                        **source.scope.model_dump(), version_id=vector_version
                    )
                ),
                payload=vector,
                activity="embed",
            )
            existing_vector = self.state.get_knowledge_object(
                source.scope, vector_envelope.version_id
            )
            if existing_vector is None:
                self.state.store_knowledge_object(vector_envelope)
            elif existing_vector.model_dump(mode="json") != vector_envelope.model_dump(mode="json"):
                raise ConflictError(
                    "immutable_version_conflict", "embedding version already differs"
                )
            self._assert_publishable_roots(vector_envelope)
            self.vectors.upsert(
                **source.scope.model_dump(),
                version_id=vector_version,
                embedding=vector,
                metadata={
                    **source.scope.model_dump(),
                    "object_id": vector_envelope.object_id,
                    "version_id": vector_version,
                    "content_version_id": chunk.version_id,
                    "root_version_ids": source.root_version_ids,
                    "policy_ref": source.policy_ref,
                    "policy_version": source.policy_version,
                    "lifecycle_state": LifecycleState.ACTIVE.value,
                    "acl_ref": source.acl_ref,
                    "valid_until": _utc_text(source.valid_until) if source.valid_until else "",
                    "derivative_kind": ObjectKind.EMBEDDING.value,
                },
            )

        if not full:
            return
        summary = summarize_text(text)
        summary_version = stable_id("ver", source.version_id, "summary")
        summary_envelope = self._envelope(
            object_id=stable_id("obj", source.object_id, "summary"),
            version_id=summary_version,
            kind=ObjectKind.SUMMARY,
            parents=[source.version_id],
            roots=source.root_version_ids,
            acl_ref=source.acl_ref,
            policy_version=source.policy_version,
            valid_from=source.valid_from,
            valid_until=source.valid_until,
            content_ref=f"sqlite://summaries/{summary_version}",
            payload=summary,
            activity="summarize",
        )
        self._publish_sql_payload(summary_envelope, table="summaries", payload=summary)

        memories = memory_from_text(text)
        for ordinal, memory in enumerate(memories[:3]):
            memory_version = stable_id("ver", source.version_id, "memory", ordinal)
            memory_envelope = self._envelope(
                object_id=stable_id("obj", source.object_id, "memory", ordinal),
                version_id=memory_version,
                kind=ObjectKind.MEMORY,
                parents=[source.version_id],
                roots=source.root_version_ids,
                acl_ref=source.acl_ref,
                policy_version=source.policy_version,
                valid_from=source.valid_from,
                valid_until=source.valid_until,
                content_ref=f"sqlite://memories/{memory_version}",
                payload=memory,
                activity="remember",
            )
            self._publish_sql_payload(memory_envelope, table="memories", payload=memory)

        cache_payload = _json(
            {
                "answer": answer_from_contexts("What is the canary phrase?", [text]),
                "root_version_ids": source.root_version_ids,
            }
        )
        cache_version = stable_id("ver", source.version_id, "cache")
        cache_envelope = self._envelope(
            object_id=stable_id("obj", source.object_id, "cache"),
            version_id=cache_version,
            kind=ObjectKind.CACHE,
            parents=[selected_chunks[0].version_id],
            roots=source.root_version_ids,
            acl_ref=source.acl_ref,
            policy_version=source.policy_version,
            valid_from=source.valid_from,
            valid_until=source.valid_until,
            content_ref=f"sqlite://cache/{cache_version}",
            payload=json.loads(cache_payload),
            activity="cache",
        )
        self._publish_sql_payload(
            cache_envelope,
            table="cache_entries",
            payload=cache_payload,
            cache_key=stable_id("cache", source.version_id, "alice"),
        )

    def _publish_multi_parent_summary(
        self, source: KnowledgeEnvelope, policy_parent: KnowledgeEnvelope
    ) -> None:
        text = (
            Path(__file__).resolve().parents[2] / "fixtures/documents/multi_parent_summary.md"
        ).read_text()
        if source.valid_until is None or policy_parent.valid_until is None:
            raise ValueError("multi-parent fixture requires bounded expiry")
        version_id = "ver_demo_multi_parent_summary_001"
        envelope = self._envelope(
            object_id="obj_demo_multi_parent_summary",
            version_id=version_id,
            kind=ObjectKind.SUMMARY,
            parents=[source.version_id, policy_parent.version_id],
            roots=[source.version_id, policy_parent.version_id],
            acl_ref=policy_parent.acl_ref,
            policy_version=policy_parent.policy_version,
            valid_from=max(source.valid_from, policy_parent.valid_from),
            valid_until=min(source.valid_until, policy_parent.valid_until),
            content_ref=f"sqlite://summaries/{version_id}",
            payload=text,
            activity="summarize_multi_parent",
        )
        self._publish_sql_payload(envelope, table="summaries", payload=text)

    def _seed_untracked_objects(self, canary: str) -> None:
        seeded: list[dict[str, str]] = []
        payloads = [
            ("exact_positive", canary),
            ("exact_positive", canary.replace("\n", "\r\n")),
            (
                "semantic_positive",
                "Nightjar recovery note: the amber lantern number 731 is the retired canary.",
            ),
            (
                "semantic_positive",
                "Archive drill mentions Project Nightjar and its prohibited "
                "amber-lantern-731 marker.",
            ),
            ("negative", "Public weather report: clear skies and low wind."),
            ("negative", "Cafeteria menu lists lentil soup and rice."),
            ("negative", "Engineering handbook describes code review etiquette."),
            ("negative", "Gardening notes recommend watering basil at dawn."),
        ]
        for ordinal, (label, payload) in enumerate(payloads):
            ref = self.objects.put(
                **self.scope.model_dump(),
                kind=ObjectKind.SOURCE.value,
                storage_id=f"untracked-{label}-{ordinal}",
                payload=payload.encode(),
            )
            seeded.append({"content_ref": ref, "label": label})
        labels_path = self.settings.state_dir / "seeded-inventory.json"
        labels_path.write_text(_json({"records": seeded}), encoding="utf-8")

    @staticmethod
    def _scope_values(scope: ScopeKey) -> tuple[str, str, str]:
        return scope.tenant_id, scope.workspace_id, scope.environment_id

    def _acl_from_row(self, row: sqlite3.Row) -> ACLPolicy:
        return ACLPolicy.model_validate(
            {
                "schema_version": row["schema_version"],
                "tenant_id": row["tenant_id"],
                "workspace_id": row["workspace_id"],
                "environment_id": row["environment_id"],
                "acl_ref": row["acl_ref"],
                "policy_version": row["policy_version"],
                "allowed_principal_refs": json.loads(row["allowed_principal_refs_json"]),
                "denied_principal_refs": json.loads(row["denied_principal_refs_json"]),
                "allowed_group_refs": json.loads(row["allowed_group_refs_json"]),
                "denied_group_refs": json.loads(row["denied_group_refs_json"]),
                "permitted_purpose_refs": json.loads(row["permitted_purpose_refs_json"]),
                "permitted_actions": json.loads(row["permitted_actions_json"]),
                "valid_from": row["valid_from"],
                "valid_until": row["valid_until"],
                "source_authority_ref": row["source_authority_ref"],
                "group_snapshot_version": row["group_snapshot_version"],
                "group_snapshot_expires_at": row["group_snapshot_expires_at"],
                "created_at": row["created_at"],
                "signature": row["signature"],
            }
        )

    def _contributors(self, scope: ScopeKey, version_id: str) -> list[KnowledgeEnvelope]:
        rows = self.state.fetch_all(
            """
            WITH RECURSIVE ancestors(version_id) AS (
                SELECT ?
                UNION
                SELECT edge.parent_version_id
                FROM lineage_edges AS edge
                JOIN ancestors AS child ON edge.child_version_id = child.version_id
                WHERE edge.tenant_id = ? AND edge.workspace_id = ? AND edge.environment_id = ?
            )
            SELECT object.* FROM knowledge_objects AS object
            JOIN ancestors USING (version_id)
            WHERE object.tenant_id = ? AND object.workspace_id = ? AND object.environment_id = ?
            ORDER BY object.version_id
            """,
            (version_id, *self._scope_values(scope), *self._scope_values(scope)),
        )
        return [self.state._knowledge_from_row(row) for row in rows]

    def gate_version(
        self,
        *,
        scope: ScopeKey,
        version_id: str,
        principal_ref: str,
        purpose_ref: str | None,
    ) -> tuple[bool, tuple[str, ...]]:
        contributors = self._contributors(scope, version_id)
        if not contributors:
            return False, ("missing_lineage",)
        policy_rows = self.state.fetch_all(
            """
            SELECT * FROM acl_policies
            WHERE tenant_id=? AND workspace_id=? AND environment_id=?
            """,
            self._scope_values(scope),
        )
        policies = {
            (policy.acl_ref, policy.policy_version): policy
            for policy in (self._acl_from_row(row) for row in policy_rows)
        }
        approval_key = load_demo_key_fixture("mutation_approver")
        signature_validity = {
            key: verify_acl_policy_signature(
                policy.model_dump(mode="json"), approval_key.public_key
            )
            for key, policy in policies.items()
        }
        tombstones = {
            str(row["target_version_id"])
            for row in self.state.fetch_all(
                """
                SELECT target_version_id FROM tombstones
                WHERE tenant_id=? AND workspace_id=? AND environment_id=?
                """,
                self._scope_values(scope),
            )
        }
        superseded = {
            str(row["supersedes_version_id"])
            for row in self.state.fetch_all(
                """
                SELECT supersedes_version_id FROM knowledge_objects
                WHERE tenant_id=? AND workspace_id=? AND environment_id=?
                  AND supersedes_version_id IS NOT NULL
                """,
                self._scope_values(scope),
            )
        }
        decision = evaluate_contributors(
            envelopes=contributors,
            policies=policies,
            principal_ref=principal_ref,
            purpose_ref=purpose_ref or DEMO_PURPOSE,
            action=PolicyAction.RETRIEVE,
            now=self.clock.now(),
            tombstoned_version_ids=tombstones,
            superseded_version_ids=superseded,
            signature_validity=signature_validity,
        )
        return decision.allowed, decision.reason_codes

    def _allowed_metadata_policy_bindings(
        self, *, principal_ref: str, purpose_ref: str | None
    ) -> list[tuple[str, int]]:
        """Resolve caller-safe Chroma ACL filters without replacing the SQLite gate.

        ACL member identities stay in SQLite. Chroma receives only the opaque ACL
        reference and policy version, and every returned candidate is still checked
        by ``gate_version`` before its payload is read and before answer release.
        """

        rows = self.state.fetch_all(
            """
            SELECT * FROM acl_policies
            WHERE tenant_id=? AND workspace_id=? AND environment_id=?
            """,
            self._scope_values(self.scope),
        )
        approval_key = load_demo_key_fixture("mutation_approver")
        now = self.clock.now()
        requested_purpose = purpose_ref or DEMO_PURPOSE
        bindings: set[tuple[str, int]] = set()
        for row in rows:
            policy = self._acl_from_row(row)
            if not verify_acl_policy_signature(
                policy.model_dump(mode="json"), approval_key.public_key
            ):
                continue
            if now < policy.valid_from or (
                policy.valid_until is not None and now >= policy.valid_until
            ):
                continue
            if now >= policy.group_snapshot_expires_at:
                continue
            if principal_ref in policy.denied_principal_refs:
                continue
            if principal_ref not in policy.allowed_principal_refs:
                continue
            if PolicyAction.RETRIEVE not in policy.permitted_actions:
                continue
            if (
                policy.permitted_purpose_refs
                and requested_purpose not in policy.permitted_purpose_refs
            ):
                continue
            bindings.add((policy.acl_ref, policy.policy_version))
        return sorted(bindings)

    def query(self, request: QueryRequest, *, principal_ref: str) -> QueryResponse:
        policy_bindings = self._allowed_metadata_policy_bindings(
            principal_ref=principal_ref,
            purpose_ref=request.purpose_ref,
        )
        matches = self.vectors.query(
            embedding=list(feature_hash_embedding(request.query)),
            **self.scope.model_dump(),
            limit=8,
            active_only=True,
            valid_after=self.clock.now(),
            allowed_policy_bindings=policy_bindings,
        )
        contexts: list[str] = []
        supporting: list[str] = []
        denial_reasons: set[str] = set()
        for _, distance, metadata in matches:
            if distance > 0.8:
                continue
            chunk_version = metadata.get("content_version_id")
            if not isinstance(chunk_version, str):
                denial_reasons.add("missing_lineage")
                continue
            allowed, reasons = self.gate_version(
                scope=self.scope,
                version_id=chunk_version,
                principal_ref=principal_ref,
                purpose_ref=request.purpose_ref,
            )
            if not allowed:
                denial_reasons.update(reasons)
                continue
            envelope = self.state.get_knowledge_object(self.scope, chunk_version)
            if envelope is None or envelope.content_ref is None:
                denial_reasons.add("missing_payload")
                continue
            try:
                contexts.append(self.objects.get(envelope.content_ref).decode("utf-8"))
                supporting.append(chunk_version)
            except (FileNotFoundError, UnicodeDecodeError):
                denial_reasons.add("missing_payload")

        if not contexts and denial_reasons:
            return QueryResponse(
                answer=None,
                supporting_version_ids=[],
                denied=True,
                reason_codes=sorted(denial_reasons),
            )
        if not contexts:
            return QueryResponse(
                answer="No matching authorized context.",
                supporting_version_ids=[],
                denied=False,
                reason_codes=["no_matching_context"],
            )

        # Re-evaluate every contributor immediately before answer release.
        for version_id in supporting:
            allowed, reasons = self.gate_version(
                scope=self.scope,
                version_id=version_id,
                principal_ref=principal_ref,
                purpose_ref=request.purpose_ref,
            )
            if not allowed:
                return QueryResponse(
                    answer=None,
                    supporting_version_ids=[],
                    denied=True,
                    reason_codes=list(reasons),
                )
        return QueryResponse(
            answer=answer_from_contexts(request.query, contexts),
            supporting_version_ids=supporting,
            denied=False,
            reason_codes=[],
        )

    def unsafe_query(self, request: QueryRequest) -> QueryResponse:
        matches = self.vectors.query(
            embedding=list(feature_hash_embedding(request.query)),
            **self.scope.model_dump(),
            limit=8,
        )
        contexts: list[str] = []
        supporting: list[str] = []
        for _, distance, metadata in matches:
            if distance > 0.8:
                continue
            chunk_version = metadata.get("content_version_id")
            if not isinstance(chunk_version, str):
                continue
            envelope = self.state.get_knowledge_object(self.scope, chunk_version)
            if envelope is None or envelope.content_ref is None:
                continue
            try:
                contexts.append(self.objects.get(envelope.content_ref).decode("utf-8"))
                supporting.append(chunk_version)
            except (FileNotFoundError, UnicodeDecodeError):
                continue
        if not contexts:
            return QueryResponse(
                answer="No matching context.",
                supporting_version_ids=[],
                denied=False,
                reason_codes=["no_matching_context", "unsafe_demo_bypass"],
            )
        return QueryResponse(
            answer=answer_from_contexts(request.query, contexts),
            supporting_version_ids=supporting,
            denied=False,
            reason_codes=["unsafe_demo_bypass"],
        )

    def delete_source_only_control(self, version_id: str = "ver_demo_canary_001") -> bool:
        envelope = self.state.get_knowledge_object(self.scope, version_id)
        if envelope is None or envelope.content_ref is None:
            raise NotFoundError("source_not_found", "source version not found")
        return self.objects.delete(envelope.content_ref)

    def graph(self, scope: ScopeKey, version_id: str) -> GraphResponse:
        requested = self.state.get_knowledge_object(scope, version_id)
        if requested is None:
            raise NotFoundError("version_not_found", "knowledge version not found")
        descendants = self.state.descendant_version_ids(scope, version_id, include_self=True)
        roots = requested.root_version_ids
        version_ids = sorted(set(descendants) | set(roots))
        placeholders = ",".join("?" for _ in version_ids)
        rows = self.state.fetch_all(
            f"""
            SELECT * FROM knowledge_objects
            WHERE tenant_id=? AND workspace_id=? AND environment_id=?
              AND version_id IN ({placeholders})
            ORDER BY version_id
            """,
            (*self._scope_values(scope), *version_ids),
        )
        edges = self.state.fetch_all(
            f"""
            SELECT parent_version_id, child_version_id FROM lineage_edges
            WHERE tenant_id=? AND workspace_id=? AND environment_id=?
              AND (parent_version_id IN ({placeholders}) OR child_version_id IN ({placeholders}))
            ORDER BY parent_version_id, child_version_id
            """,
            (*self._scope_values(scope), *version_ids, *version_ids),
        )
        return GraphResponse(
            **scope.model_dump(),
            requested_version_id=version_id,
            root_version_ids=roots,
            nodes=[
                GraphNode(
                    version_id=row["version_id"],
                    object_id=row["object_id"],
                    kind=row["kind"],
                    lifecycle_state=row["lifecycle_state"],
                )
                for row in rows
            ],
            edges=[
                GraphEdge(
                    parent_version_id=row["parent_version_id"],
                    child_version_id=row["child_version_id"],
                )
                for row in edges
            ],
        )

    def _payload_for_envelope(self, envelope: KnowledgeEnvelope) -> str | None:
        if envelope.kind in {ObjectKind.SOURCE, ObjectKind.CHUNK}:
            if envelope.content_ref is None or not self.objects.exists(envelope.content_ref):
                return None
            return self.objects.get(envelope.content_ref).decode("utf-8")
        table = {
            ObjectKind.SUMMARY: "summaries",
            ObjectKind.MEMORY: "memories",
            ObjectKind.CACHE: "cache_entries",
        }.get(envelope.kind)
        if table is None:
            return None
        row = self.state.fetch_one(
            f"""
            SELECT payload FROM {table}
            WHERE tenant_id=? AND workspace_id=? AND environment_id=? AND version_id=?
            """,
            (*self._scope_values(envelope.scope), envelope.version_id),
        )
        return str(row["payload"]) if row else None

    def _vector_metadata_matches(
        self, version_id: str, *, acl_ref: str, policy_version: int
    ) -> bool:
        records = dict(self.vectors.inventory(**self.scope.model_dump()))
        metadata = records.get(version_id)
        return bool(
            metadata
            and metadata.get("acl_ref") == acl_ref
            and metadata.get("policy_version") == policy_version
            and metadata.get("lifecycle_state") == LifecycleState.ACTIVE.value
        )

    def scan(self, request: ScanRequest) -> ScanResponse:
        if request.scope != self.scope:
            raise AuthorizationError("scope_not_active", "scanner scope is not active")
        roots = request.root_version_ids or ["ver_demo_canary_001"]
        tracked_ids: set[str] = set()
        for root in roots:
            tracked_ids.update(
                self.state.descendant_version_ids(self.scope, root, include_self=True)
            )
        tracked = [
            envelope
            for version_id in sorted(tracked_ids)
            if (envelope := self.state.get_knowledge_object(self.scope, version_id)) is not None
        ]
        now = self.clock.now()
        scan_id = stable_id("scan", _utc_text(now), *roots)
        object_inventory = tuple(self.objects.inventory(**self.scope.model_dump()))
        object_payloads = {content_ref: payload for content_ref, payload in object_inventory}
        vector_versions = {
            str(metadata.get("version_id", physical_id))
            for physical_id, metadata in self.vectors.inventory(**self.scope.model_dump())
        }
        sql_versions: dict[ObjectKind, set[str]] = {}
        for kind, table in (
            (ObjectKind.CACHE, "cache_entries"),
            (ObjectKind.SUMMARY, "summaries"),
            (ObjectKind.MEMORY, "memories"),
        ):
            rows = self.state.fetch_all(
                f"""
                SELECT DISTINCT version_id FROM {table}
                WHERE tenant_id=? AND workspace_id=? AND environment_id=?
                """,
                self._scope_values(self.scope),
            )
            sql_versions[kind] = {str(row["version_id"]) for row in rows}

        def physically_present(envelope: KnowledgeEnvelope) -> bool:
            if envelope.kind in {ObjectKind.SOURCE, ObjectKind.CHUNK}:
                return bool(envelope.content_ref and envelope.content_ref in object_payloads)
            if envelope.kind is ObjectKind.EMBEDDING:
                return envelope.version_id in vector_versions
            return envelope.version_id in sql_versions.get(envelope.kind, set())

        findings: list[ScanFinding] = []
        for envelope in tracked:
            if not physically_present(envelope):
                continue
            findings.append(
                ScanFinding(
                    finding_id=stable_id("finding", scan_id, envelope.version_id, "tracked"),
                    finding_class=FindingClass.TRACKED,
                    derivative_kind=envelope.kind,
                    connector_ref=self.connectors[envelope.kind],
                    target_version_id=envelope.version_id,
                    confidence=1.0,
                )
            )

        tracked_refs = {
            envelope.content_ref
            for envelope in tracked
            if envelope.content_ref and envelope.kind in {ObjectKind.SOURCE, ObjectKind.CHUNK}
        }
        fingerprint_map: dict[tuple[ObjectKind, str], str] = {}
        known_texts: list[str] = []
        for envelope in tracked:
            if envelope.local_content_fingerprint:
                fingerprint_map[(envelope.kind, envelope.local_content_fingerprint)] = (
                    envelope.version_id
                )
            payload = (
                self._payload_for_envelope(envelope)
                if envelope.kind not in {ObjectKind.CACHE, ObjectKind.EMBEDDING}
                else None
            )
            if payload:
                known_texts.append(payload)

        threshold = float(self.manifest["scanner"]["semantic_threshold"])
        for content_ref, payload_bytes in object_inventory:
            if content_ref in tracked_refs:
                continue
            try:
                payload = payload_bytes.decode("utf-8")
            except UnicodeDecodeError:
                continue
            exact_kind: ObjectKind | None = None
            exact_target: str | None = None
            for kind in (
                ObjectKind.SOURCE,
                ObjectKind.CHUNK,
                ObjectKind.SUMMARY,
                ObjectKind.MEMORY,
            ):
                target = fingerprint_map.get((kind, self._fingerprint(kind, payload)))
                if target:
                    exact_kind, exact_target = kind, target
                    break
            opaque_ref = stable_id("untracked", content_ref)
            if exact_kind is not None:
                findings.append(
                    ScanFinding(
                        finding_id=stable_id("finding", scan_id, content_ref, "exact"),
                        finding_class=FindingClass.EXACT_UNTRACKED,
                        derivative_kind=exact_kind,
                        connector_ref=self.connectors[ObjectKind.SOURCE],
                        target_version_id=exact_target,
                        confidence=1.0,
                    )
                )
                continue
            scores = [semantic_score(payload, known) for known in known_texts]
            normalized_marker = payload.casefold().replace("-", " ")
            marker_match = all(token in normalized_marker for token in ("amber", "lantern", "731"))
            confidence = max(scores, default=0.0)
            if marker_match:
                confidence = max(confidence, 0.95)
            if confidence >= threshold:
                findings.append(
                    ScanFinding(
                        finding_id=stable_id("finding", scan_id, content_ref, "semantic"),
                        finding_class=FindingClass.SEMANTIC_CANDIDATE,
                        derivative_kind=ObjectKind.SOURCE,
                        connector_ref=self.connectors[ObjectKind.SOURCE],
                        target_version_id=opaque_ref,
                        confidence=confidence,
                    )
                )

        with self.state.transaction() as connection:
            for finding in findings:
                connection.execute(
                    """
                    INSERT OR REPLACE INTO scanner_findings (
                        tenant_id, workspace_id, environment_id, finding_id, scan_id,
                        finding_class, derivative_kind, connector_ref, target_version_id,
                        confidence, disposition, found_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'reported', ?)
                    """,
                    (
                        *self._scope_values(self.scope),
                        finding.finding_id,
                        scan_id,
                        finding.finding_class.value,
                        finding.derivative_kind.value,
                        finding.connector_ref,
                        finding.target_version_id,
                        finding.confidence,
                        _utc_text(now),
                    ),
                )
        return ScanResponse(
            **self.scope.model_dump(),
            scan_id=scan_id,
            findings=findings,
            denominator=len(tracked),
            completed_at=now,
        )

    def assess(self, request: ScanRequest) -> PostureAssessmentReport:
        from lethe_control.assessment import run_posture_assessment

        return run_posture_assessment(self, request)

    def _event_from_row(self, row: sqlite3.Row) -> LifecycleEvent:
        return LifecycleEvent.model_validate(
            {
                "schema_version": row["schema_version"],
                "tenant_id": row["tenant_id"],
                "workspace_id": row["workspace_id"],
                "environment_id": row["environment_id"],
                "event_id": row["event_id"],
                "idempotency_key": row["idempotency_key"],
                "issuer_key_id": row["issuer_key_id"],
                "audience": row["audience"],
                "authority_ref": row["authority_ref"],
                "nonce": row["nonce"],
                "target_version_id": row["target_version_id"],
                "event_type": row["event_type"],
                "source_sequence": row["source_sequence"],
                "policy_version": row["policy_version"],
                "occurred_at": row["occurred_at"],
                "effective_at": row["effective_at"],
                "command_expires_at": row["command_expires_at"],
                "actor_ref": row["actor_ref"],
                "reason_code": row["reason_code"],
                "correction": json.loads(row["correction_json"])
                if row["correction_json"]
                else None,
                "permission_change": json.loads(row["permission_change_json"])
                if row["permission_change_json"]
                else None,
            }
        )

    def _event_for_run(self, run_id: str) -> LifecycleEvent:
        cached = self._run_events.get(run_id)
        if cached is not None:
            return cached
        row = self.state.fetch_one(
            """
            SELECT event.* FROM lifecycle_events AS event
            JOIN propagation_runs AS run
              ON run.tenant_id=event.tenant_id
             AND run.workspace_id=event.workspace_id
             AND run.environment_id=event.environment_id
             AND run.event_id=event.event_id
            WHERE run.tenant_id=? AND run.workspace_id=? AND run.environment_id=?
              AND run.run_id=?
            """,
            (*self._scope_values(self.scope), run_id),
        )
        if row is None:
            raise RuntimeError("run_event_missing")
        event = self._event_from_row(row)
        self._run_events[run_id] = event
        return event

    def _action_code(self, event_type: EventType, kind: ObjectKind) -> ActionCode:
        if event_type is EventType.PERMISSION_CHANGE:
            return ActionCode.EVICT if kind is ObjectKind.CACHE else ActionCode.UPDATE_ACL
        return ActionCode.EVICT if kind is ObjectKind.CACHE else ActionCode.DELETE

    def _build_manifest(
        self, event: LifecycleEvent, targets: list[KnowledgeEnvelope], now: datetime
    ) -> ScopeManifest:
        return self._manifest_for_targets(
            seed=event.event_id,
            scope=event.scope,
            targets=targets,
            now=now,
            requested_evidence_level=EvidenceLevel.L3,
        )

    def _manifest_for_targets(
        self,
        *,
        seed: str,
        scope: ScopeKey,
        targets: list[KnowledgeEnvelope],
        now: datetime,
        requested_evidence_level: EvidenceLevel,
    ) -> ScopeManifest:
        denominators = {kind: 0 for kind in ObjectKind}
        for target in targets:
            denominators[target.kind] += 1
        capabilities = [
            ConnectorCapabilityVersion(
                connector_ref=connector,
                capability_version="1",
                supports_inventory=True,
                supports_mutation=True,
                supports_read_back=True,
            )
            for connector in sorted(set(self.connectors.values()))
        ]
        manifest_id = stable_id("manifest", seed, requested_evidence_level.value)
        body = {
            "schema_version": "1",
            **scope.model_dump(),
            "manifest_id": manifest_id,
            "manifest_hash": "pending00",
            "selected_by": "authority://demo/data-owner",
            "requested_evidence_level": requested_evidence_level.value,
            "scan_cutoff": _utc_text(now),
            "registered_store_refs": self.registered_stores,
            "registered_connector_refs": sorted(set(self.connectors.values())),
            "connector_capability_versions": [
                capability.model_dump(mode="json") for capability in capabilities
            ],
            "derivative_classes": [kind.value for kind in ObjectKind],
            "freshness_cursors": {store: _utc_text(now) for store in self.registered_stores},
            "denominators": {kind.value: count for kind, count in denominators.items()},
            "exclusions": [],
            "created_at": _utc_text(now),
        }
        body["manifest_hash"] = _hash_json(body | {"manifest_hash": None})
        return ScopeManifest.model_validate(body)

    def _approval(
        self,
        *,
        event: LifecycleEvent,
        action_plan_hash: str,
        action_plan: list[dict[str, str]],
        now: datetime,
    ) -> MutationApproval:
        body: dict[str, Any] = {
            "schema_version": "1",
            **event.scope.model_dump(),
            "approval_id": stable_id("approval", event.event_id, action_plan_hash),
            "action_plan_hash": action_plan_hash,
            "connector_refs": sorted({item["connector_ref"] for item in action_plan}),
            "target_version_refs": sorted({item["target_version_id"] for item in action_plan}),
            "maximum_action_count": max(1, len(action_plan)),
            "requester_ref": event.actor_ref,
            "approver_ref": "actor://demo/data-owner",
            "approver_authority": "mutation_approve",
            "policy_snapshot_hash": _hash_json(
                {"policy_version": event.policy_version, "event_id": event.event_id}
            ),
            "legal_hold_snapshot_hash": _hash_json({"holds": []}),
            "nonce": stable_id("nonce", event.event_id, "approval"),
            "expires_at": _utc_text(now + timedelta(minutes=15)),
            "signature": "pending00",
        }
        from lethe_control.crypto import sign_event

        body["signature"] = sign_event(
            {key: value for key, value in body.items() if key != "signature"},
            load_demo_key_fixture("mutation_approver").private_seed,
        )
        return MutationApproval.model_validate(body)

    def accept_event(self, event: LifecycleEvent, signature: str) -> EventAccepted:
        try:
            return self._accept_event(event, signature)
        except LetheError as error:
            self._record_rejected_event(event, error.code)
            raise
        except sqlite3.IntegrityError as error:
            conflict = ConflictError(
                "event_replay_conflict",
                "event conflicts with an accepted sequence or nonce",
            )
            self._record_rejected_event(event, conflict.code)
            raise conflict from error

    def _record_rejected_event(self, event: LifecycleEvent, reason_code: str) -> None:
        try:
            received_at = self.clock.now()
        except LetheError:
            received_at = datetime.now(UTC)
        rejection_id = stable_id(
            "rejection",
            event.event_id,
            event.issuer_key_id,
            event.nonce,
            reason_code,
        )
        try:
            with self.state.transaction() as connection:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO rejected_event_audit (
                        tenant_id, workspace_id, environment_id, rejection_id,
                        event_id, issuer_key_id, authority_ref, target_version_id,
                        event_type, source_sequence, nonce, rejection_reason_code,
                        event_json, received_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        *self._scope_values(event.scope),
                        rejection_id,
                        event.event_id,
                        event.issuer_key_id,
                        event.authority_ref,
                        event.target_version_id,
                        event.event_type.value,
                        event.source_sequence,
                        event.nonce,
                        reason_code,
                        _json(event.model_dump(mode="json")),
                        _utc_text(received_at),
                    ),
                )
        except Exception:
            # Rejection auditing must never turn a reason-coded denial into a 500.
            return

    def _accept_event(self, event: LifecycleEvent, signature: str) -> EventAccepted:
        if event.scope != self.scope:
            raise AuthorizationError("scope_not_active", "event scope is not active")
        if event.audience != DEMO_AGENT_ID:
            raise AuthorizationError("wrong_audience", "event audience rejected")
        if event.authority_ref not in {
            "authority://demo/source-admin",
            "authority://demo/data-owner",
        }:
            raise AuthorizationError("unauthorized_authority", "event authority rejected")
        try:
            enrollment = load_key_enrollment(event.issuer_key_id)
        except KeyError as error:
            raise AuthorizationError("unknown_issuer", "event issuer is not enrolled") from error
        if enrollment.status != "active" or "event_issuer" not in enrollment.allowed_roles:
            raise AuthorizationError("issuer_not_active", "event issuer is not active")
        if not verify_event_signature(
            event.model_dump(mode="json"), signature, enrollment.public_key
        ):
            raise AuthorizationError("invalid_event_signature", "event signature rejected")

        now = self.clock.now()
        if now >= event.command_expires_at:
            raise AuthorizationError("command_expired", "event command expired")
        if event.effective_at > now:
            raise ConflictError("event_not_effective", "event is not effective yet")

        existing = self.state.fetch_one(
            """
            SELECT event_id, run_id FROM lifecycle_events
            WHERE tenant_id=? AND workspace_id=? AND environment_id=?
              AND authority_ref=? AND target_version_id=? AND idempotency_key=?
            """,
            (
                *self._scope_values(event.scope),
                event.authority_ref,
                event.target_version_id,
                event.idempotency_key,
            ),
        )
        if existing is not None:
            duplicate_target = self.state.get_knowledge_object(event.scope, event.target_version_id)
            return EventAccepted(
                **event.scope.model_dump(),
                event_id=str(existing["event_id"]),
                run_id=str(existing["run_id"]),
                duplicate=True,
                gate_state=(
                    duplicate_target.lifecycle_state
                    if duplicate_target is not None
                    else LifecycleState.DENIED
                ),
            )

        replayed_nonce = self.state.fetch_one(
            """
            SELECT event_id FROM lifecycle_events
            WHERE tenant_id=? AND workspace_id=? AND environment_id=?
              AND issuer_key_id=? AND nonce=?
            """,
            (
                *self._scope_values(event.scope),
                event.issuer_key_id,
                event.nonce,
            ),
        )
        if replayed_nonce is not None:
            raise ConflictError("replayed_nonce", "event nonce was already accepted")

        target = self.state.get_knowledge_object(event.scope, event.target_version_id)
        if target is None:
            raise NotFoundError("target_not_found", "event target version not found")
        latest = self.state.latest_source_sequence(
            event.scope, event.authority_ref, event.target_version_id
        )
        if latest is not None and event.source_sequence <= latest:
            raise ConflictError("stale_source_sequence", "event source sequence is stale")
        if event.policy_version < target.policy_version:
            raise ConflictError("stale_policy_version", "event policy version is stale")
        if (
            event.event_type is EventType.PERMISSION_CHANGE
            and event.permission_change is not None
            and event.permission_change.change is PermissionChangeKind.WIDEN
            and event.authority_ref != "authority://demo/data-owner"
        ):
            raise AuthorizationError(
                "permission_widening_unauthorized", "permission widening requires data owner"
            )
        if event.event_type is EventType.CORRECT:
            self._validate_replacement(event)

        target_ids = self.state.descendant_version_ids(
            event.scope, event.target_version_id, include_self=True
        )
        targets = self.state.get_knowledge_objects(event.scope, target_ids)
        if not targets:
            raise ConflictError("scope_unknown", "event scope has no tracked denominator")
        # Capture the declared-store snapshot before cleanup can race the worker.
        self.scan(
            ScanRequest(
                **event.scope.model_dump(),
                root_version_ids=[event.target_version_id],
            )
        )
        run_id = stable_id("run", event.event_id)
        manifest = self._build_manifest(event, targets, now)
        action_plan = [
            {
                "target_version_id": item.version_id,
                "target_kind": item.kind.value,
                "connector_ref": self.connectors[item.kind],
                "action_code": self._action_code(event.event_type, item.kind).value,
            }
            for item in sorted(targets, key=lambda value: value.version_id)
        ]
        if event.event_type is EventType.CORRECT and event.correction is not None:
            action_plan.append(
                {
                    "target_version_id": event.correction.replacement_version_id,
                    "target_kind": ObjectKind.SOURCE.value,
                    "connector_ref": self.connectors[ObjectKind.SOURCE],
                    "action_code": ActionCode.REBUILD.value,
                }
            )
        action_plan.sort(
            key=lambda item: (
                item["target_version_id"],
                item["connector_ref"],
                item["action_code"],
            )
        )
        action_plan_hash = _hash_json(action_plan)
        graph_snapshot_hash = _hash_json(
            {
                "target": event.target_version_id,
                "versions": sorted(target_ids),
                "manifest": manifest.manifest_hash,
            }
        )
        approval = self._approval(
            event=event,
            action_plan_hash=action_plan_hash,
            action_plan=action_plan,
            now=now,
        )

        with self.state.transaction() as connection:
            _, created = self.state.record_lifecycle_event(
                connection, event, received_at=now, run_id=run_id
            )
            if not created:
                raise ConflictError("duplicate_event", "event already exists")
            self.state.insert_scope_manifest(connection, manifest)
            self._insert_approval(connection, approval, now)
            connection.execute(
                """
                INSERT INTO propagation_runs (
                    tenant_id, workspace_id, environment_id, run_id, event_id,
                    scope_manifest_id, approval_id, phase, graph_snapshot_hash,
                    action_plan_hash, counts_json, failures_json, exclusions_json,
                    started_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?, '{}', '[]', '[]', ?, ?)
                """,
                (
                    *self._scope_values(event.scope),
                    run_id,
                    event.event_id,
                    manifest.manifest_id,
                    approval.approval_id,
                    graph_snapshot_hash,
                    action_plan_hash,
                    _utc_text(now),
                    _utc_text(now),
                ),
            )
            if event.event_type is EventType.PERMISSION_CHANGE:
                self._apply_immediate_permission_fence(connection, event, target_ids)
            else:
                for item in targets:
                    self.state.set_lifecycle_state(
                        connection, event.scope, item.version_id, LifecycleState.DENIED
                    )
                    self.state.install_tombstone(
                        connection,
                        scope=event.scope,
                        target_version_id=item.version_id,
                        event_id=event.event_id,
                        object_kind=item.kind.value,
                        reason_code=event.reason_code,
                        policy_version=event.policy_version,
                        created_at=now,
                        fingerprint_key_version=FINGERPRINT_KEY_VERSION,
                        local_content_fingerprint=item.local_content_fingerprint,
                    )
            for plan in action_plan:
                action_id = stable_id(
                    "action",
                    run_id,
                    plan["target_version_id"],
                    plan["connector_ref"],
                    plan["action_code"],
                )
                values = (
                    *self._scope_values(event.scope),
                    action_id,
                    run_id,
                    plan["target_version_id"],
                    plan["target_kind"],
                    plan["connector_ref"],
                    plan["action_code"],
                    _utc_text(now),
                    _utc_text(now),
                )
                connection.execute(
                    """
                    INSERT INTO propagation_actions (
                        tenant_id, workspace_id, environment_id, action_id, run_id,
                        target_version_id, target_kind, connector_ref, action_code,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    values,
                )
                connection.execute(
                    """
                    INSERT INTO action_outbox (
                        tenant_id, workspace_id, environment_id, action_id,
                        available_at, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        *self._scope_values(event.scope),
                        action_id,
                        _utc_text(now),
                        _utc_text(now),
                        _utc_text(now),
                    ),
                )

        return EventAccepted(
            **event.scope.model_dump(),
            event_id=event.event_id,
            run_id=run_id,
            duplicate=False,
            gate_state=(
                LifecycleState.ACTIVE
                if event.event_type is EventType.PERMISSION_CHANGE
                else LifecycleState.DENIED
            ),
        )

    def _insert_approval(
        self, connection: sqlite3.Connection, approval: MutationApproval, now: datetime
    ) -> None:
        connection.execute(
            """
            INSERT INTO mutation_approvals (
                tenant_id, workspace_id, environment_id, approval_id, schema_version,
                action_plan_hash, connector_refs_json, target_version_refs_json,
                maximum_action_count, requester_ref, approver_ref, approver_authority,
                policy_snapshot_hash, legal_hold_snapshot_hash, nonce, expires_at,
                signature, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                *self._scope_values(approval.scope),
                approval.approval_id,
                approval.schema_version,
                approval.action_plan_hash,
                _json(approval.connector_refs),
                _json(approval.target_version_refs),
                approval.maximum_action_count,
                approval.requester_ref,
                approval.approver_ref,
                approval.approver_authority,
                approval.policy_snapshot_hash,
                approval.legal_hold_snapshot_hash,
                approval.nonce,
                _utc_text(approval.expires_at),
                approval.signature,
                _utc_text(now),
            ),
        )

    def _approval_from_row(self, row: sqlite3.Row) -> MutationApproval:
        return MutationApproval.model_validate(
            {
                "schema_version": row["schema_version"],
                "tenant_id": row["tenant_id"],
                "workspace_id": row["workspace_id"],
                "environment_id": row["environment_id"],
                "approval_id": row["approval_id"],
                "action_plan_hash": row["action_plan_hash"],
                "connector_refs": json.loads(row["connector_refs_json"]),
                "target_version_refs": json.loads(row["target_version_refs_json"]),
                "maximum_action_count": row["maximum_action_count"],
                "requester_ref": row["requester_ref"],
                "approver_ref": row["approver_ref"],
                "approver_authority": row["approver_authority"],
                "policy_snapshot_hash": row["policy_snapshot_hash"],
                "legal_hold_snapshot_hash": row["legal_hold_snapshot_hash"],
                "nonce": row["nonce"],
                "expires_at": row["expires_at"],
                "signature": row["signature"],
            }
        )

    def _validate_action_approval(self, action_row: sqlite3.Row) -> None:
        run_id = str(action_row["run_id"])
        if run_id in self._validated_approval_runs:
            return
        row = self.state.fetch_one(
            """
            SELECT approval.* FROM mutation_approvals AS approval
            JOIN propagation_runs AS run
              ON run.tenant_id=approval.tenant_id
             AND run.workspace_id=approval.workspace_id
             AND run.environment_id=approval.environment_id
             AND run.approval_id=approval.approval_id
            WHERE run.tenant_id=? AND run.workspace_id=? AND run.environment_id=?
              AND run.run_id=?
            """,
            (*self._scope_values(self.scope), run_id),
        )
        if row is None:
            raise RuntimeError("approval_missing")
        approval = self._approval_from_row(row)
        enrollment = load_key_enrollment("approval_demo_01")
        if (
            enrollment.status != "active"
            or "mutation_approver" not in enrollment.allowed_roles
            or approval.scope != self.scope
            or enrollment.tenant_id != self.scope.tenant_id
            or enrollment.workspace_id != self.scope.workspace_id
            or enrollment.environment_id != self.scope.environment_id
        ):
            raise RuntimeError("approval_untrusted")
        now = self.clock.now()
        if not (_parse_time(enrollment.valid_from) <= now < _parse_time(enrollment.valid_until)):
            raise RuntimeError("approval_key_outside_validity")
        if now >= approval.expires_at:
            raise RuntimeError("approval_expired")
        unsigned = approval.model_dump(mode="json")
        signature = str(unsigned.pop("signature"))
        if not verify_event_signature(unsigned, signature, enrollment.public_key):
            raise RuntimeError("approval_signature_invalid")

        actions = self.state.fetch_all(
            """
            SELECT target_version_id, target_kind, connector_ref, action_code
            FROM propagation_actions
            WHERE tenant_id=? AND workspace_id=? AND environment_id=? AND run_id=?
            ORDER BY target_version_id, connector_ref, action_code
            """,
            (*self._scope_values(self.scope), run_id),
        )
        plan = [
            {
                "target_version_id": item["target_version_id"],
                "target_kind": item["target_kind"],
                "connector_ref": item["connector_ref"],
                "action_code": item["action_code"],
            }
            for item in actions
        ]
        if _hash_json(plan) != approval.action_plan_hash:
            raise RuntimeError("approval_plan_hash_mismatch")
        if len(actions) > approval.maximum_action_count:
            raise RuntimeError("approval_action_limit_exceeded")
        if {str(item["connector_ref"]) for item in actions} - set(approval.connector_refs):
            raise RuntimeError("approval_connector_out_of_scope")
        if {str(item["target_version_id"]) for item in actions} != set(
            approval.target_version_refs
        ):
            raise RuntimeError("approval_target_mismatch")
        if approval.legal_hold_snapshot_hash != _hash_json({"holds": []}):
            raise RuntimeError("approval_legal_hold_snapshot_invalid")
        self._validated_approval_runs.add(run_id)

    def _apply_immediate_permission_fence(
        self,
        connection: sqlite3.Connection,
        event: LifecycleEvent,
        target_ids: list[str],
    ) -> None:
        payload = event.permission_change
        if payload is None:
            raise ValueError("permission event missing payload")
        allowed = ["alice"] if payload.change is PermissionChangeKind.NARROW else ["alice", "bob"]
        policy = self._acl_policy(
            acl_ref=payload.new_acl_ref,
            policy_version=payload.new_policy_version,
            allowed_principals=allowed,
            valid_until=_parse_time(self.manifest["clock"]["canary_source_valid_until"]),
        )
        self._store_acl(policy, connection)
        if payload.change is PermissionChangeKind.WIDEN:
            return
        placeholders = ",".join("?" for _ in target_ids)
        connection.execute(
            f"""
            UPDATE knowledge_objects SET acl_ref=?, policy_version=?
            WHERE tenant_id=? AND workspace_id=? AND environment_id=?
              AND version_id IN ({placeholders})
            """,
            (
                payload.new_acl_ref,
                payload.new_policy_version,
                *self._scope_values(event.scope),
                *target_ids,
            ),
        )

    def _validate_replacement(self, event: LifecycleEvent) -> None:
        payload = event.correction
        if payload is None:
            raise ConflictError("missing_replacement", "correction replacement missing")
        entry = next(
            (
                item
                for item in self.manifest["corpus"]
                if item.get("version_id") == payload.replacement_version_id
            ),
            None,
        )
        if entry is None or entry.get("registered") is not False:
            raise ConflictError("replacement_not_registered", "replacement is not registered")
        expected_ref = f"local-object://registered/{payload.replacement_version_id}"
        if payload.replacement_content_ref != expected_ref:
            raise ConflictError("replacement_ref_mismatch", "replacement reference mismatch")
        replacement_path = Path(__file__).resolve().parents[2] / "fixtures" / entry["path"]
        fingerprint = self._fingerprint(
            ObjectKind.SOURCE, replacement_path.read_text(encoding="utf-8")
        )
        if fingerprint != payload.replacement_fingerprint:
            raise ConflictError(
                "replacement_fingerprint_mismatch", "replacement fingerprint mismatch"
            )

    def reconcile_actions(self) -> int:
        if not self.settings.database_path.exists():
            return 0
        now = _utc_text(self.clock.now())
        with self.state.transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE action_outbox
                SET state='retryable', lease_owner=NULL, lease_expires_at=NULL, updated_at=?
                WHERE state='leased'
                """,
                (now,),
            )
            connection.execute(
                """
                UPDATE propagation_actions
                SET state='retryable', lease_owner=NULL, lease_expires_at=NULL, updated_at=?
                WHERE state='leased'
                """,
                (now,),
            )
            return cursor.rowcount

    def reconcile_terminal_runs(self) -> int:
        rows = self.state.fetch_all(
            """
            SELECT run_id FROM propagation_runs
            WHERE tenant_id=? AND workspace_id=? AND environment_id=?
              AND phase <> 'completed'
              AND NOT EXISTS (
                  SELECT 1 FROM propagation_actions AS action
                  WHERE action.tenant_id=propagation_runs.tenant_id
                    AND action.workspace_id=propagation_runs.workspace_id
                    AND action.environment_id=propagation_runs.environment_id
                    AND action.run_id=propagation_runs.run_id
                    AND action.state IN ('pending', 'leased', 'retryable')
              )
            """,
            self._scope_values(self.scope),
        )
        for row in rows:
            self._finalize_if_terminal(str(row["run_id"]))
        return len(rows)

    def process_next_action(self, *, force: bool = False) -> bool:
        now = self.clock.now()
        lease_until = now + timedelta(seconds=30)
        with self.state.transaction() as connection:
            where_time = "" if force else "AND outbox.available_at <= ?"
            parameters: tuple[Any, ...] = (
                (*self._scope_values(self.scope),)
                if force
                else (*self._scope_values(self.scope), _utc_text(now))
            )
            row = connection.execute(
                f"""
                SELECT action.* FROM action_outbox AS outbox
                JOIN propagation_actions AS action
                  ON action.tenant_id=outbox.tenant_id
                 AND action.workspace_id=outbox.workspace_id
                 AND action.environment_id=outbox.environment_id
                 AND action.action_id=outbox.action_id
                WHERE outbox.tenant_id=? AND outbox.workspace_id=? AND outbox.environment_id=?
                  AND outbox.state IN ('pending','retryable') {where_time}
                ORDER BY outbox.created_at, outbox.action_id LIMIT 1
                """,
                parameters,
            ).fetchone()
            if row is None:
                return False
            attempt = int(row["attempt_count"]) + 1
            common = (
                "worker-local",
                _utc_text(lease_until),
                attempt,
                _utc_text(now),
                *self._scope_values(self.scope),
                row["action_id"],
            )
            connection.execute(
                """
                UPDATE action_outbox
                SET state='leased', lease_owner=?, lease_expires_at=?,
                    attempt_count=?, updated_at=?
                WHERE tenant_id=? AND workspace_id=? AND environment_id=? AND action_id=?
                """,
                common,
            )
            connection.execute(
                """
                UPDATE propagation_actions
                SET state='leased', lease_owner=?, lease_expires_at=?,
                    attempt_count=?, started_at=COALESCE(started_at, ?), updated_at=?
                WHERE tenant_id=? AND workspace_id=? AND environment_id=? AND action_id=?
                """,
                (
                    "worker-local",
                    _utc_text(lease_until),
                    attempt,
                    _utc_text(now),
                    _utc_text(now),
                    *self._scope_values(self.scope),
                    row["action_id"],
                ),
            )

        try:
            self._validate_action_approval(row)
            read_back = self._apply_action(row)
        except Exception as error:
            self._record_action_failure(row, attempt, error)
        else:
            completed = self.clock.now()
            with self.state.transaction() as connection:
                for table in ("action_outbox", "propagation_actions"):
                    extra = ", completed_at=?" if table == "propagation_actions" else ""
                    values: tuple[Any, ...]
                    if table == "propagation_actions":
                        values = (
                            _json(read_back),
                            _utc_text(completed),
                            _utc_text(completed),
                            *self._scope_values(self.scope),
                            row["action_id"],
                        )
                    else:
                        values = (
                            _utc_text(completed),
                            *self._scope_values(self.scope),
                            row["action_id"],
                        )
                    if table == "propagation_actions":
                        connection.execute(
                            f"""
                            UPDATE {table} SET state='succeeded', read_back_json=?,
                                lease_owner=NULL, lease_expires_at=NULL{extra}, updated_at=?
                            WHERE tenant_id=? AND workspace_id=? AND environment_id=?
                              AND action_id=?
                            """,
                            values,
                        )
                    else:
                        connection.execute(
                            f"""
                            UPDATE {table} SET state='succeeded', lease_owner=NULL,
                                lease_expires_at=NULL, updated_at=?
                            WHERE tenant_id=? AND workspace_id=? AND environment_id=?
                              AND action_id=?
                            """,
                            values,
                        )
        self._finalize_if_terminal(str(row["run_id"]))
        return True

    def process_cache_action_batch(self, *, force: bool = False, limit: int = 1_000) -> int:
        now = self.clock.now()
        where_time = "" if force else "AND outbox.available_at <= ?"
        parameters: tuple[Any, ...] = (
            (*self._scope_values(self.scope), limit)
            if force
            else (*self._scope_values(self.scope), _utc_text(now), limit)
        )
        rows = self.state.fetch_all(
            f"""
            SELECT action.* FROM action_outbox AS outbox
            JOIN propagation_actions AS action
              ON action.tenant_id=outbox.tenant_id
             AND action.workspace_id=outbox.workspace_id
             AND action.environment_id=outbox.environment_id
             AND action.action_id=outbox.action_id
            JOIN propagation_runs AS run
              ON run.tenant_id=action.tenant_id
             AND run.workspace_id=action.workspace_id
             AND run.environment_id=action.environment_id
             AND run.run_id=action.run_id
            JOIN lifecycle_events AS event
              ON event.tenant_id=run.tenant_id
             AND event.workspace_id=run.workspace_id
             AND event.environment_id=run.environment_id
             AND event.event_id=run.event_id
            WHERE outbox.tenant_id=? AND outbox.workspace_id=? AND outbox.environment_id=?
              AND outbox.state IN ('pending','retryable') {where_time}
              AND action.target_kind='cache' AND action.action_code='evict'
              AND event.event_type IN ('delete','correct','expire')
            ORDER BY outbox.created_at, outbox.action_id LIMIT ?
            """,
            parameters,
        )
        if not rows:
            return 0
        try:
            for run_id in {str(row["run_id"]) for row in rows}:
                run_row = next(row for row in rows if str(row["run_id"]) == run_id)
                self._validate_action_approval(run_row)
        except Exception:
            return 0

        completed = _utc_text(now)
        read_back = _json(
            {"result": "payload_absent", "verified": True, "transactional_batch": True}
        )
        with self.state.transaction() as connection:
            for row in rows:
                attempt = int(row["attempt_count"]) + 1
                connection.execute(
                    """
                    DELETE FROM cache_entries
                    WHERE tenant_id=? AND workspace_id=? AND environment_id=? AND version_id=?
                    """,
                    (*self._scope_values(self.scope), row["target_version_id"]),
                )
                self.state.set_lifecycle_state(
                    connection,
                    self.scope,
                    str(row["target_version_id"]),
                    LifecycleState.TOMBSTONED,
                )
                connection.execute(
                    """
                    UPDATE propagation_actions
                    SET state='succeeded', attempt_count=?, lease_owner=NULL,
                        lease_expires_at=NULL, last_error_code=NULL, read_back_json=?,
                        started_at=COALESCE(started_at, ?), completed_at=?, updated_at=?
                    WHERE tenant_id=? AND workspace_id=? AND environment_id=? AND action_id=?
                    """,
                    (
                        attempt,
                        read_back,
                        completed,
                        completed,
                        completed,
                        *self._scope_values(self.scope),
                        row["action_id"],
                    ),
                )
                connection.execute(
                    """
                    UPDATE action_outbox
                    SET state='succeeded', attempt_count=?, lease_owner=NULL,
                        lease_expires_at=NULL, last_error_code=NULL, updated_at=?
                    WHERE tenant_id=? AND workspace_id=? AND environment_id=? AND action_id=?
                    """,
                    (
                        attempt,
                        completed,
                        *self._scope_values(self.scope),
                        row["action_id"],
                    ),
                )
        for run_id in {str(row["run_id"]) for row in rows}:
            self._finalize_if_terminal(run_id)
        return len(rows)

    def _record_action_failure(self, row: sqlite3.Row, attempt: int, error: Exception) -> None:
        now = self.clock.now()
        terminal = attempt >= self.max_attempts
        state = "failed" if terminal else "retryable"
        error_code = str(error)[:160] or error.__class__.__name__
        available = now + timedelta(milliseconds=100 * attempt * attempt)
        with self.state.transaction() as connection:
            connection.execute(
                """
                UPDATE action_outbox SET state=?, available_at=?, lease_owner=NULL,
                    lease_expires_at=NULL, last_error_code=?, updated_at=?
                WHERE tenant_id=? AND workspace_id=? AND environment_id=? AND action_id=?
                """,
                (
                    state,
                    _utc_text(available),
                    error_code,
                    _utc_text(now),
                    *self._scope_values(self.scope),
                    row["action_id"],
                ),
            )
            connection.execute(
                """
                UPDATE propagation_actions SET state=?, lease_owner=NULL,
                    lease_expires_at=NULL, last_error_code=?, completed_at=?, updated_at=?
                WHERE tenant_id=? AND workspace_id=? AND environment_id=? AND action_id=?
                """,
                (
                    state,
                    error_code,
                    _utc_text(now) if terminal else None,
                    _utc_text(now),
                    *self._scope_values(self.scope),
                    row["action_id"],
                ),
            )

    def _apply_action(self, row: sqlite3.Row) -> dict[str, Any]:
        version_id = str(row["target_version_id"])
        kind = ObjectKind(str(row["target_kind"]))
        action = ActionCode(str(row["action_code"]))
        event = self._event_for_run(str(row["run_id"]))
        if action is ActionCode.REBUILD:
            if event.event_type is not EventType.CORRECT:
                raise RuntimeError("rebuild_event_mismatch")
            self._build_replacement(event)
            descendants = self.state.descendant_version_ids(
                self.scope, version_id, include_self=False
            )
            kinds = {
                envelope.kind
                for descendant in descendants
                if (envelope := self.state.get_knowledge_object(self.scope, descendant)) is not None
            }
            required = {
                ObjectKind.CHUNK,
                ObjectKind.EMBEDDING,
                ObjectKind.CACHE,
                ObjectKind.SUMMARY,
                ObjectKind.MEMORY,
            }
            if not required <= kinds:
                raise RuntimeError("replacement_branch_incomplete")
            return {
                "result": "branch_rebuilt",
                "verified": True,
                "derivative_kinds": sorted(item.value for item in kinds),
            }
        envelope = self.state.get_knowledge_object(self.scope, version_id)
        if envelope is None:
            raise RuntimeError("target_missing")
        if event.event_type is EventType.PERMISSION_CHANGE and event.permission_change is not None:
            change = event.permission_change
            if change.change is PermissionChangeKind.WIDEN:
                if kind is ObjectKind.EMBEDDING:
                    self.vectors.update_metadata(
                        **self.scope.model_dump(),
                        version_id=version_id,
                        metadata={
                            **self.scope.model_dump(),
                            "object_id": envelope.object_id,
                            "version_id": version_id,
                            "content_version_id": envelope.parent_version_ids[0],
                            "root_version_ids": envelope.root_version_ids,
                            "policy_ref": envelope.policy_ref,
                            "policy_version": change.new_policy_version,
                            "lifecycle_state": envelope.lifecycle_state.value,
                            "acl_ref": change.new_acl_ref,
                            "valid_until": _utc_text(envelope.valid_until)
                            if envelope.valid_until
                            else "",
                            "derivative_kind": kind.value,
                        },
                    )
                    if not self._vector_metadata_matches(
                        version_id,
                        acl_ref=change.new_acl_ref,
                        policy_version=change.new_policy_version,
                    ):
                        raise RuntimeError("read_back_failed")
                return {"result": "widening_staged", "verified": True}
            if kind is ObjectKind.CACHE and action is ActionCode.EVICT:
                policy_row = self.state.fetch_one(
                    """
                    SELECT allowed_principal_refs_json FROM acl_policies
                    WHERE tenant_id=? AND workspace_id=? AND environment_id=?
                      AND acl_ref=? AND policy_version=?
                    """,
                    (
                        *self._scope_values(self.scope),
                        change.new_acl_ref,
                        change.new_policy_version,
                    ),
                )
                if policy_row is None:
                    raise RuntimeError("permission_policy_missing")
                allowed = list(json.loads(policy_row["allowed_principal_refs_json"]))
                placeholders = ",".join("?" for _ in allowed)
                with self.state.transaction() as connection:
                    connection.execute(
                        f"""
                        DELETE FROM cache_entries
                        WHERE tenant_id=? AND workspace_id=? AND environment_id=?
                          AND version_id=?
                          AND principal_ref NOT IN ({placeholders})
                        """,
                        (*self._scope_values(self.scope), version_id, *allowed),
                    )
                    connection.execute(
                        """
                        UPDATE cache_entries SET policy_version=?
                        WHERE tenant_id=? AND workspace_id=? AND environment_id=?
                          AND version_id=?
                        """,
                        (
                            change.new_policy_version,
                            *self._scope_values(self.scope),
                            version_id,
                        ),
                    )
                remaining_removed = self.state.fetch_one(
                    f"""
                    SELECT 1 FROM cache_entries
                    WHERE tenant_id=? AND workspace_id=? AND environment_id=?
                      AND version_id=? AND principal_ref NOT IN ({placeholders})
                    LIMIT 1
                    """,
                    (*self._scope_values(self.scope), version_id, *allowed),
                )
                stale_policy = self.state.fetch_one(
                    """
                    SELECT 1 FROM cache_entries
                    WHERE tenant_id=? AND workspace_id=? AND environment_id=?
                      AND version_id=? AND policy_version<>? LIMIT 1
                    """,
                    (
                        *self._scope_values(self.scope),
                        version_id,
                        change.new_policy_version,
                    ),
                )
                if remaining_removed is not None or stale_policy is not None:
                    raise RuntimeError("read_back_failed")
                return {
                    "result": "removed_principal_cache_evicted",
                    "verified": True,
                    "policy_version": change.new_policy_version,
                }

        absent = False
        lifecycle_updated = False
        if action in {ActionCode.DELETE, ActionCode.EVICT}:
            if kind in {ObjectKind.SOURCE, ObjectKind.CHUNK}:
                quarantined_exact_refs: list[str] = []
                if kind is ObjectKind.SOURCE and envelope.local_content_fingerprint:
                    tracked_rows = self.state.fetch_all(
                        """
                        SELECT content_ref FROM knowledge_objects
                        WHERE tenant_id=? AND workspace_id=? AND environment_id=?
                          AND kind IN ('source', 'chunk') AND content_ref IS NOT NULL
                        """,
                        self._scope_values(self.scope),
                    )
                    tracked_refs = {str(item["content_ref"]) for item in tracked_rows}
                    for candidate_ref, candidate_bytes in self.objects.inventory(
                        **self.scope.model_dump()
                    ):
                        if candidate_ref in tracked_refs:
                            continue
                        try:
                            candidate = candidate_bytes.decode("utf-8")
                        except UnicodeDecodeError:
                            continue
                        if (
                            self._fingerprint(ObjectKind.SOURCE, candidate)
                            == envelope.local_content_fingerprint
                        ):
                            self.objects.delete(candidate_ref)
                            if not self.objects.exists(candidate_ref):
                                quarantined_exact_refs.append(candidate_ref)
                if envelope.content_ref:
                    self.objects.delete(envelope.content_ref)
                    absent = not self.objects.exists(envelope.content_ref)
            elif kind is ObjectKind.EMBEDDING:
                self.vectors.delete(**self.scope.model_dump(), version_id=version_id)
                absent = not self.vectors.exists(**self.scope.model_dump(), version_id=version_id)
            else:
                table = {
                    ObjectKind.CACHE: "cache_entries",
                    ObjectKind.SUMMARY: "summaries",
                    ObjectKind.MEMORY: "memories",
                }[kind]
                with self.state.transaction() as connection:
                    connection.execute(
                        f"""
                        DELETE FROM {table}
                        WHERE tenant_id=? AND workspace_id=? AND environment_id=? AND version_id=?
                        """,
                        (*self._scope_values(self.scope), version_id),
                    )
                    self.state.set_lifecycle_state(
                        connection, self.scope, version_id, LifecycleState.TOMBSTONED
                    )
                    remaining = connection.execute(
                        f"""
                        SELECT 1 FROM {table}
                        WHERE tenant_id=? AND workspace_id=? AND environment_id=?
                          AND version_id=? LIMIT 1
                        """,
                        (*self._scope_values(self.scope), version_id),
                    ).fetchone()
                absent = remaining is None
                lifecycle_updated = True
            if not lifecycle_updated:
                with self.state.transaction() as connection:
                    self.state.set_lifecycle_state(
                        connection, self.scope, version_id, LifecycleState.TOMBSTONED
                    )
            if not absent:
                raise RuntimeError("read_back_failed")
            return {
                "result": "payload_absent",
                "verified": True,
                "exact_untracked_quarantined": len(quarantined_exact_refs)
                if kind is ObjectKind.SOURCE
                else 0,
            }

        if action is ActionCode.UPDATE_ACL:
            current = self.state.get_knowledge_object(self.scope, version_id)
            if current is None:
                raise RuntimeError("target_missing")
            if kind is ObjectKind.EMBEDDING:
                self.vectors.update_metadata(
                    **self.scope.model_dump(),
                    version_id=version_id,
                    metadata={
                        **self.scope.model_dump(),
                        "object_id": current.object_id,
                        "version_id": version_id,
                        "content_version_id": current.parent_version_ids[0],
                        "root_version_ids": current.root_version_ids,
                        "policy_ref": current.policy_ref,
                        "policy_version": current.policy_version,
                        "lifecycle_state": current.lifecycle_state.value,
                        "acl_ref": current.acl_ref,
                        "valid_until": _utc_text(current.valid_until)
                        if current.valid_until
                        else "",
                        "derivative_kind": kind.value,
                    },
                )
                if not self._vector_metadata_matches(
                    version_id,
                    acl_ref=current.acl_ref,
                    policy_version=current.policy_version,
                ):
                    raise RuntimeError("read_back_failed")
            return {
                "result": "metadata_updated",
                "acl_ref": current.acl_ref,
                "policy_version": current.policy_version,
                "verified": True,
            }
        raise RuntimeError("unsupported_action")

    def drain_actions(self, *, force: bool = True, limit: int = 100_000) -> int:
        processed = 0
        while processed < limit:
            batched = self.process_cache_action_batch(
                force=force, limit=min(1_000, limit - processed)
            )
            if batched:
                processed += batched
                continue
            if not self.process_next_action(force=force):
                break
            processed += 1
        return processed

    def start_worker(self, *, poll_interval: float = 0.1) -> None:
        if self._worker and self._worker.is_alive():
            return
        self._stop_worker.clear()

        def run() -> None:
            next_expiration_check = 0.0
            while not self._stop_worker.is_set():
                monotonic_now = time.monotonic()
                if monotonic_now >= next_expiration_check:
                    with suppress(LetheError):
                        self.process_due_expirations()
                    next_expiration_check = monotonic_now + 0.5
                if not self.process_cache_action_batch() and not self.process_next_action():
                    self._stop_worker.wait(poll_interval)

        self._worker = threading.Thread(target=run, name="lethe-worker", daemon=True)
        self._worker.start()

    def stop_worker(self) -> None:
        self._stop_worker.set()
        if self._worker and self._worker.is_alive():
            self._worker.join(timeout=2)
        self._worker = None

    def _finalize_if_terminal(self, run_id: str) -> None:
        nonterminal = self.state.fetch_one(
            """
            SELECT 1 FROM propagation_actions
            WHERE tenant_id=? AND workspace_id=? AND environment_id=? AND run_id=?
              AND state IN ('pending', 'leased', 'retryable')
            LIMIT 1
            """,
            (*self._scope_values(self.scope), run_id),
        )
        if nonterminal is not None:
            return
        existing_receipt = self.state.fetch_one(
            """
            SELECT receipt_json FROM receipt_chain
            WHERE tenant_id=? AND workspace_id=? AND environment_id=? AND run_id=?
            """,
            (*self._scope_values(self.scope), run_id),
        )
        if existing_receipt is not None:
            receipt = ExecutionReceipt.model_validate_json(existing_receipt["receipt_json"])
            with self.state.transaction() as connection:
                connection.execute(
                    """
                    UPDATE propagation_runs
                    SET phase='completed', outcome=?, completed_at=COALESCE(completed_at, ?),
                        updated_at=?
                    WHERE tenant_id=? AND workspace_id=? AND environment_id=? AND run_id=?
                    """,
                    (
                        receipt.outcome.value,
                        _utc_text(receipt.completed_at),
                        _utc_text(receipt.completed_at),
                        *self._scope_values(self.scope),
                        run_id,
                    ),
                )
            return
        run = self.state.fetch_one(
            """
            SELECT * FROM propagation_runs
            WHERE tenant_id=? AND workspace_id=? AND environment_id=? AND run_id=?
            """,
            (*self._scope_values(self.scope), run_id),
        )
        if run is None:
            return
        actions = self.state.fetch_all(
            """
            SELECT * FROM propagation_actions
            WHERE tenant_id=? AND workspace_id=? AND environment_id=? AND run_id=?
            ORDER BY action_id
            """,
            (*self._scope_values(self.scope), run_id),
        )
        if not actions:
            return
        event_row = self.state.fetch_one(
            """
            SELECT * FROM lifecycle_events
            WHERE tenant_id=? AND workspace_id=? AND environment_id=? AND event_id=?
            """,
            (*self._scope_values(self.scope), run["event_id"]),
        )
        if event_row is None:
            return
        event = self._event_from_row(event_row)
        rebuild_error: str | None = None
        if event.event_type is EventType.CORRECT and all(
            row["state"] == ActionState.SUCCEEDED.value for row in actions
        ):
            try:
                self._build_replacement(event)
            except Exception as error:
                rebuild_error = str(error)[:160]
        if (
            event.event_type is EventType.PERMISSION_CHANGE
            and event.permission_change is not None
            and event.permission_change.change is PermissionChangeKind.WIDEN
            and all(row["state"] == ActionState.SUCCEEDED.value for row in actions)
        ):
            try:
                self._commit_permission_widening(event)
            except Exception as error:
                rebuild_error = str(error)[:160]

        resurrection = self.run_resurrection_attacks(event)
        probes = self.run_probes(event)
        failed = [row for row in actions if row["state"] != ActionState.SUCCEEDED.value]
        functional_failure = (
            probes["prohibited_disclosures"] > 0
            or probes["unrelated_false_blocks"] > 1
            or rebuild_error is not None
            or not all(resurrection.values())
        )
        verified_actions = 0
        for row in actions:
            if row["state"] != ActionState.SUCCEEDED.value or not row["read_back_json"]:
                continue
            try:
                verified_actions += int(bool(json.loads(row["read_back_json"])["verified"]))
            except (KeyError, TypeError, json.JSONDecodeError):
                continue
        if failed and len(failed) == len(actions):
            outcome = RunOutcome.FAILED
        elif verified_actions < len(actions) and not failed:
            outcome = RunOutcome.UNKNOWN
        elif failed or functional_failure:
            outcome = RunOutcome.PARTIAL
        else:
            outcome = RunOutcome.SUCCEEDED
        with self._receipt_lock:
            if (
                self.state.fetch_one(
                    """
                SELECT 1 FROM receipt_chain
                WHERE tenant_id=? AND workspace_id=? AND environment_id=? AND run_id=?
                """,
                    (*self._scope_values(self.scope), run_id),
                )
                is not None
            ):
                return
            receipt = self._issue_receipt(
                run=run,
                event=event,
                actions=actions,
                probes=probes,
                resurrection=resurrection,
                outcome=outcome,
                rebuild_error=rebuild_error,
            )
            counts = {
                "expected": len(actions),
                "succeeded": sum(row["state"] == "succeeded" for row in actions),
                "failed": len(failed),
                "verified": receipt.counts.targets_verified,
            }
            completed = self.clock.now()
            entry_hash = receipt_entry_hash(receipt.model_dump(mode="json", by_alias=True))
            with self.state.transaction() as connection:
                self.state.append_receipt(connection, receipt, entry_hash=entry_hash)
                connection.execute(
                    """
                    UPDATE propagation_runs
                    SET phase='completed', outcome=?, counts_json=?, failures_json=?,
                        completed_at=?, updated_at=?
                    WHERE tenant_id=? AND workspace_id=? AND environment_id=? AND run_id=?
                    """,
                    (
                        outcome.value,
                        _json(counts),
                        _json([row["action_id"] for row in failed]),
                        _utc_text(completed),
                        _utc_text(completed),
                        *self._scope_values(self.scope),
                        run_id,
                    ),
                )

    def _build_replacement(self, event: LifecycleEvent) -> None:
        payload = event.correction
        if payload is None:
            raise ValueError("correction replacement missing")
        entry = next(
            item
            for item in self.manifest["corpus"]
            if item.get("version_id") == payload.replacement_version_id
        )
        text = (Path(__file__).resolve().parents[2] / "fixtures" / str(entry["path"])).read_text(
            encoding="utf-8"
        )
        acl_ref = str(entry["acl_ref"])
        policy = self._acl_policy(
            acl_ref=acl_ref,
            policy_version=event.policy_version,
            allowed_principals=list(entry["allowed_principals"]),
            valid_until=_parse_time(str(entry["valid_until"])),
        )
        self._store_acl(policy)
        replacement = self.state.get_knowledge_object(self.scope, payload.replacement_version_id)
        if replacement is None:
            replacement = self._publish_source(
                object_id=str(entry["object_id"]),
                version_id=str(entry["version_id"]),
                text=text,
                acl_ref=acl_ref,
                policy_version=event.policy_version,
                valid_from=event.effective_at,
                valid_until=_parse_time(str(entry["valid_until"])),
                supersedes=event.target_version_id,
            )
        elif replacement.supersedes_version_id != event.target_version_id:
            raise ConflictError(
                "replacement_branch_conflict", "replacement supersession does not match"
            )
        self._derive_source(replacement, text, full=True)
        self._rebuild_mixed_parent_summary(replacement)

    def _rebuild_mixed_parent_summary(self, replacement: KnowledgeEnvelope) -> None:
        policy_parent = self.state.get_knowledge_object(self.scope, "ver_demo_policy_parent_001")
        if (
            policy_parent is None
            or policy_parent.lifecycle_state is not LifecycleState.ACTIVE
            or policy_parent.valid_until is None
            or replacement.valid_until is None
            or self.clock.now() >= policy_parent.valid_until
        ):
            return
        version_id = stable_id(
            "ver", replacement.version_id, policy_parent.version_id, "mixed-summary"
        )
        text = (
            "# Nightjar Corrected Combined Briefing\n\n"
            f"PARENTS: {replacement.version_id}, {policy_parent.version_id}\n"
            'FACT: Project Nightjar\'s current canary phrase is "blue-orchid-842".\n'
            "FACT: Distribution remains restricted by every contributing parent.\n"
        )
        envelope = self._envelope(
            object_id=stable_id("obj", replacement.object_id, "mixed-summary"),
            version_id=version_id,
            kind=ObjectKind.SUMMARY,
            parents=[replacement.version_id, policy_parent.version_id],
            roots=[replacement.version_id, policy_parent.version_id],
            acl_ref=replacement.acl_ref,
            policy_version=replacement.policy_version,
            valid_from=max(replacement.valid_from, policy_parent.valid_from),
            valid_until=min(replacement.valid_until, policy_parent.valid_until),
            content_ref=f"sqlite://summaries/{version_id}",
            payload=text,
            activity="rebuild_mixed_parent",
        )
        self._publish_sql_payload(envelope, table="summaries", payload=text)

    def _commit_permission_widening(self, event: LifecycleEvent) -> None:
        change = event.permission_change
        if change is None or change.change is not PermissionChangeKind.WIDEN:
            return
        target_ids = self.state.descendant_version_ids(
            event.scope, event.target_version_id, include_self=True
        )
        placeholders = ",".join("?" for _ in target_ids)
        with self.state.transaction() as connection:
            connection.execute(
                f"""
                UPDATE knowledge_objects SET acl_ref=?, policy_version=?
                WHERE tenant_id=? AND workspace_id=? AND environment_id=?
                  AND version_id IN ({placeholders})
                """,
                (
                    change.new_acl_ref,
                    change.new_policy_version,
                    *self._scope_values(event.scope),
                    *target_ids,
                ),
            )

    def run_probes(self, event: LifecycleEvent) -> dict[str, int]:
        if event.event_type is EventType.PERMISSION_CHANGE:
            principal = (
                "principal://demo/charlie"
                if event.permission_change is not None
                and event.permission_change.change is PermissionChangeKind.WIDEN
                else "principal://demo/bob"
            )
        else:
            principal = "principal://demo/alice"
        target_versions = set(
            self.state.descendant_version_ids(
                event.scope, event.target_version_id, include_self=True
            )
        )
        prohibited_disclosures = 0
        for query in load_probe_suite("prohibited"):
            response = self.query(
                QueryRequest(query=query, purpose_ref=DEMO_PURPOSE),
                principal_ref=principal,
            )
            answer = (response.answer or "").casefold()
            target_supported = bool(target_versions.intersection(response.supporting_version_ids))
            if target_supported and ("amber-lantern-731" in answer or "vault 19" in answer):
                prohibited_disclosures += 1
        unrelated_false_blocks = 0
        for query in load_probe_suite("unrelated"):
            response = self.query(
                QueryRequest(query=query, purpose_ref=DEMO_PURPOSE),
                principal_ref="principal://demo/alice",
            )
            unrelated_false_blocks += int(response.denied)
        gate_allowed, _ = self.gate_version(
            scope=event.scope,
            version_id=event.target_version_id,
            principal_ref=principal,
            purpose_ref=DEMO_PURPOSE,
        )
        return {
            "prohibited_queries": 100,
            "prohibited_disclosures": prohibited_disclosures,
            "unrelated_queries": 100,
            "unrelated_false_blocks": unrelated_false_blocks,
            "target_gate_denials": int(not gate_allowed),
        }

    def run_resurrection_attacks(self, event: LifecycleEvent) -> dict[str, bool]:
        fixture_key = load_demo_key_fixture("event_issuer")
        signature = sign_event(event.model_dump(mode="json"), fixture_key.private_seed)
        duplicate = self.accept_event(event, signature)

        stale = event.model_copy(
            update={
                "event_id": stable_id("evt", event.event_id, "stale-replay"),
                "idempotency_key": stable_id("idem", event.event_id, "stale-replay"),
                "nonce": stable_id("nonce", event.event_id, "stale-replay"),
            }
        )
        stale_signature = sign_event(stale.model_dump(mode="json"), fixture_key.private_seed)
        stale_rejected = False
        try:
            self.accept_event(stale, stale_signature)
        except ConflictError as error:
            stale_rejected = error.code == "stale_source_sequence"

        reingestion_blocked = True
        if event.event_type is not EventType.PERMISSION_CHANGE:
            entry = next(
                item
                for item in self.manifest["corpus"]
                if item.get("version_id") == event.target_version_id
            )
            source_text = (
                Path(__file__).resolve().parents[2] / "fixtures" / str(entry["path"])
            ).read_text(encoding="utf-8")
            target = self.state.get_knowledge_object(self.scope, event.target_version_id)
            if target is None:
                raise RuntimeError("resurrection_target_missing")
            restored_valid_until = (
                target.valid_until
                if target.valid_until is not None and target.valid_until > event.effective_at
                else event.effective_at + timedelta(days=1)
            )
            try:
                self._publish_source(
                    object_id=stable_id("obj", event.event_id, "resurrection"),
                    version_id=stable_id("ver", event.event_id, "resurrection"),
                    text=source_text,
                    acl_ref=target.acl_ref,
                    policy_version=event.policy_version,
                    valid_from=event.effective_at,
                    valid_until=restored_valid_until,
                    supersedes=None,
                )
            except ConflictError as error:
                reingestion_blocked = error.code == "resurrection_quarantined"
            else:
                reingestion_blocked = False

        cache_restore_blocked = True
        descendants = self.state.descendant_version_ids(
            event.scope, event.target_version_id, include_self=False
        )
        cache = next(
            (
                envelope
                for version_id in descendants
                if (envelope := self.state.get_knowledge_object(self.scope, version_id)) is not None
                and envelope.kind is ObjectKind.CACHE
            ),
            None,
        )
        if cache is not None and event.event_type is not EventType.PERMISSION_CHANGE:
            attack_key = stable_id("cache", event.event_id, "stale-restore")
            with self.state.transaction() as connection:
                connection.execute(
                    """
                    INSERT OR REPLACE INTO cache_entries (
                        tenant_id, workspace_id, environment_id, cache_key, version_id,
                        principal_ref, policy_version, payload, created_at, expires_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        *self._scope_values(self.scope),
                        attack_key,
                        cache.version_id,
                        "principal://demo/alice",
                        cache.policy_version,
                        _json({"answer": "amber-lantern-731", "restored": True}),
                        _utc_text(self.clock.now()),
                        _utc_text(cache.valid_until) if cache.valid_until else None,
                    ),
                )
            allowed, _ = self.gate_version(
                scope=self.scope,
                version_id=cache.version_id,
                principal_ref="principal://demo/alice",
                purpose_ref=DEMO_PURPOSE,
            )
            cache_restore_blocked = not allowed
            with self.state.transaction() as connection:
                connection.execute(
                    """
                    DELETE FROM cache_entries
                    WHERE tenant_id=? AND workspace_id=? AND environment_id=? AND cache_key=?
                    """,
                    (*self._scope_values(self.scope), attack_key),
                )

        return {
            "duplicate_returned_original_run": duplicate.duplicate,
            "stale_replay_rejected": stale_rejected,
            "reingestion_quarantined": reingestion_blocked,
            "stale_cache_gate_blocked": cache_restore_blocked,
        }

    def _issue_receipt(
        self,
        *,
        run: sqlite3.Row,
        event: LifecycleEvent,
        actions: list[sqlite3.Row],
        probes: dict[str, int],
        resurrection: dict[str, bool],
        outcome: RunOutcome,
        rebuild_error: str | None,
    ) -> ExecutionReceipt:
        head = self.state.receipt_head(self.scope, DEMO_AGENT_ID)
        sequence = int(head["chain_sequence"]) + 1 if head else 1
        previous_hash = str(head["entry_hash"]) if head else None
        candidate_row = self.state.fetch_one(
            """
            SELECT COUNT(*) AS count FROM scanner_findings
            WHERE tenant_id=? AND workspace_id=? AND environment_id=?
              AND finding_class <> 'tracked'
            """,
            self._scope_values(self.scope),
        )
        scanner_candidates = int(candidate_row["count"]) if candidate_row else 0
        semantic_row = self.state.fetch_one(
            """
            SELECT COUNT(*) AS count FROM scanner_findings
            WHERE tenant_id=? AND workspace_id=? AND environment_id=?
              AND finding_class = 'semantic_candidate'
            """,
            self._scope_values(self.scope),
        )
        semantic_candidates = int(semantic_row["count"]) if semantic_row else 0
        failed = [row for row in actions if row["state"] != "succeeded"]
        now = self.clock.now()
        receipt_actions = [
            ReceiptAction(
                action_id=row["action_id"],
                target_version_ref=row["target_version_id"],
                derivative_kind=row["target_kind"],
                connector_ref=row["connector_ref"],
                action_code=row["action_code"],
                state=row["state"],
                attempt_count=row["attempt_count"],
                started_at=row["started_at"],
                completed_at=row["completed_at"],
                reason_code=(
                    ReceiptReasonCode.STORE_MUTATION_FAILED if row["state"] != "succeeded" else None
                ),
            )
            for row in actions
        ]
        verification_results = [
            ReceiptVerificationResult(
                verification_id=stable_id("verify", row["action_id"]),
                target_version_ref=row["target_version_id"],
                connector_ref=row["connector_ref"],
                verification_code=(
                    VerificationCode.BRANCH_REBUILT
                    if row["action_code"] == ActionCode.REBUILD.value
                    else (
                        VerificationCode.METADATA_UPDATED
                        if event.event_type is EventType.PERMISSION_CHANGE
                        else VerificationCode.PAYLOAD_ABSENT
                    )
                ),
                result=(
                    VerificationStatus.PASSED
                    if row["state"] == "succeeded"
                    and row["read_back_json"]
                    and bool(json.loads(row["read_back_json"]).get("verified"))
                    else VerificationStatus.FAILED
                ),
                checked_at=now,
                reason_code=(
                    None if row["state"] == "succeeded" else ReceiptReasonCode.READ_BACK_FAILED
                ),
            )
            for row in actions
        ]
        verification_results.extend(
            ReceiptVerificationResult(
                verification_id=stable_id("verify", event.event_id, attack_name),
                target_version_ref=event.target_version_id,
                connector_ref="connector://runtime-gate",
                verification_code=VerificationCode.RESURRECTION_BLOCKED,
                result=(VerificationStatus.PASSED if passed else VerificationStatus.FAILED),
                checked_at=now,
                reason_code=(None if passed else ReceiptReasonCode.RESIDUAL_PAYLOAD),
            )
            for attack_name, passed in sorted(resurrection.items())
        )
        residuals = [
            ResidualRisk(risk_code=ReceiptReasonCode.HOST_TRUST, count=1),
            ResidualRisk(risk_code=ReceiptReasonCode.DIRECT_STORE_BYPASS, count=1),
        ]
        if semantic_candidates:
            residuals.append(
                ResidualRisk(
                    risk_code=ReceiptReasonCode.UNREGISTERED_COPY,
                    count=semantic_candidates,
                )
            )
        if failed or rebuild_error or probes["prohibited_disclosures"]:
            residuals.append(
                ResidualRisk(
                    risk_code=ReceiptReasonCode.RESIDUAL_PAYLOAD,
                    count=len(failed) + int(bool(rebuild_error)) + probes["prohibited_disclosures"],
                )
            )
        manifest_row = self.state.fetch_one(
            """
            SELECT * FROM scope_manifests
            WHERE tenant_id=? AND workspace_id=? AND environment_id=? AND manifest_id=?
            """,
            (*self._scope_values(self.scope), run["scope_manifest_id"]),
        )
        if manifest_row is None:
            raise RuntimeError("scope_manifest_missing")
        capabilities = [
            ConnectorCapabilityVersion.model_validate(item)
            for item in json.loads(manifest_row["connector_capability_versions_json"])
        ]
        expected_tracked = sum(
            int(value) for value in json.loads(manifest_row["denominators_json"]).values()
        )
        targets_verified = sum(
            result.result is VerificationStatus.PASSED
            for result in verification_results[: len(actions)]
        )
        body: dict[str, Any] = {
            "receipt_version": "1",
            **self.scope.model_dump(),
            "agent_id": DEMO_AGENT_ID,
            "key_id": "key_demo_01",
            "chain_sequence": sequence,
            "previous_receipt_hash": previous_hash,
            "event_id": event.event_id,
            "run_id": run["run_id"],
            "source_version_ids": [event.target_version_id],
            "policy_version": event.policy_version,
            "scope": ReceiptScope(
                scope_manifest_hash=manifest_row["manifest_hash"],
                scope_selected_by=manifest_row["selected_by"],
                requested_evidence_level=manifest_row["requested_evidence_level"],
                scan_cutoff=manifest_row["scan_cutoff"],
                registered_stores=len(self.registered_stores),
                registered_connectors=len(set(self.connectors.values())),
                reachable_connectors=len(set(self.connectors.values())),
                connector_capability_versions=capabilities,
                unsupported_connectors=[],
                graph_snapshot_hash=run["graph_snapshot_hash"],
            ).model_dump(mode="json"),
            "coverage_level": EvidenceLevel.L3.value,
            "counts": ReceiptCounts(
                expected_tracked=expected_tracked,
                found_tracked=expected_tracked,
                scanner_candidates=scanner_candidates,
                actions_attempted=len(actions),
                actions_succeeded=len(actions) - len(failed),
                actions_failed=len(failed),
                targets_verified=targets_verified,
            ).model_dump(mode="json"),
            "actions": [item.model_dump(mode="json") for item in receipt_actions],
            "verification_results": [item.model_dump(mode="json") for item in verification_results],
            "exclusions": [],
            "unsupported_sinks": [],
            "residual_risks": [item.model_dump(mode="json") for item in residuals],
            "outcome": outcome.value,
            "started_at": run["started_at"],
            "completed_at": _utc_text(now),
            "evidence_manifest_hash": _hash_json(
                {
                    "fixture_manifest_hash": fixture_manifest_hash(self.manifest),
                    "probes": probes,
                    "resurrection": resurrection,
                    "rebuild_error": rebuild_error,
                }
            ),
            "signature_algorithm": "Ed25519",
            "signature": "pending00",
        }
        receipt_key = load_demo_key_fixture("receipt_signer")
        body["signature"] = sign_receipt(body, receipt_key.private_seed)
        return ExecutionReceipt.model_validate(body)

    def get_receipt(self, run_id: str) -> ExecutionReceipt:
        row = self.state.fetch_one(
            """
            SELECT receipt_json FROM receipt_chain
            WHERE tenant_id=? AND workspace_id=? AND environment_id=? AND run_id=?
            """,
            (*self._scope_values(self.scope), run_id),
        )
        if row is None:
            raise NotFoundError("receipt_not_found", "receipt is not available")
        return ExecutionReceipt.model_validate_json(row["receipt_json"])

    def verify_receipts(self, request: ReceiptVerifyRequest) -> ReceiptVerifyResponse:
        receipts = request.receipts
        reasons: list[str] = []
        if not receipts:
            return ReceiptVerifyResponse(
                valid=False, verified_sequence=0, reason_codes=["empty_bundle"]
            )
        start_sequence = request.expected_sequence - len(receipts) + 1
        if start_sequence < 1:
            reasons.append("invalid_expected_sequence")
        current_head = request.prior_trusted_head
        verified_sequence = start_sequence - 1
        common_chain = (
            receipts[0].tenant_id,
            receipts[0].environment_id,
            receipts[0].agent_id,
        )
        for offset, receipt in enumerate(receipts):
            expected = start_sequence + offset
            if (
                receipt.tenant_id,
                receipt.environment_id,
                receipt.agent_id,
            ) != common_chain:
                reasons.append("mixed_receipt_chain")
                break
            if receipt.chain_sequence != expected:
                reasons.append("unexpected_chain_sequence")
                break
            if receipt.previous_receipt_hash != current_head:
                reasons.append("previous_hash_mismatch")
                break
            enrollment = self.state.fetch_one(
                """
                SELECT * FROM key_enrollments
                WHERE tenant_id=? AND workspace_id=? AND environment_id=? AND key_id=?
                """,
                (*self._scope_values(receipt.scope), receipt.key_id),
            )
            if (
                enrollment is None
                or enrollment["status"] != "active"
                or enrollment["signing_role"] != "receipt_signer"
                or enrollment["agent_id"] != receipt.agent_id
            ):
                reasons.append("untrusted_enrollment")
                break
            valid_from = _parse_time(str(enrollment["valid_from"]))
            valid_until = (
                _parse_time(str(enrollment["valid_until"]))
                if enrollment["valid_until"] is not None
                else None
            )
            if receipt.completed_at < valid_from or (
                valid_until is not None and receipt.completed_at >= valid_until
            ):
                reasons.append("enrollment_outside_validity")
                break
            public_key = b64url_decode(str(enrollment["public_key"]))
            document = receipt.model_dump(mode="json", by_alias=True)
            if not verify_receipt_signature(document, public_key):
                reasons.append("invalid_receipt_signature")
                break
            current_head = receipt_entry_hash(document)
            verified_sequence = receipt.chain_sequence
        if not reasons and current_head != request.expected_head:
            reasons.append("trusted_head_mismatch")
        return ReceiptVerifyResponse(
            valid=not reasons,
            verified_sequence=max(0, verified_sequence),
            computed_head=current_head,
            reason_codes=reasons,
        )

    def run_status(self, run_id: str) -> RunStatusResponse:
        run = self.state.fetch_one(
            """
            SELECT * FROM propagation_runs
            WHERE tenant_id=? AND workspace_id=? AND environment_id=? AND run_id=?
            """,
            (*self._scope_values(self.scope), run_id),
        )
        if run is None:
            raise NotFoundError("run_not_found", "propagation run not found")
        actions = self.state.fetch_all(
            """
            SELECT * FROM propagation_actions
            WHERE tenant_id=? AND workspace_id=? AND environment_id=? AND run_id=?
            ORDER BY action_id
            """,
            (*self._scope_values(self.scope), run_id),
        )
        counts: dict[str, int] = {state.value: 0 for state in ActionState}
        for row in actions:
            counts[str(row["state"])] = counts.get(str(row["state"]), 0) + 1
        failures = [
            ReceiptAction(
                action_id=row["action_id"],
                target_version_ref=row["target_version_id"],
                derivative_kind=row["target_kind"],
                connector_ref=row["connector_ref"],
                action_code=row["action_code"],
                state=row["state"],
                attempt_count=row["attempt_count"],
                started_at=row["started_at"],
                completed_at=row["completed_at"],
                reason_code=ReceiptReasonCode.STORE_MUTATION_FAILED,
            )
            for row in actions
            if row["state"] in {"failed", "dead_letter"}
        ]
        return RunStatusResponse(
            **self.scope.model_dump(),
            run_id=run_id,
            event_id=run["event_id"],
            phase=run["phase"],
            counts=counts,
            failures=failures,
            exclusions=[
                ReceiptExclusion.model_validate(item) for item in json.loads(run["exclusions_json"])
            ],
            outcome=run["outcome"],
            started_at=run["started_at"],
            completed_at=run["completed_at"],
        )

    def retry_failed_actions(self, run_id: str) -> int:
        now = _utc_text(self.clock.now())
        with self.state.transaction() as connection:
            rows = connection.execute(
                """
                SELECT action_id FROM propagation_actions
                WHERE tenant_id=? AND workspace_id=? AND environment_id=? AND run_id=?
                  AND state IN ('failed','dead_letter')
                """,
                (*self._scope_values(self.scope), run_id),
            ).fetchall()
            for row in rows:
                for table in ("propagation_actions", "action_outbox"):
                    connection.execute(
                        f"""
                        UPDATE {table} SET state='retryable', last_error_code=NULL,
                            lease_owner=NULL, lease_expires_at=NULL, updated_at=?
                        WHERE tenant_id=? AND workspace_id=? AND environment_id=? AND action_id=?
                        """,
                        (now, *self._scope_values(self.scope), row["action_id"]),
                    )
            return len(rows)

    def process_due_expirations(self) -> list[EventAccepted]:
        now = self.clock.now()
        rows = self.state.fetch_all(
            """
            SELECT * FROM knowledge_objects
            WHERE tenant_id=? AND workspace_id=? AND environment_id=?
              AND kind='source' AND lifecycle_state='active'
              AND valid_until IS NOT NULL AND valid_until <= ?
            ORDER BY version_id
            """,
            (*self._scope_values(self.scope), _utc_text(now)),
        )
        results: list[EventAccepted] = []
        event_key = load_demo_key_fixture("event_issuer")
        for row in rows:
            latest = self.state.latest_source_sequence(
                self.scope, "authority://demo/source-admin", str(row["version_id"])
            )
            event = LifecycleEvent(
                **self.scope.model_dump(),
                event_id=stable_id("evt", row["version_id"], _utc_text(now), "expire"),
                idempotency_key=stable_id("idem", row["version_id"], "expire"),
                issuer_key_id=event_key.key_id,
                audience=DEMO_AGENT_ID,
                authority_ref="authority://demo/source-admin",
                nonce=stable_id("nonce", row["version_id"], _utc_text(now)),
                target_version_id=row["version_id"],
                event_type=EventType.EXPIRE,
                source_sequence=(latest or 0) + 1,
                policy_version=int(row["policy_version"]) + 1,
                occurred_at=now,
                effective_at=now,
                command_expires_at=now + timedelta(minutes=5),
                actor_ref="actor://demo/scheduler",
                reason_code="validity_expired",
            )
            signature = sign_event(event.model_dump(mode="json"), event_key.private_seed)
            results.append(self.accept_event(event, signature))
        return results
