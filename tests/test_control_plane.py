from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from lethe_control.control_plane.app import create_control_app
from lethe_control.control_plane.store import (
    DEMO_AGENT_TOKEN,
    DEMO_FOREIGN_TOKEN,
    DEMO_OPERATOR_TOKEN,
    ControlPlaneStore,
    seed_demo_enrollments,
)


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _delete_event_fixture() -> tuple[dict[str, Any], str]:
    path = Path(__file__).resolve().parents[1] / "fixtures/demo-events.json"
    for fixture in json.loads(path.read_text(encoding="utf-8"))["events"]:
        if fixture["event"]["event_type"] == "delete":
            return dict(fixture["event"]), str(fixture["signature"])
    raise AssertionError("missing delete fixture")


@pytest.fixture
def control_store(tmp_path: Path) -> ControlPlaneStore:
    store = ControlPlaneStore(tmp_path / "control_plane.db")
    seed_demo_enrollments(store)
    return store


@pytest.fixture
def control_client(control_store: ControlPlaneStore) -> TestClient:
    return TestClient(create_control_app(control_store))


def _enqueue_delete(client: TestClient) -> dict[str, Any]:
    event, signature = _delete_event_fixture()
    response = client.post(
        "/control/v1/operator/events",
        json=event,
        headers={**_bearer(DEMO_OPERATOR_TOKEN), "X-Lethe-Event-Signature": signature},
    )
    assert response.status_code == 202, response.text
    return dict(response.json())


def test_unknown_token_rejected(control_client: TestClient) -> None:
    response = control_client.get("/control/v1/agents/self/events", headers=_bearer("not-a-token"))
    assert response.status_code == 403


def test_role_enforcement(control_client: TestClient) -> None:
    event, signature = _delete_event_fixture()
    as_agent = control_client.post(
        "/control/v1/operator/events",
        json=event,
        headers={**_bearer(DEMO_AGENT_TOKEN), "X-Lethe-Event-Signature": signature},
    )
    assert as_agent.status_code == 403

    as_operator = control_client.get(
        "/control/v1/agents/self/events", headers=_bearer(DEMO_OPERATOR_TOKEN)
    )
    assert as_operator.status_code == 403


def test_tenancy_isolation_on_pull_and_listing(control_client: TestClient) -> None:
    _enqueue_delete(control_client)

    demo = control_client.get("/control/v1/agents/self/events", headers=_bearer(DEMO_AGENT_TOKEN))
    assert demo.status_code == 200
    assert len(demo.json()["events"]) == 1

    foreign = control_client.get(
        "/control/v1/agents/self/events", headers=_bearer(DEMO_FOREIGN_TOKEN)
    )
    assert foreign.status_code == 200
    assert foreign.json()["events"] == []

    foreign_receipts = control_client.get(
        "/control/v1/receipts", headers=_bearer(DEMO_FOREIGN_TOKEN)
    )
    assert foreign_receipts.status_code == 200
    assert foreign_receipts.json() == []


def test_scope_mismatch_is_rejected_and_audited(
    control_client: TestClient, control_store: ControlPlaneStore
) -> None:
    event, signature = _delete_event_fixture()
    event["tenant_id"] = "ten_other"
    response = control_client.post(
        "/control/v1/operator/events",
        json=event,
        headers={**_bearer(DEMO_OPERATOR_TOKEN), "X-Lethe-Event-Signature": signature},
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "scope_mismatch"

    rejections = control_store._connection.execute("SELECT * FROM rejections").fetchall()
    assert len(rejections) == 1
    assert rejections[0]["reason_code"] == "scope_mismatch"
    stored_text = json.dumps([dict(row) for row in rejections])
    assert "amber" not in stored_text  # payload-free audit record


def test_upload_reruns_allowlist_defensively(
    control_client: TestClient, control_store: ControlPlaneStore
) -> None:
    document = {
        "tenant_id": "ten_demo",
        "workspace_id": "ws_demo",
        "environment_id": "env_local",
        "run_id": "run-x",
        "content": "leaked canary",
    }
    response = control_client.post(
        "/control/v1/uploads/receipts", json=document, headers=_bearer(DEMO_AGENT_TOKEN)
    )
    assert response.status_code == 422
    assert response.json()["detail"]["reason_code"] == "allowlist_violation"
    assert control_store.documents("receipt_ledger", _demo_scope()) == []


def _demo_scope():
    from lethe_control.models import ScopeKey

    return ScopeKey(tenant_id="ten_demo", workspace_id="ws_demo", environment_id="env_local")


def test_pull_cursor_and_enqueue_are_idempotent(control_client: TestClient) -> None:
    first = _enqueue_delete(control_client)
    second = _enqueue_delete(control_client)
    assert first["queue_seq"] == second["queue_seq"]

    pull_one = control_client.get(
        "/control/v1/agents/self/events", headers=_bearer(DEMO_AGENT_TOKEN)
    ).json()
    pull_two = control_client.get(
        "/control/v1/agents/self/events", headers=_bearer(DEMO_AGENT_TOKEN)
    ).json()
    assert pull_one == pull_two
    assert len(pull_one["events"]) == 1

    beyond = control_client.get(
        "/control/v1/agents/self/events",
        params={"cursor": pull_one["cursor"]},
        headers=_bearer(DEMO_AGENT_TOKEN),
    ).json()
    assert beyond["events"] == []
    assert beyond["cursor"] == pull_one["cursor"]
