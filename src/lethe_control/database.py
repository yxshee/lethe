from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path
from typing import Any, cast

from lethe_control.models import (
    ACLPolicy,
    ExecutionReceipt,
    KnowledgeEnvelope,
    LifecycleEvent,
    LifecycleState,
    ScopeKey,
    ScopeManifest,
)


class DatabaseIntegrityError(RuntimeError):
    pass


def utc_now() -> datetime:
    return datetime.now(UTC)


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _json(value: Any) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json", by_alias=True)
    elif isinstance(value, Mapping):
        value = {
            key: item.model_dump(mode="json", by_alias=True)
            if hasattr(item, "model_dump")
            else item
            for key, item in value.items()
        }
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        value = [
            item.model_dump(mode="json", by_alias=True) if hasattr(item, "model_dump") else item
            for item in value
        ]
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


class StateStore:
    _locks_guard = threading.Lock()
    _writer_locks: dict[Path, threading.RLock] = {}

    def __init__(self, database_path: Path | str, *, busy_timeout_ms: int = 5_000) -> None:
        self.database_path = Path(database_path).expanduser().resolve()
        self.busy_timeout_ms = busy_timeout_ms
        with self._locks_guard:
            self._writer_lock = self._writer_locks.setdefault(self.database_path, threading.RLock())

    def initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.migrate()
        with self.connect() as connection:
            foreign_key_errors = connection.execute("PRAGMA foreign_key_check").fetchall()
            quick_check = connection.execute("PRAGMA quick_check").fetchone()
        if foreign_key_errors:
            raise DatabaseIntegrityError("database contains invalid foreign keys")
        if quick_check is None or quick_check[0] != "ok":
            raise DatabaseIntegrityError("SQLite quick_check failed")

    def _open(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.database_path,
            timeout=self.busy_timeout_ms / 1_000,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(f"PRAGMA busy_timeout = {self.busy_timeout_ms:d}")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = FULL")
        return connection

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = self._open()
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def transaction(self, *, immediate: bool = True) -> Iterator[sqlite3.Connection]:
        with self._writer_lock:
            connection = self._open()
            try:
                connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
                yield connection
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
            finally:
                connection.close()

    def migrate(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        migration_root = resources.files("lethe_control.migrations")
        migrations = sorted(
            (
                resource
                for resource in migration_root.iterdir()
                if resource.name[:4].isdigit() and resource.name.endswith(".sql")
            ),
            key=lambda resource: resource.name,
        )
        with self._writer_lock, self.connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    name TEXT NOT NULL UNIQUE,
                    checksum TEXT NOT NULL,
                    applied_at TEXT NOT NULL
                )
                """
            )
            for migration in migrations:
                version = int(migration.name.split("_", 1)[0])
                script = migration.read_text(encoding="utf-8")
                checksum = hashlib.sha256(script.encode("utf-8")).hexdigest()
                applied = connection.execute(
                    "SELECT name, checksum FROM schema_migrations WHERE version = ?", (version,)
                ).fetchone()
                if applied is not None:
                    if applied["name"] != migration.name or applied["checksum"] != checksum:
                        raise DatabaseIntegrityError(
                            f"migration {version:04d} differs from applied migration"
                        )
                    continue
                try:
                    connection.executescript("BEGIN IMMEDIATE;\n" + script)
                    connection.execute(
                        """
                        INSERT INTO schema_migrations (version, name, checksum, applied_at)
                        VALUES (?, ?, ?, ?)
                        """,
                        (version, migration.name, checksum, _timestamp(utc_now())),
                    )
                    connection.commit()
                except BaseException:
                    connection.rollback()
                    raise

    def schema_version(self) -> int:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
            ).fetchone()
        return int(row[0]) if row is not None else 0

    def table_names(self) -> set[str]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT name FROM sqlite_schema WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        return {str(row["name"]) for row in rows}

    def fetch_one(
        self, sql: str, parameters: Sequence[Any] | Mapping[str, Any] = ()
    ) -> sqlite3.Row | None:
        with self.connect() as connection:
            return cast(sqlite3.Row | None, connection.execute(sql, parameters).fetchone())

    def fetch_all(
        self, sql: str, parameters: Sequence[Any] | Mapping[str, Any] = ()
    ) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return list(connection.execute(sql, parameters).fetchall())

    def insert_knowledge_object(
        self, connection: sqlite3.Connection, envelope: KnowledgeEnvelope
    ) -> None:
        values = envelope.model_dump(mode="json")
        connection.execute(
            """
            INSERT INTO knowledge_objects (
                tenant_id, workspace_id, environment_id, version_id, object_id,
                schema_version, kind, lifecycle_state, parent_version_ids_json,
                root_version_ids_json, created_at, valid_from, valid_until,
                policy_ref, policy_version, acl_ref, supersedes_version_id,
                content_ref, local_content_fingerprint, provenance_json, governance_json
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                envelope.tenant_id,
                envelope.workspace_id,
                envelope.environment_id,
                envelope.version_id,
                envelope.object_id,
                envelope.schema_version,
                envelope.kind.value,
                envelope.lifecycle_state.value,
                _json(values["parent_version_ids"]),
                _json(values["root_version_ids"]),
                _timestamp(envelope.created_at),
                _timestamp(envelope.valid_from),
                _timestamp(envelope.valid_until) if envelope.valid_until else None,
                envelope.policy_ref,
                envelope.policy_version,
                envelope.acl_ref,
                envelope.supersedes_version_id,
                envelope.content_ref,
                envelope.local_content_fingerprint,
                _json(envelope.provenance),
                _json(envelope.governance),
            ),
        )
        for root_version_id in envelope.root_version_ids:
            connection.execute(
                """
                INSERT INTO knowledge_roots (
                    tenant_id, workspace_id, environment_id, version_id, root_version_id
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (*self._scope_values(envelope.scope), envelope.version_id, root_version_id),
            )
        for parent_version_id in envelope.parent_version_ids:
            connection.execute(
                """
                INSERT INTO lineage_edges (
                    tenant_id, workspace_id, environment_id,
                    parent_version_id, child_version_id, activity_type, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    *self._scope_values(envelope.scope),
                    parent_version_id,
                    envelope.version_id,
                    envelope.provenance.activity_type,
                    _timestamp(envelope.created_at),
                ),
            )

    def store_knowledge_object(self, envelope: KnowledgeEnvelope) -> None:
        with self.transaction() as connection:
            self.insert_knowledge_object(connection, envelope)

    def get_knowledge_object(self, scope: ScopeKey, version_id: str) -> KnowledgeEnvelope | None:
        row = self.fetch_one(
            """
            SELECT * FROM knowledge_objects
            WHERE tenant_id = ? AND workspace_id = ? AND environment_id = ?
              AND version_id = ?
            """,
            (*self._scope_values(scope), version_id),
        )
        if row is None:
            return None
        return self._knowledge_from_row(row)

    def get_knowledge_objects(
        self, scope: ScopeKey, version_ids: Sequence[str]
    ) -> list[KnowledgeEnvelope]:
        if not version_ids:
            return []
        objects: list[KnowledgeEnvelope] = []
        with self.connect() as connection:
            for offset in range(0, len(version_ids), 900):
                batch = version_ids[offset : offset + 900]
                placeholders = ",".join("?" for _ in batch)
                rows = connection.execute(
                    f"""
                    SELECT * FROM knowledge_objects
                    WHERE tenant_id=? AND workspace_id=? AND environment_id=?
                      AND version_id IN ({placeholders})
                    """,
                    (*self._scope_values(scope), *batch),
                ).fetchall()
                objects.extend(self._knowledge_from_row(row) for row in rows)
        return objects

    def set_lifecycle_state(
        self,
        connection: sqlite3.Connection,
        scope: ScopeKey,
        version_id: str,
        state: LifecycleState,
    ) -> bool:
        cursor = connection.execute(
            """
            UPDATE knowledge_objects SET lifecycle_state = ?
            WHERE tenant_id = ? AND workspace_id = ? AND environment_id = ?
              AND version_id = ?
            """,
            (state.value, *self._scope_values(scope), version_id),
        )
        return cursor.rowcount == 1

    def descendant_version_ids(
        self, scope: ScopeKey, version_id: str, *, include_self: bool = False
    ) -> list[str]:
        rows = self.fetch_all(
            """
            WITH RECURSIVE descendants(version_id) AS (
                SELECT child_version_id
                FROM lineage_edges
                WHERE tenant_id = ? AND workspace_id = ? AND environment_id = ?
                  AND parent_version_id = ?
                UNION
                SELECT edge.child_version_id
                FROM lineage_edges AS edge
                JOIN descendants AS prior ON edge.parent_version_id = prior.version_id
                WHERE edge.tenant_id = ? AND edge.workspace_id = ? AND edge.environment_id = ?
            )
            SELECT version_id FROM descendants ORDER BY version_id
            """,
            (
                *self._scope_values(scope),
                version_id,
                *self._scope_values(scope),
            ),
        )
        descendants = [str(row["version_id"]) for row in rows]
        return [version_id, *descendants] if include_self else descendants

    def record_lifecycle_event(
        self,
        connection: sqlite3.Connection,
        event: LifecycleEvent,
        *,
        received_at: datetime,
        run_id: str | None = None,
        assertion_status: str = "accepted",
        rejection_reason_code: str | None = None,
    ) -> tuple[sqlite3.Row, bool]:
        existing = connection.execute(
            """
            SELECT * FROM lifecycle_events
            WHERE tenant_id = ? AND workspace_id = ? AND environment_id = ?
              AND authority_ref = ? AND target_version_id = ? AND idempotency_key = ?
            """,
            (
                *self._scope_values(event.scope),
                event.authority_ref,
                event.target_version_id,
                event.idempotency_key,
            ),
        ).fetchone()
        if existing is not None:
            return existing, False
        connection.execute(
            """
            INSERT INTO lifecycle_events (
                tenant_id, workspace_id, environment_id, event_id, schema_version,
                idempotency_key, issuer_key_id, audience, authority_ref, nonce,
                target_version_id, event_type, source_sequence, policy_version,
                occurred_at, effective_at, command_expires_at, actor_ref, reason_code,
                correction_json, permission_change_json, assertion_status,
                rejection_reason_code, received_at, run_id
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                *self._scope_values(event.scope),
                event.event_id,
                event.schema_version,
                event.idempotency_key,
                event.issuer_key_id,
                event.audience,
                event.authority_ref,
                event.nonce,
                event.target_version_id,
                event.event_type.value,
                event.source_sequence,
                event.policy_version,
                _timestamp(event.occurred_at),
                _timestamp(event.effective_at),
                _timestamp(event.command_expires_at),
                event.actor_ref,
                event.reason_code,
                _json(event.correction) if event.correction else None,
                _json(event.permission_change) if event.permission_change else None,
                assertion_status,
                rejection_reason_code,
                _timestamp(received_at),
                run_id,
            ),
        )
        row = connection.execute(
            """
            SELECT * FROM lifecycle_events
            WHERE tenant_id = ? AND workspace_id = ? AND environment_id = ? AND event_id = ?
            """,
            (*self._scope_values(event.scope), event.event_id),
        ).fetchone()
        if row is None:
            raise DatabaseIntegrityError("event insert did not produce a readable row")
        return row, True

    def latest_source_sequence(
        self, scope: ScopeKey, authority_ref: str, target_version_id: str
    ) -> int | None:
        row = self.fetch_one(
            """
            SELECT MAX(source_sequence) AS source_sequence
            FROM lifecycle_events
            WHERE tenant_id = ? AND workspace_id = ? AND environment_id = ?
              AND authority_ref = ? AND target_version_id = ? AND assertion_status = 'accepted'
            """,
            (*self._scope_values(scope), authority_ref, target_version_id),
        )
        if row is None or row["source_sequence"] is None:
            return None
        return int(row["source_sequence"])

    def insert_scope_manifest(
        self, connection: sqlite3.Connection, manifest: ScopeManifest
    ) -> None:
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
                *self._scope_values(manifest.scope),
                manifest.manifest_id,
                manifest.schema_version,
                manifest.manifest_hash,
                manifest.selected_by,
                manifest.requested_evidence_level.value,
                _timestamp(manifest.scan_cutoff),
                _json(manifest.registered_store_refs),
                _json(manifest.registered_connector_refs),
                _json(manifest.connector_capability_versions),
                _json(manifest.derivative_classes),
                _json(manifest.freshness_cursors),
                _json(manifest.denominators),
                _json(manifest.exclusions),
                _timestamp(manifest.created_at),
            ),
        )

    def insert_acl_policy(self, connection: sqlite3.Connection, policy: ACLPolicy) -> None:
        connection.execute(
            """
            INSERT INTO acl_policies (
                tenant_id, workspace_id, environment_id, acl_ref, schema_version,
                policy_version, allowed_principal_refs_json, denied_principal_refs_json,
                allowed_group_refs_json, denied_group_refs_json, permitted_purpose_refs_json,
                permitted_actions_json, valid_from, valid_until, source_authority_ref,
                group_snapshot_version, group_snapshot_expires_at, created_at, signature
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                *self._scope_values(policy.scope),
                policy.acl_ref,
                policy.schema_version,
                policy.policy_version,
                _json(policy.allowed_principal_refs),
                _json(policy.denied_principal_refs),
                _json(policy.allowed_group_refs),
                _json(policy.denied_group_refs),
                _json(policy.permitted_purpose_refs),
                _json(policy.permitted_actions),
                _timestamp(policy.valid_from),
                _timestamp(policy.valid_until) if policy.valid_until else None,
                policy.source_authority_ref,
                policy.group_snapshot_version,
                _timestamp(policy.group_snapshot_expires_at),
                _timestamp(policy.created_at),
                policy.signature,
            ),
        )

    def install_tombstone(
        self,
        connection: sqlite3.Connection,
        *,
        scope: ScopeKey,
        target_version_id: str,
        event_id: str,
        object_kind: str,
        reason_code: str,
        policy_version: int,
        created_at: datetime,
        fingerprint_key_version: str | None = None,
        local_content_fingerprint: str | None = None,
    ) -> None:
        connection.execute(
            """
            INSERT INTO tombstones (
                tenant_id, workspace_id, environment_id, target_version_id, event_id,
                object_kind, fingerprint_key_version, local_content_fingerprint,
                reason_code, policy_version, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (tenant_id, workspace_id, environment_id, target_version_id)
            DO UPDATE SET
                event_id = excluded.event_id,
                reason_code = excluded.reason_code,
                policy_version = MAX(tombstones.policy_version, excluded.policy_version),
                created_at = excluded.created_at
            """,
            (
                *self._scope_values(scope),
                target_version_id,
                event_id,
                object_kind,
                fingerprint_key_version,
                local_content_fingerprint,
                reason_code,
                policy_version,
                _timestamp(created_at),
            ),
        )

    def has_tombstone(self, scope: ScopeKey, target_version_id: str) -> bool:
        row = self.fetch_one(
            """
            SELECT 1 FROM tombstones
            WHERE tenant_id = ? AND workspace_id = ? AND environment_id = ?
              AND target_version_id = ?
            """,
            (*self._scope_values(scope), target_version_id),
        )
        return row is not None

    def append_receipt(
        self,
        connection: sqlite3.Connection,
        receipt: ExecutionReceipt,
        *,
        entry_hash: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO receipt_chain (
                tenant_id, workspace_id, environment_id, agent_id, chain_sequence,
                run_id, event_id, key_id, previous_receipt_hash, entry_hash,
                receipt_json, signed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                *self._scope_values(receipt.scope),
                receipt.agent_id,
                receipt.chain_sequence,
                receipt.run_id,
                receipt.event_id,
                receipt.key_id,
                receipt.previous_receipt_hash,
                entry_hash,
                _json(receipt),
                _timestamp(receipt.completed_at),
            ),
        )

    def receipt_head(self, scope: ScopeKey, agent_id: str) -> sqlite3.Row | None:
        return self.fetch_one(
            """
            SELECT chain_sequence, entry_hash, key_id, run_id
            FROM receipt_chain
            WHERE tenant_id = ? AND environment_id = ? AND agent_id = ?
            ORDER BY chain_sequence DESC LIMIT 1
            """,
            (scope.tenant_id, scope.environment_id, agent_id),
        )

    @staticmethod
    def _scope_values(scope: ScopeKey) -> tuple[str, str, str]:
        return scope.tenant_id, scope.workspace_id, scope.environment_id

    @staticmethod
    def _knowledge_from_row(row: sqlite3.Row) -> KnowledgeEnvelope:
        return KnowledgeEnvelope.model_validate(
            {
                "schema_version": row["schema_version"],
                "tenant_id": row["tenant_id"],
                "workspace_id": row["workspace_id"],
                "environment_id": row["environment_id"],
                "object_id": row["object_id"],
                "version_id": row["version_id"],
                "kind": row["kind"],
                "parent_version_ids": json.loads(row["parent_version_ids_json"]),
                "root_version_ids": json.loads(row["root_version_ids_json"]),
                "lifecycle_state": row["lifecycle_state"],
                "created_at": row["created_at"],
                "valid_from": row["valid_from"],
                "valid_until": row["valid_until"],
                "policy_ref": row["policy_ref"],
                "policy_version": row["policy_version"],
                "acl_ref": row["acl_ref"],
                "supersedes_version_id": row["supersedes_version_id"],
                "content_ref": row["content_ref"],
                "local_content_fingerprint": row["local_content_fingerprint"],
                "provenance": json.loads(row["provenance_json"]),
                "governance": json.loads(row["governance_json"]),
            }
        )
