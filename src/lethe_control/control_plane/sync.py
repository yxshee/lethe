"""Data-plane sync client (outbound-only).

The agent initiates every connection: it pulls signed declarative lifecycle
events and pushes metadata-only receipts and run status. Every upload is
validated against the outbound allowlist BEFORE it leaves the process;
event authorization stays with the data-plane service (signature, TTL,
sequence, idempotency are enforced by ``accept_event``), never with
transport authentication.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

import httpx

from lethe_control.control_plane.allowlist import assert_outbound_safe
from lethe_control.errors import LetheError
from lethe_control.models import LifecycleEvent

if TYPE_CHECKING:
    from lethe_control.service import LetheService


class ControlTransport(Protocol):
    def get(
        self, path: str, *, params: dict[str, Any], headers: dict[str, str]
    ) -> tuple[int, Any]: ...

    def post(
        self, path: str, *, json_body: dict[str, Any], headers: dict[str, str]
    ) -> tuple[int, Any]: ...


class HttpxControlTransport:
    def __init__(self, base_url: str, *, timeout: float = 10.0) -> None:
        self._client = httpx.Client(base_url=base_url, timeout=timeout)

    def get(self, path: str, *, params: dict[str, Any], headers: dict[str, str]) -> tuple[int, Any]:
        response = self._client.get(path, params=params, headers=headers)
        return response.status_code, response.json()

    def post(
        self, path: str, *, json_body: dict[str, Any], headers: dict[str, str]
    ) -> tuple[int, Any]:
        response = self._client.post(path, json=json_body, headers=headers)
        return response.status_code, response.json()


class ControlPlaneClient:
    def __init__(self, transport: ControlTransport, *, agent_token: str) -> None:
        self._transport = transport
        self._headers = {"Authorization": f"Bearer {agent_token}"}

    def pull_and_apply(self, service: LetheService, *, cursor: int = 0) -> dict[str, Any]:
        status_code, body = self._transport.get(
            "/control/v1/agents/self/events",
            params={"cursor": cursor},
            headers=self._headers,
        )
        if status_code != 200:
            raise RuntimeError(f"event pull failed: {status_code}")
        applied: list[str] = []
        duplicates = 0
        rejected: list[dict[str, str]] = []
        next_cursor = int(body["cursor"])
        for queued in body["events"]:
            event = LifecycleEvent.model_validate(queued["event"])
            try:
                accepted = service.accept_event(event, str(queued["signature"]))
            except LetheError as error:
                rejected.append({"event_id": event.event_id, "reason_code": error.code})
                continue
            if accepted.duplicate:
                duplicates += 1
            else:
                applied.append(accepted.run_id)
        return {
            "cursor": next_cursor,
            "applied_run_ids": applied,
            "duplicates": duplicates,
            "rejected": rejected,
        }

    def _push(self, document_type: str, path: str, document: dict[str, Any]) -> Any:
        assert_outbound_safe(document_type, document)
        status_code, body = self._transport.post(path, json_body=document, headers=self._headers)
        if status_code != 200:
            raise RuntimeError(f"{document_type} upload failed: {status_code} {body}")
        return body

    def push_receipt(self, service: LetheService, run_id: str) -> Any:
        document = service.get_receipt(run_id).model_dump(mode="json", by_alias=True)
        return self._push("receipt", "/control/v1/uploads/receipts", document)

    def push_status(self, service: LetheService, run_id: str) -> Any:
        document = service.run_status(run_id).model_dump(mode="json", by_alias=True)
        return self._push("run_status", "/control/v1/uploads/status", document)
