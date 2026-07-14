from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from lethe_control.clock import MutableClock
from lethe_control.config import Settings
from lethe_control.crypto import (
    load_demo_key_fixture,
    receipt_entry_hash,
    sign_event,
)
from lethe_control.errors import AuthorizationError, ConflictError, StateUnavailableError
from lethe_control.models import (
    EventType,
    ExecutionReceipt,
    FindingClass,
    LifecycleEvent,
    PermissionChangeKind,
    QueryRequest,
    ReceiptVerifyRequest,
    RunOutcome,
    ScanRequest,
)
from lethe_control.service import DEMO_PURPOSE, LetheService

FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures"


def event_fixture(event_type: EventType) -> tuple[LifecycleEvent, str]:
    fixtures = json.loads((FIXTURE_ROOT / "demo-events.json").read_text(encoding="utf-8"))
    for fixture in fixtures["events"]:
        if fixture["event"]["event_type"] == event_type.value:
            return LifecycleEvent.model_validate(fixture["event"]), fixture["signature"]
    raise AssertionError(f"missing event fixture: {event_type}")


def signed_copy(
    event: LifecycleEvent,
    *,
    signer_role: str = "event_issuer",
    **updates: object,
) -> tuple[LifecycleEvent, str]:
    document = event.model_dump(mode="python")
    document.update(updates)
    candidate = LifecycleEvent.model_validate(document)
    key = load_demo_key_fixture(signer_role)
    return candidate, sign_event(candidate.model_dump(mode="json"), key.private_seed)


def delete_and_receipt(service: LetheService) -> tuple[LifecycleEvent, ExecutionReceipt]:
    event, signature = event_fixture(EventType.DELETE)
    accepted = service.accept_event(event, signature)
    assert service.drain_actions(force=True) > 0
    return event, service.get_receipt(accepted.run_id)


def test_source_only_deletion_leaves_derivative_disclosure_untreated(
    service: LetheService,
) -> None:
    request = QueryRequest(query="What is the Nightjar canary phrase?", purpose_ref=DEMO_PURPOSE)
    before = service.unsafe_query(request)
    assert "amber-lantern-731" in (before.answer or "").casefold()

    assert service.delete_source_only_control()

    after = service.unsafe_query(request)
    assert "amber-lantern-731" in (after.answer or "").casefold()
    gated = service.query(request, principal_ref="principal://demo/alice")
    assert not gated.denied
    assert "amber-lantern-731" in (gated.answer or "").casefold()


def test_partial_connector_failure_keeps_deny_fence_and_issues_partial_receipt(
    service: LetheService,
) -> None:
    service.max_attempts = 1
    service.vectors.fail_next_delete = True
    event, signature = event_fixture(EventType.DELETE)
    accepted = service.accept_event(event, signature)

    allowed, reasons = service.gate_version(
        scope=service.scope,
        version_id=event.target_version_id,
        principal_ref="principal://demo/alice",
        purpose_ref=DEMO_PURPOSE,
    )
    assert not allowed
    assert reasons

    expected = len(
        service.state.descendant_version_ids(
            service.scope, event.target_version_id, include_self=True
        )
    )
    assert service.drain_actions(force=True) == expected
    receipt = service.get_receipt(accepted.run_id)
    assert receipt.outcome is RunOutcome.PARTIAL
    assert receipt.counts.actions_failed == 1
    assert receipt.counts.actions_succeeded == expected - 1
    failed = [action for action in receipt.actions if action.state.value == "failed"]
    assert len(failed) == 1
    assert service.vectors.exists(
        **service.scope.model_dump(), version_id=failed[0].target_version_ref
    )

    # A connector failure never lifts the authoritative deny. A later retry is
    # idempotent and can finish the physical repair without changing that fence.
    assert service.retry_failed_actions(accepted.run_id) == 1
    assert service.drain_actions(force=True) == 1
    assert not service.vectors.exists(
        **service.scope.model_dump(), version_id=failed[0].target_version_ref
    )
    assert not service.gate_version(
        scope=service.scope,
        version_id=event.target_version_id,
        principal_ref="principal://demo/alice",
        purpose_ref=DEMO_PURPOSE,
    )[0]


def test_duplicate_is_idempotent_and_lower_sequence_is_rejected(
    service: LetheService,
) -> None:
    event, signature = event_fixture(EventType.PERMISSION_CHANGE)
    first = service.accept_event(event, signature)
    duplicate = service.accept_event(event, signature)

    assert duplicate.duplicate
    assert duplicate.run_id == first.run_id
    event_count = service.state.fetch_one(
        """
        SELECT COUNT(*) AS count FROM lifecycle_events
        WHERE tenant_id=? AND workspace_id=? AND environment_id=?
        """,
        (service.scope.tenant_id, service.scope.workspace_id, service.scope.environment_id),
    )
    assert event_count is not None and event_count["count"] == 1

    stale, stale_signature = event_fixture(EventType.DELETE)
    with pytest.raises(ConflictError, match="source sequence is stale") as caught:
        service.accept_event(stale, stale_signature)
    assert caught.value.code == "stale_source_sequence"


def test_restart_reconciles_leased_action_and_preserves_run(
    settings: Settings,
) -> None:
    clock = MutableClock(datetime(2026, 7, 14, 10, tzinfo=UTC))
    original = LetheService(settings, clock=clock)
    original.initialize()
    event, signature = event_fixture(EventType.DELETE)
    accepted = original.accept_event(event, signature)
    action = original.state.fetch_one(
        """
        SELECT action_id FROM propagation_actions
        WHERE tenant_id=? AND workspace_id=? AND environment_id=? AND run_id=?
        ORDER BY action_id LIMIT 1
        """,
        (*original.scope.model_dump().values(), accepted.run_id),
    )
    assert action is not None
    with original.state.transaction() as connection:
        for table in ("propagation_actions", "action_outbox"):
            connection.execute(
                f"""
                UPDATE {table}
                SET state='leased', lease_owner='crashed-worker',
                    lease_expires_at='2099-01-01T00:00:00Z', attempt_count=1
                WHERE tenant_id=? AND workspace_id=? AND environment_id=? AND action_id=?
                """,
                (*original.scope.model_dump().values(), action["action_id"]),
            )

    restarted = LetheService(settings, clock=clock)
    restarted.initialize(seed_if_empty=False)
    reconciled = restarted.state.fetch_one(
        """
        SELECT state, lease_owner, lease_expires_at FROM propagation_actions
        WHERE tenant_id=? AND workspace_id=? AND environment_id=? AND action_id=?
        """,
        (*restarted.scope.model_dump().values(), action["action_id"]),
    )
    assert reconciled is not None
    assert reconciled["state"] == "retryable"
    assert reconciled["lease_owner"] is None
    assert reconciled["lease_expires_at"] is None

    expected = len(
        restarted.state.descendant_version_ids(
            restarted.scope, event.target_version_id, include_self=True
        )
    )
    assert restarted.drain_actions(force=True) == expected
    assert restarted.get_receipt(accepted.run_id).outcome is RunOutcome.SUCCEEDED


def test_durable_fingerprint_tombstone_quarantines_stale_reingestion(
    service: LetheService,
) -> None:
    original = service.state.get_knowledge_object(service.scope, "ver_demo_canary_001")
    assert original is not None and original.valid_until is not None
    text = (FIXTURE_ROOT / "documents/canary_source.md").read_text(encoding="utf-8")
    event, signature = event_fixture(EventType.DELETE)
    service.accept_event(event, signature)

    with pytest.raises(ConflictError, match="durable tombstone") as caught:
        service._publish_source(
            object_id="obj_demo_canary_reingested",
            version_id="ver_demo_canary_reingested_001",
            text=text,
            acl_ref=original.acl_ref,
            policy_version=event.policy_version,
            valid_from=event.effective_at,
            valid_until=original.valid_until,
            supersedes=None,
        )
    assert caught.value.code == "resurrection_quarantined"
    assert (
        service.state.get_knowledge_object(service.scope, "ver_demo_canary_reingested_001") is None
    )


def test_permission_widening_requires_data_owner_authority(
    service: LetheService,
) -> None:
    base, _ = event_fixture(EventType.PERMISSION_CHANGE)
    assert base.permission_change is not None
    widening = base.permission_change.model_copy(update={"change": PermissionChangeKind.WIDEN})
    event, signature = signed_copy(
        base,
        event_id="evt_permission_widen_unauthorized",
        idempotency_key="demo-permission-widen-unauthorized",
        nonce="nonce-permission-widen-unauthorized",
        permission_change=widening,
    )

    with pytest.raises(AuthorizationError, match="requires data owner") as caught:
        service.accept_event(event, signature)
    assert caught.value.code == "permission_widening_unauthorized"


def test_wrong_role_issuer_expired_command_and_bad_signature_are_rejected(
    service: LetheService,
) -> None:
    base, _ = event_fixture(EventType.DELETE)
    wrong_issuer, wrong_signature = signed_copy(
        base,
        signer_role="receipt_signer",
        issuer_key_id="key_demo_01",
        event_id="evt_wrong_role_issuer",
        idempotency_key="demo-wrong-role-issuer",
        nonce="nonce-wrong-role-issuer",
    )
    with pytest.raises(AuthorizationError) as wrong_caught:
        service.accept_event(wrong_issuer, wrong_signature)
    assert wrong_caught.value.code == "issuer_not_active"

    expired, expired_signature = signed_copy(
        base,
        event_id="evt_expired_command",
        idempotency_key="demo-expired-command",
        nonce="nonce-expired-command",
        occurred_at=datetime(2026, 7, 14, 8, tzinfo=UTC),
        effective_at=datetime(2026, 7, 14, 8, tzinfo=UTC),
        command_expires_at=datetime(2026, 7, 14, 9, tzinfo=UTC),
    )
    with pytest.raises(AuthorizationError) as expired_caught:
        service.accept_event(expired, expired_signature)
    assert expired_caught.value.code == "command_expired"

    with pytest.raises(AuthorizationError) as signature_caught:
        service.accept_event(base, "A" * 86)
    assert signature_caught.value.code == "invalid_event_signature"

    assert (
        service.state.latest_source_sequence(
            service.scope, base.authority_ref, base.target_version_id
        )
        is None
    )


def test_receipt_verifier_rejects_tampering_removal_and_reordering(
    service: LetheService,
) -> None:
    _, first = delete_and_receipt(service)
    base, _ = event_fixture(EventType.DELETE)
    second_event, second_signature = signed_copy(
        base,
        event_id="evt_delete_policy_parent_001",
        idempotency_key="demo-delete-policy-parent-001",
        nonce="nonce-delete-policy-parent-001",
        target_version_id="ver_demo_policy_parent_001",
    )
    accepted = service.accept_event(second_event, second_signature)
    assert service.drain_actions(force=True) > 0
    second = service.get_receipt(accepted.run_id)
    head = receipt_entry_hash(second.model_dump(mode="json", by_alias=True))

    valid = service.verify_receipts(
        ReceiptVerifyRequest(
            receipts=[first, second],
            expected_sequence=2,
            expected_head=head,
        )
    )
    assert valid.valid
    assert valid.verified_sequence == 2

    tampered_document = second.model_dump(mode="json", by_alias=True)
    tampered_document["event_id"] = "evt_tampered"
    tampered = ExecutionReceipt.model_validate(tampered_document)
    tamper_result = service.verify_receipts(
        ReceiptVerifyRequest(
            receipts=[first, tampered],
            expected_sequence=2,
            expected_head=head,
        )
    )
    assert not tamper_result.valid
    assert "invalid_receipt_signature" in tamper_result.reason_codes

    removal_result = service.verify_receipts(
        ReceiptVerifyRequest(
            receipts=[second],
            expected_sequence=2,
            expected_head=head,
        )
    )
    assert not removal_result.valid
    assert "previous_hash_mismatch" in removal_result.reason_codes

    reorder_result = service.verify_receipts(
        ReceiptVerifyRequest(
            receipts=[second, first],
            expected_sequence=2,
            expected_head=head,
        )
    )
    assert not reorder_result.valid
    assert "unexpected_chain_sequence" in reorder_result.reason_codes


def test_expiry_is_fail_closed_and_schedules_durable_cleanup(
    service: LetheService,
) -> None:
    clock = service.clock
    assert isinstance(clock, MutableClock)
    multi_parent = "ver_demo_multi_parent_summary_001"
    assert service.gate_version(
        scope=service.scope,
        version_id=multi_parent,
        principal_ref="principal://demo/alice",
        purpose_ref=DEMO_PURPOSE,
    )[0]

    clock.advance(timedelta(days=1, hours=2, minutes=1))

    allowed, reasons = service.gate_version(
        scope=service.scope,
        version_id=multi_parent,
        principal_ref="principal://demo/alice",
        purpose_ref=DEMO_PURPOSE,
    )
    assert not allowed
    assert any("expired" in reason for reason in reasons)
    scheduled = service.process_due_expirations()
    assert len(scheduled) == 1
    assert scheduled[0].gate_state.value == "denied"
    event_row = service.state.fetch_one(
        """
        SELECT event_type, target_version_id FROM lifecycle_events
        WHERE tenant_id=? AND workspace_id=? AND environment_id=? AND run_id=?
        """,
        (*service.scope.model_dump().values(), scheduled[0].run_id),
    )
    assert event_row is not None
    assert event_row["event_type"] == EventType.EXPIRE.value
    assert event_row["target_version_id"] == "ver_demo_policy_parent_001"
    assert service.drain_actions(force=True) > 0
    assert service.get_receipt(scheduled[0].run_id).outcome is RunOutcome.SUCCEEDED


def test_clock_rollback_fails_closed(service: LetheService) -> None:
    clock = service.clock
    assert isinstance(clock, MutableClock)
    clock.set(datetime(2026, 7, 14, 9, 59, tzinfo=UTC))

    with pytest.raises(StateUnavailableError) as caught:
        service.gate_version(
            scope=service.scope,
            version_id="ver_demo_canary_001",
            principal_ref="principal://demo/alice",
            purpose_ref=DEMO_PURPOSE,
        )
    assert caught.value.code == "clock_rollback"


def test_scanner_classifies_tracked_exact_and_semantic_without_mutating_candidates(
    service: LetheService,
) -> None:
    response = service.scan(
        ScanRequest(
            **service.scope.model_dump(),
            root_version_ids=["ver_demo_canary_001"],
        )
    )
    counts = {
        finding_class: sum(finding.finding_class is finding_class for finding in response.findings)
        for finding_class in FindingClass
    }
    assert response.denominator == 15
    assert counts[FindingClass.TRACKED] == 15
    assert counts[FindingClass.EXACT_UNTRACKED] == 2
    assert counts[FindingClass.SEMANTIC_CANDIDATE] == 2

    semantic_targets = {
        finding.target_version_id
        for finding in response.findings
        if finding.finding_class is FindingClass.SEMANTIC_CANDIDATE
    }
    assert all(target and target.startswith("untracked_") for target in semantic_targets)
    # Report-only candidates remain in the physical inventory.
    assert len(service.objects.inventory(**service.scope.model_dump())) >= 8
