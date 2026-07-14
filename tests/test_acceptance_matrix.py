from __future__ import annotations

import json
from pathlib import Path

import pytest

from lethe_control.models import EventType, LifecycleEvent, LifecycleState, ObjectKind
from lethe_control.service import LetheService

DERIVATIVE_KINDS = {
    ObjectKind.CHUNK,
    ObjectKind.EMBEDDING,
    ObjectKind.CACHE,
    ObjectKind.SUMMARY,
    ObjectKind.MEMORY,
}


def event_fixture(event_type: EventType) -> tuple[LifecycleEvent, str]:
    path = Path(__file__).resolve().parents[1] / "fixtures/demo-events.json"
    for fixture in json.loads(path.read_text(encoding="utf-8"))["events"]:
        if fixture["event"]["event_type"] == event_type.value:
            return LifecycleEvent.model_validate(fixture["event"]), fixture["signature"]
    raise AssertionError(f"missing event fixture: {event_type}")


@pytest.mark.parametrize("event_type", list(EventType))
def test_four_event_by_five_derivative_matrix(service: LetheService, event_type: EventType) -> None:
    event, signature = event_fixture(event_type)
    target_ids = service.state.descendant_version_ids(
        service.scope, event.target_version_id, include_self=True
    )
    before = [
        item
        for version_id in target_ids
        if (item := service.state.get_knowledge_object(service.scope, version_id)) is not None
    ]
    by_kind = {kind: [item for item in before if item.kind is kind] for kind in DERIVATIVE_KINDS}
    assert all(by_kind.values())

    accepted = service.accept_event(event, signature)
    if event_type is EventType.PERMISSION_CHANGE:
        assert service.gate_version(
            scope=service.scope,
            version_id=event.target_version_id,
            principal_ref="principal://demo/alice",
            purpose_ref="purpose://demo/qa",
        )[0]
        assert not service.gate_version(
            scope=service.scope,
            version_id=event.target_version_id,
            principal_ref="principal://demo/bob",
            purpose_ref="purpose://demo/qa",
        )[0]
    else:
        assert not service.gate_version(
            scope=service.scope,
            version_id=event.target_version_id,
            principal_ref="principal://demo/alice",
            purpose_ref="purpose://demo/qa",
        )[0]

    expected_actions = len(target_ids) + int(event_type is EventType.CORRECT)
    assert service.drain_actions(force=True) == expected_actions
    receipt = service.get_receipt(accepted.run_id)
    assert receipt.outcome.value == "succeeded"

    for kind, envelopes in by_kind.items():
        for envelope in envelopes:
            assert any(
                action.target_version_ref == envelope.version_id
                and action.derivative_kind is kind
                and action.state.value == "succeeded"
                for action in receipt.actions
            )
            assert any(
                result.target_version_ref == envelope.version_id and result.result.value == "passed"
                for result in receipt.verification_results
            )
            current = service.state.get_knowledge_object(service.scope, envelope.version_id)
            assert current is not None
            if event_type is EventType.PERMISSION_CHANGE:
                assert current.lifecycle_state is LifecycleState.ACTIVE
                assert current.policy_version == 4
                assert current.acl_ref == "acl://demo/nightjar/2"
                if kind is ObjectKind.CACHE:
                    cache_rows = service.state.fetch_all(
                        """
                        SELECT principal_ref, policy_version FROM cache_entries
                        WHERE tenant_id=? AND workspace_id=? AND environment_id=?
                          AND version_id=?
                        """,
                        (
                            service.scope.tenant_id,
                            service.scope.workspace_id,
                            service.scope.environment_id,
                            current.version_id,
                        ),
                    )
                    assert {row["principal_ref"] for row in cache_rows} == {
                        "principal://demo/alice"
                    }
                    assert {row["policy_version"] for row in cache_rows} == {4}
            else:
                assert current.lifecycle_state is LifecycleState.TOMBSTONED
                if kind is ObjectKind.EMBEDDING:
                    assert not service.vectors.exists(
                        **service.scope.model_dump(), version_id=envelope.version_id
                    )
                else:
                    assert service._payload_for_envelope(current) is None

    if event_type is EventType.CORRECT:
        replacement = service.state.get_knowledge_object(service.scope, "ver_demo_corrected_001")
        assert replacement is not None
        assert replacement.supersedes_version_id == event.target_version_id
