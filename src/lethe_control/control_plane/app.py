"""Control-plane FastAPI app (alpha).

Separate from the data-plane app and intentionally outside the data-plane
OpenAPI drift gate. The data-plane agent initiates every connection; this
service never reaches into a customer network. Tenant context derives from
the authenticated enrollment and is cross-checked against — never trusted
from — payloads. Uploads are re-validated against the outbound allowlist
defensively even though agents validate before signing.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Query, status

from lethe_control.control_plane.allowlist import validate_outbound
from lethe_control.control_plane.models import (
    EventPullResponse,
    QueuedControlEvent,
    UploadAccepted,
)
from lethe_control.control_plane.store import ControlPlaneStore
from lethe_control.models import LifecycleEvent, ScopeKey


class SignedEventEnvelope(LifecycleEvent):
    """Operator-enqueued lifecycle event plus issuer signature."""


def _bearer(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="bearer token required")
    return authorization.partition(" ")[2]


def create_control_app(store: ControlPlaneStore) -> FastAPI:
    app = FastAPI(title="Lethe control plane — alpha", version="0.2.0")
    app.state.control_store = store

    def _enrollment(authorization: str | None, *, role: str) -> tuple[ScopeKey, str]:
        row = store.resolve_token(_bearer(authorization))
        if row is None:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="unknown enrollment")
        if str(row["role"]) != role:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="role_not_permitted")
        scope = ScopeKey(
            tenant_id=str(row["tenant_id"]),
            workspace_id=str(row["workspace_id"]),
            environment_id=str(row["environment_id"]),
        )
        return scope, str(row["agent_id"])

    def _require_scope_match(
        enrolled: ScopeKey, document: dict[str, Any], document_type: str
    ) -> None:
        payload_scope = {
            "tenant_id": document.get("tenant_id"),
            "workspace_id": document.get("workspace_id"),
            "environment_id": document.get("environment_id"),
        }
        if payload_scope != enrolled.model_dump():
            store.record_rejection(
                enrolled, document_type=document_type, reason_code="scope_mismatch"
            )
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="scope_mismatch")

    @app.post("/control/v1/operator/events", status_code=status.HTTP_202_ACCEPTED)
    def enqueue_event(
        body: SignedEventEnvelope,
        authorization: str | None = Header(default=None),
        event_signature: str | None = Header(default=None, alias="X-Lethe-Event-Signature"),
    ) -> dict[str, int | str]:
        scope, _agent = _enrollment(authorization, role="operator")
        if not event_signature:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="missing_event_signature"
            )
        event = LifecycleEvent.model_validate(body.model_dump(mode="json", by_alias=True))
        _require_scope_match(scope, event.model_dump(mode="json", by_alias=True), "control_event")
        queue_seq = store.enqueue_event(
            scope,
            event_id=event.event_id,
            event_json=event.model_dump_json(by_alias=True),
            signature=event_signature,
        )
        return {"queue_seq": queue_seq, "event_id": event.event_id}

    @app.get("/control/v1/agents/self/events", response_model=EventPullResponse)
    def pull_events(
        cursor: int = Query(default=0, ge=0),
        limit: int = Query(default=100, ge=1, le=500),
        authorization: str | None = Header(default=None),
    ) -> EventPullResponse:
        scope, _agent = _enrollment(authorization, role="agent")
        rows = store.events_after(scope, cursor, limit=limit)
        events = [
            QueuedControlEvent(
                queue_seq=int(row["queue_seq"]),
                event=LifecycleEvent.model_validate(json.loads(row["event_json"])),
                signature=str(row["signature"]),
                enqueued_at=datetime.fromisoformat(str(row["enqueued_at"]).replace("Z", "+00:00")),
            )
            for row in rows
        ]
        next_cursor = events[-1].queue_seq if events else cursor
        return EventPullResponse(events=events, cursor=next_cursor)

    def _accept_upload(
        table: str,
        document_type: str,
        document: dict[str, Any],
        authorization: str | None,
    ) -> UploadAccepted:
        scope, _agent = _enrollment(authorization, role="agent")
        violations = validate_outbound(document_type, document)
        if violations:
            store.record_rejection(
                scope, document_type=document_type, reason_code="allowlist_violation"
            )
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={"reason_code": "allowlist_violation", "violations": violations[:8]},
            )
        _require_scope_match(scope, document, document_type)
        run_id = str(document.get("run_id", ""))
        if not run_id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="missing run_id"
            )
        store.record_document(table, scope, run_id=run_id, document=document)
        return UploadAccepted(run_id=run_id, stored=True)

    @app.post("/control/v1/uploads/receipts", response_model=UploadAccepted)
    def upload_receipt(
        body: dict[str, Any], authorization: str | None = Header(default=None)
    ) -> UploadAccepted:
        return _accept_upload("receipt_ledger", "receipt", body, authorization)

    @app.post("/control/v1/uploads/status", response_model=UploadAccepted)
    def upload_status(
        body: dict[str, Any], authorization: str | None = Header(default=None)
    ) -> UploadAccepted:
        return _accept_upload("status_ledger", "run_status", body, authorization)

    @app.get("/control/v1/receipts")
    def list_receipts(
        authorization: str | None = Header(default=None),
    ) -> list[dict[str, Any]]:
        scope, _agent = _enrollment(authorization, role="agent")
        return [
            {
                "run_id": str(row["run_id"]),
                "uploaded_at": str(row["uploaded_at"]),
                "document": json.loads(row["document_json"]),
            }
            for row in store.documents("receipt_ledger", scope)
        ]

    return app
