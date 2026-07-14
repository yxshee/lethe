from __future__ import annotations

import json
from pathlib import Path

import pytest

from lethe_control.control_plane.allowlist import (
    OutboundViolation,
    assert_outbound_safe,
    validate_outbound,
)
from lethe_control.models import EventType, LifecycleEvent
from lethe_control.service import LetheService


def _delete_receipt(service: LetheService) -> dict:
    path = Path(__file__).resolve().parents[1] / "fixtures/demo-events.json"
    for fixture in json.loads(path.read_text(encoding="utf-8"))["events"]:
        if fixture["event"]["event_type"] == EventType.DELETE.value:
            event = LifecycleEvent.model_validate(fixture["event"])
            accepted = service.accept_event(event, fixture["signature"])
            service.drain_actions(force=True)
            receipt = service.get_receipt(accepted.run_id)
            return receipt.model_dump(mode="json", by_alias=True)
    raise AssertionError("missing delete fixture")


def test_real_receipt_passes(service: LetheService) -> None:
    receipt = _delete_receipt(service)
    assert validate_outbound("receipt", receipt) == []


def test_real_run_status_passes(service: LetheService) -> None:
    receipt = _delete_receipt(service)
    status = service.run_status(receipt["run_id"]).model_dump(mode="json", by_alias=True)
    assert validate_outbound("run_status", status) == []


def test_injected_content_fields_fail(service: LetheService) -> None:
    receipt = _delete_receipt(service)
    receipt["content"] = "the canary is amber-lantern-731"
    receipt["prompt"] = "what is the canary?"
    violations = validate_outbound("receipt", receipt)
    assert any(v.startswith("denied_key:content") for v in violations)
    assert any(v.startswith("denied_key:prompt") for v in violations)


def test_nested_injection_is_found(service: LetheService) -> None:
    receipt = _delete_receipt(service)
    receipt["actions"][0]["answer"] = "leaked"
    violations = validate_outbound("receipt", receipt)
    assert any(v.startswith("denied_key:actions[0].answer") for v in violations)


def test_url_and_path_values_fail(service: LetheService) -> None:
    receipt = _delete_receipt(service)
    receipt["event_id"] = "https://internal.example.com/doc/42"
    violations = validate_outbound("receipt", receipt)
    assert any(v.startswith("url_value:event_id") for v in violations)

    receipt2 = _delete_receipt(service)
    receipt2["run_id"] = "/Users/someone/Documents/report.pdf"
    assert any(
        v.startswith("filesystem_value:run_id") for v in validate_outbound("receipt", receipt2)
    )


def test_unknown_ref_scheme_fails(service: LetheService) -> None:
    receipt = _delete_receipt(service)
    receipt["event_id"] = "local://objects/abc/def.payload"
    violations = validate_outbound("receipt", receipt)
    assert any(v.startswith("unknown_ref_scheme:event_id") for v in violations)


def test_unallowed_key_fails(service: LetheService) -> None:
    receipt = _delete_receipt(service)
    receipt["debug_notes"] = "should never travel"
    violations = validate_outbound("receipt", receipt)
    assert any(v.startswith("unallowed_key:debug_notes") for v in violations)


def test_raw_digest_outside_hash_field_fails(service: LetheService) -> None:
    receipt = _delete_receipt(service)
    receipt["event_id"] = "a" * 64
    violations = validate_outbound("receipt", receipt)
    assert any(v.startswith("raw_digest_outside_hash_field:event_id") for v in violations)


def test_unknown_document_type_is_rejected() -> None:
    assert validate_outbound("telemetry", {}) == ["unknown_document_type:telemetry"]


def test_assert_raises_with_machine_readable_codes(service: LetheService) -> None:
    receipt = _delete_receipt(service)
    receipt["content"] = "x"
    with pytest.raises(OutboundViolation) as exc:
        assert_outbound_safe("receipt", receipt)
    assert all(":" in violation for violation in exc.value.violations)
