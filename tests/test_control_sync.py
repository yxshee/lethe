from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from lethe_control.control_plane.allowlist import OutboundViolation, validate_outbound
from lethe_control.control_plane.app import create_control_app
from lethe_control.control_plane.store import (
    DEMO_AGENT_TOKEN,
    DEMO_OPERATOR_TOKEN,
    ControlPlaneStore,
    seed_demo_enrollments,
)
from lethe_control.control_plane.sync import ControlPlaneClient
from lethe_control.models import ScopeKey
from lethe_control.service import LetheService

DEMO_SCOPE = ScopeKey(tenant_id="ten_demo", workspace_id="ws_demo", environment_id="env_local")


class ClientTransportAdapter:
    """Adapts a fastapi test client to the ControlTransport protocol."""

    def __init__(self, client: TestClient) -> None:
        self._client = client

    def get(self, path: str, *, params: dict[str, Any], headers: dict[str, str]) -> tuple[int, Any]:
        response = self._client.get(path, params=params, headers=headers)
        return response.status_code, response.json()

    def post(
        self, path: str, *, json_body: dict[str, Any], headers: dict[str, str]
    ) -> tuple[int, Any]:
        response = self._client.post(path, json=json_body, headers=headers)
        return response.status_code, response.json()


class RecordingTransport:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def get(self, path: str, *, params: dict[str, Any], headers: dict[str, str]) -> tuple[int, Any]:
        self.calls.append(f"GET {path}")
        return 200, {"events": [], "cursor": 0}

    def post(
        self, path: str, *, json_body: dict[str, Any], headers: dict[str, str]
    ) -> tuple[int, Any]:
        self.calls.append(f"POST {path}")
        return 200, {}


def _delete_event_fixture() -> tuple[dict[str, Any], str]:
    path = Path(__file__).resolve().parents[1] / "fixtures/demo-events.json"
    for fixture in json.loads(path.read_text(encoding="utf-8"))["events"]:
        if fixture["event"]["event_type"] == "delete":
            return dict(fixture["event"]), str(fixture["signature"])
    raise AssertionError("missing delete fixture")


@pytest.fixture
def control_client(tmp_path: Path) -> TestClient:
    store = ControlPlaneStore(tmp_path / "control_plane.db")
    seed_demo_enrollments(store)
    return TestClient(create_control_app(store))


def test_validator_runs_before_upload() -> None:
    transport = RecordingTransport()
    client = ControlPlaneClient(transport, agent_token=DEMO_AGENT_TOKEN)

    with pytest.raises(OutboundViolation):
        client._push(
            "receipt",
            "/control/v1/uploads/receipts",
            {"run_id": "run-x", "content": "leaked"},
        )
    assert transport.calls == []


def test_end_to_end_operator_to_verified_ledger(
    control_client: TestClient, service: LetheService
) -> None:
    event, signature = _delete_event_fixture()
    enqueue = control_client.post(
        "/control/v1/operator/events",
        json=event,
        headers={
            "Authorization": f"Bearer {DEMO_OPERATOR_TOKEN}",
            "X-Lethe-Event-Signature": signature,
        },
    )
    assert enqueue.status_code == 202

    tampered = dict(event)
    tampered["event_id"] = event["event_id"] + "-tampered"
    tampered["nonce"] = "tampered-nonce"
    enqueue_tampered = control_client.post(
        "/control/v1/operator/events",
        json=tampered,
        headers={
            "Authorization": f"Bearer {DEMO_OPERATOR_TOKEN}",
            "X-Lethe-Event-Signature": "bad-signature",
        },
    )
    assert enqueue_tampered.status_code == 202  # queue stores opaque envelopes

    client = ControlPlaneClient(
        ClientTransportAdapter(control_client), agent_token=DEMO_AGENT_TOKEN
    )
    summary = client.pull_and_apply(service)
    assert len(summary["applied_run_ids"]) == 1
    assert summary["rejected"] and summary["rejected"][0]["event_id"].endswith("-tampered")

    replay = client.pull_and_apply(service)
    assert replay["applied_run_ids"] == []
    assert replay["duplicates"] == 1

    run_id = summary["applied_run_ids"][0]
    service.drain_actions(force=True)
    assert client.push_receipt(service, run_id)["stored"] is True
    assert client.push_status(service, run_id)["stored"] is True

    listing = control_client.get(
        "/control/v1/receipts", headers={"Authorization": f"Bearer {DEMO_AGENT_TOKEN}"}
    ).json()
    assert [entry["run_id"] for entry in listing] == [run_id]
    stored_document = listing[0]["document"]
    assert validate_outbound("receipt", stored_document) == []
    assert "amber-lantern-731" not in json.dumps(stored_document)
