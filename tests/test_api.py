from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from lethe_control.app import create_app
from lethe_control.config import Settings
from lethe_control.crypto import receipt_entry_hash
from lethe_control.service import LetheService

CANARY_QUERY = "What is Project Nightjar's revocation canary phrase?"
CANARY_VALUE = "amber-lantern-731"
SOURCE_VERSION = "ver_demo_canary_001"
CONTROL_TOKEN = "lethe-control-demo"
ALICE_TOKEN = "lethe-alice-demo"
BOB_TOKEN = "lethe-bob-demo"
UNSAFE_TOKEN = "lethe-unsafe-demo"


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _delete_fixture() -> tuple[dict[str, Any], str]:
    fixture_path = Path(__file__).resolve().parents[1] / "fixtures" / "demo-events.json"
    document = json.loads(fixture_path.read_text(encoding="utf-8"))
    fixture = next(item for item in document["events"] if item["event"]["event_type"] == "delete")
    return deepcopy(fixture["event"]), str(fixture["signature"])


@pytest.fixture
def client(service: LetheService, settings: Settings) -> Iterator[TestClient]:
    app = create_app(settings=settings, service=service, start_worker=False)
    with TestClient(app) as test_client:
        yield test_client


def _all_keys(value: Any) -> set[str]:
    if isinstance(value, Mapping):
        return set(value) | {key for item in value.values() for key in _all_keys(item)}
    if isinstance(value, list):
        return {key for item in value for key in _all_keys(item)}
    return set()


def test_health_and_ready_are_public_loopback_probes(client: TestClient) -> None:
    for path in ("/healthz", "/readyz"):
        response = client.get(path)
        assert response.status_code == 200
        assert response.json() == {
            "status": "ok",
            "checks": {"sqlite": "ok", "clock": "ok", "chroma": "ok"},
        }


def test_query_identity_is_token_owned_and_gate_precedes_answer_release(
    client: TestClient,
) -> None:
    missing = client.post("/api/v1/query", json={"query": CANARY_QUERY})
    assert missing.status_code == 403
    assert missing.json()["reason_code"] == "missing_bearer_token"

    unknown = client.post(
        "/api/v1/query",
        headers=_bearer("not-a-fixture-principal"),
        json={"query": CANARY_QUERY},
    )
    assert unknown.status_code == 403
    assert unknown.json()["reason_code"] == "unknown_principal"

    caller_asserted = client.post(
        "/api/v1/query",
        headers=_bearer(ALICE_TOKEN),
        json={"query": CANARY_QUERY, "principal_ref": "principal://demo/bob"},
    )
    assert caller_asserted.status_code == 422

    for token in (ALICE_TOKEN, BOB_TOKEN):
        allowed = client.post(
            "/api/v1/query",
            headers=_bearer(token),
            json={"query": CANARY_QUERY},
        )
        assert allowed.status_code == 200
        assert allowed.json()["denied"] is False
        assert CANARY_VALUE in allowed.json()["answer"]

    unsafe_wrong_token = client.post(
        "/api/v1/demo/unsafe-query",
        headers=_bearer(ALICE_TOKEN),
        json={"query": CANARY_QUERY},
    )
    assert unsafe_wrong_token.status_code == 403
    assert unsafe_wrong_token.json()["reason_code"] == "invalid_unsafe_token"

    event, signature = _delete_fixture()
    accepted = client.post(
        "/api/v1/events",
        headers={
            **_bearer(CONTROL_TOKEN),
            "X-Lethe-Event-Signature": signature,
        },
        json=event,
    )
    assert accepted.status_code == 202

    gated = client.post(
        "/api/v1/query",
        headers=_bearer(ALICE_TOKEN),
        json={"query": CANARY_QUERY},
    )
    assert gated.status_code == 200
    assert gated.json()["denied"] is True
    assert gated.json()["answer"] is None
    assert gated.json()["supporting_version_ids"] == []

    unsafe = client.post(
        "/api/v1/demo/unsafe-query",
        headers=_bearer(UNSAFE_TOKEN),
        json={"query": CANARY_QUERY},
    )
    assert unsafe.status_code == 200
    assert unsafe.json()["denied"] is False
    assert "unsafe_demo_bypass" in unsafe.json()["reason_codes"]
    assert CANARY_VALUE in unsafe.json()["answer"]


def test_event_endpoint_requires_control_token_and_domain_signature(
    client: TestClient,
) -> None:
    event, signature = _delete_fixture()

    missing_token = client.post("/api/v1/events", json=event)
    assert missing_token.status_code == 403
    assert missing_token.json()["reason_code"] == "missing_bearer_token"

    missing_signature = client.post("/api/v1/events", headers=_bearer(CONTROL_TOKEN), json=event)
    assert missing_signature.status_code == 403
    assert missing_signature.json()["reason_code"] == "missing_event_signature"

    invalid_signature = client.post(
        "/api/v1/events",
        headers={
            **_bearer(CONTROL_TOKEN),
            "X-Lethe-Event-Signature": "not-a-valid-ed25519-signature",
        },
        json=event,
    )
    assert invalid_signature.status_code == 403
    assert invalid_signature.json()["reason_code"] == "invalid_event_signature"

    accepted = client.post(
        "/api/v1/events",
        headers={
            **_bearer(CONTROL_TOKEN),
            "X-Lethe-Event-Signature": signature,
        },
        json=event,
    )
    assert accepted.status_code == 202
    accepted_body = accepted.json()
    assert accepted_body["event_id"] == event["event_id"]
    assert accepted_body["gate_state"] == "denied"
    assert accepted_body["duplicate"] is False

    duplicate = client.post(
        "/api/v1/events",
        headers={
            **_bearer(CONTROL_TOKEN),
            "X-Lethe-Event-Signature": signature,
        },
        json=event,
    )
    assert duplicate.status_code == 202
    assert duplicate.json()["duplicate"] is True
    assert duplicate.json()["run_id"] == accepted_body["run_id"]


def test_control_plane_endpoints_cover_scan_graph_run_receipt_and_verification(
    client: TestClient, service: LetheService
) -> None:
    event, signature = _delete_fixture()
    control_headers = {
        **_bearer(CONTROL_TOKEN),
        "X-Lethe-Event-Signature": signature,
    }
    accepted = client.post("/api/v1/events", headers=control_headers, json=event)
    assert accepted.status_code == 202
    run_id = accepted.json()["run_id"]

    queued = client.get(f"/api/v1/runs/{run_id}", headers=_bearer(CONTROL_TOKEN))
    assert queued.status_code == 200
    assert queued.json()["phase"] == "queued"
    assert queued.json()["counts"]["pending"] > 0

    missing_receipt = client.get(f"/api/v1/receipts/{run_id}", headers=_bearer(CONTROL_TOKEN))
    assert missing_receipt.status_code == 404
    assert missing_receipt.json()["reason_code"] == "receipt_not_found"

    scan = client.post(
        "/api/v1/scans",
        headers=_bearer(CONTROL_TOKEN),
        json={**service.scope.model_dump(), "root_version_ids": [SOURCE_VERSION]},
    )
    assert scan.status_code == 200
    scan_body = scan.json()
    assert scan_body["denominator"] > 0
    assert len(scan_body["findings"]) >= scan_body["denominator"]
    assert {item["finding_class"] for item in scan_body["findings"]} >= {"tracked"}

    graph = client.get(f"/api/v1/graph/{SOURCE_VERSION}", headers=_bearer(CONTROL_TOKEN))
    assert graph.status_code == 200
    graph_body = graph.json()
    assert graph_body["requested_version_id"] == SOURCE_VERSION
    assert len(graph_body["nodes"]) == scan_body["denominator"]
    assert graph_body["edges"]
    assert all(node["lifecycle_state"] == "denied" for node in graph_body["nodes"])

    assert service.drain_actions(force=True) == scan_body["denominator"]

    complete = client.get(f"/api/v1/runs/{run_id}", headers=_bearer(CONTROL_TOKEN))
    assert complete.status_code == 200
    complete_body = complete.json()
    assert complete_body["phase"] == "completed"
    assert complete_body["outcome"] == "succeeded"
    assert complete_body["counts"]["succeeded"] == scan_body["denominator"]
    assert complete_body["failures"] == []

    receipt = client.get(f"/api/v1/receipts/{run_id}", headers=_bearer(CONTROL_TOKEN))
    assert receipt.status_code == 200
    receipt_body = receipt.json()
    assert receipt_body["run_id"] == run_id
    assert receipt_body["coverage_level"] == "L3"
    assert receipt_body["outcome"] == "succeeded"
    assert receipt_body["counts"]["targets_verified"] == scan_body["denominator"]

    expected_head = receipt_entry_hash(receipt_body)
    verification_request = {
        "receipts": [receipt_body],
        "expected_sequence": 1,
        "expected_head": expected_head,
        "prior_trusted_head": None,
    }
    verified = client.post(
        "/api/v1/receipts/verify",
        headers=_bearer(CONTROL_TOKEN),
        json=verification_request,
    )
    assert verified.status_code == 200
    assert verified.json() == {
        "valid": True,
        "verified_sequence": 1,
        "computed_head": expected_head,
        "reason_codes": [],
    }

    tampered = deepcopy(receipt_body)
    signature_value = tampered["signature"]
    tampered["signature"] = ("A" if signature_value[0] != "A" else "B") + signature_value[1:]
    rejected = client.post(
        "/api/v1/receipts/verify",
        headers=_bearer(CONTROL_TOKEN),
        json={**verification_request, "receipts": [tampered]},
    )
    assert rejected.status_code == 200
    assert rejected.json()["valid"] is False
    assert "invalid_receipt_signature" in rejected.json()["reason_codes"]

    dashboard_documents = [
        queued.json(),
        scan_body,
        graph_body,
        complete_body,
        receipt_body,
        verified.json(),
    ]
    forbidden_keys = {
        "content_ref",
        "local_content_fingerprint",
        "acl_ref",
        "allowed_principal_refs",
        "denied_principal_refs",
    }
    for document in dashboard_documents:
        assert forbidden_keys.isdisjoint(_all_keys(document))
        assert CANARY_VALUE not in json.dumps(document, sort_keys=True)


def test_assessments_endpoint_requires_control_token(
    client: TestClient, service: LetheService
) -> None:
    body = {**service.scope.model_dump(), "root_version_ids": [SOURCE_VERSION]}

    unauthorized = client.post("/api/v1/assessments", json=body)
    assert unauthorized.status_code == 403

    response = client.post("/api/v1/assessments", headers=_bearer(CONTROL_TOKEN), json=body)
    assert response.status_code == 200
    report = response.json()
    assert report["read_only"] is True
    assert report["coverage_level"] in {"L1", "L2"}
    assert sum(report["denominators"].values()) > 0
    assert report["scan_id"]
    assert {entry["connector_ref"] for entry in report["connector_freshness"]}
