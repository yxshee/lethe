"""Control-plane state on a dedicated SQLite file.

Shared logical tenancy: every table is keyed by the composite scope and
every query is scope-qualified. Tenant context always comes from the
authenticated enrollment, never from payloads.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import Any

from lethe_control.models import ScopeKey

_SCHEMA = """
CREATE TABLE IF NOT EXISTS enrollments (
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    environment_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    role TEXT NOT NULL,
    token_digest TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    PRIMARY KEY (tenant_id, workspace_id, environment_id, agent_id, role)
);
CREATE TABLE IF NOT EXISTS event_queue (
    queue_seq INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    environment_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    event_json TEXT NOT NULL,
    signature TEXT NOT NULL,
    enqueued_at TEXT NOT NULL,
    UNIQUE (tenant_id, workspace_id, environment_id, event_id)
);
CREATE TABLE IF NOT EXISTS receipt_ledger (
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    environment_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    document_json TEXT NOT NULL,
    uploaded_at TEXT NOT NULL,
    PRIMARY KEY (tenant_id, workspace_id, environment_id, run_id)
);
CREATE TABLE IF NOT EXISTS status_ledger (
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    environment_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    document_json TEXT NOT NULL,
    uploaded_at TEXT NOT NULL,
    PRIMARY KEY (tenant_id, workspace_id, environment_id, run_id)
);
CREATE TABLE IF NOT EXISTS rejections (
    rejection_seq INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id TEXT,
    workspace_id TEXT,
    environment_id TEXT,
    document_type TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    recorded_at TEXT NOT NULL
);
"""


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _utc_now_text() -> str:
    return datetime.now(tz=UTC).isoformat().replace("+00:00", "Z")


class ControlPlaneStore:
    def __init__(self, path: Path | str) -> None:
        if isinstance(path, Path):
            path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(str(path), check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._lock = RLock()
        with self._lock:
            self._connection.executescript(_SCHEMA)
            self._connection.commit()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            try:
                yield self._connection
                self._connection.commit()
            except Exception:
                self._connection.rollback()
                raise

    def enroll_agent(self, scope: ScopeKey, *, agent_id: str, role: str, token: str) -> None:
        with self._transaction() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO enrollments (
                    tenant_id, workspace_id, environment_id, agent_id, role,
                    token_digest, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    scope.tenant_id,
                    scope.workspace_id,
                    scope.environment_id,
                    agent_id,
                    role,
                    token_digest(token),
                    _utc_now_text(),
                ),
            )

    def resolve_token(self, token: str) -> sqlite3.Row | None:
        with self._lock:
            row: sqlite3.Row | None = self._connection.execute(
                "SELECT * FROM enrollments WHERE token_digest=?",
                (token_digest(token),),
            ).fetchone()
            return row

    def enqueue_event(
        self, scope: ScopeKey, *, event_id: str, event_json: str, signature: str
    ) -> int:
        with self._transaction() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO event_queue (
                    tenant_id, workspace_id, environment_id, event_id,
                    event_json, signature, enqueued_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    scope.tenant_id,
                    scope.workspace_id,
                    scope.environment_id,
                    event_id,
                    event_json,
                    signature,
                    _utc_now_text(),
                ),
            )
            if cursor.rowcount == 0:
                existing = connection.execute(
                    """
                    SELECT queue_seq FROM event_queue
                    WHERE tenant_id=? AND workspace_id=? AND environment_id=? AND event_id=?
                    """,
                    (scope.tenant_id, scope.workspace_id, scope.environment_id, event_id),
                ).fetchone()
                return int(existing["queue_seq"])
            return int(cursor.lastrowid or 0)

    def events_after(self, scope: ScopeKey, cursor: int, *, limit: int = 100) -> list[sqlite3.Row]:
        with self._lock:
            return list(
                self._connection.execute(
                    """
                    SELECT * FROM event_queue
                    WHERE tenant_id=? AND workspace_id=? AND environment_id=?
                      AND queue_seq > ?
                    ORDER BY queue_seq ASC LIMIT ?
                    """,
                    (
                        scope.tenant_id,
                        scope.workspace_id,
                        scope.environment_id,
                        cursor,
                        limit,
                    ),
                ).fetchall()
            )

    def record_document(
        self, table: str, scope: ScopeKey, *, run_id: str, document: dict[str, Any]
    ) -> None:
        if table not in {"receipt_ledger", "status_ledger"}:
            raise ValueError("unknown ledger table")
        with self._transaction() as connection:
            connection.execute(
                f"""
                INSERT OR REPLACE INTO {table} (
                    tenant_id, workspace_id, environment_id, run_id,
                    document_json, uploaded_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    scope.tenant_id,
                    scope.workspace_id,
                    scope.environment_id,
                    run_id,
                    json.dumps(document, sort_keys=True, separators=(",", ":")),
                    _utc_now_text(),
                ),
            )

    def documents(self, table: str, scope: ScopeKey) -> list[sqlite3.Row]:
        if table not in {"receipt_ledger", "status_ledger"}:
            raise ValueError("unknown ledger table")
        with self._lock:
            return list(
                self._connection.execute(
                    f"""
                    SELECT * FROM {table}
                    WHERE tenant_id=? AND workspace_id=? AND environment_id=?
                    ORDER BY run_id
                    """,
                    (scope.tenant_id, scope.workspace_id, scope.environment_id),
                ).fetchall()
            )

    def record_rejection(
        self, scope: ScopeKey | None, *, document_type: str, reason_code: str
    ) -> None:
        with self._transaction() as connection:
            connection.execute(
                """
                INSERT INTO rejections (
                    tenant_id, workspace_id, environment_id,
                    document_type, reason_code, recorded_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    scope.tenant_id if scope else None,
                    scope.workspace_id if scope else None,
                    scope.environment_id if scope else None,
                    document_type,
                    reason_code,
                    _utc_now_text(),
                ),
            )


DEMO_AGENT_TOKEN = "lethe-cp-agent-demo"
DEMO_OPERATOR_TOKEN = "lethe-cp-operator-demo"
DEMO_FOREIGN_TOKEN = "lethe-cp-foreign-demo"

_DEMO_SCOPE = ScopeKey(tenant_id="ten_demo", workspace_id="ws_demo", environment_id="env_local")
_FOREIGN_SCOPE = ScopeKey(
    tenant_id="ten_other", workspace_id="ws_other", environment_id="env_other"
)


def seed_demo_enrollments(store: ControlPlaneStore) -> None:
    """Fixture enrollments for the alpha; deliberately non-production tokens."""

    store.enroll_agent(_DEMO_SCOPE, agent_id="agent_demo_01", role="agent", token=DEMO_AGENT_TOKEN)
    store.enroll_agent(
        _DEMO_SCOPE, agent_id="operator_demo_01", role="operator", token=DEMO_OPERATOR_TOKEN
    )
    store.enroll_agent(
        _FOREIGN_SCOPE, agent_id="agent_foreign_01", role="agent", token=DEMO_FOREIGN_TOKEN
    )
