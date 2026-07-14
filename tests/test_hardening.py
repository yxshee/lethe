from __future__ import annotations

import json
import time
from datetime import timedelta
from pathlib import Path

import pytest

from lethe_control.config import Settings
from lethe_control.crypto import (
    load_demo_key_fixture,
    receipt_entry_hash,
    sign_event,
    sign_receipt,
)
from lethe_control.errors import AuthorizationError, ConflictError
from lethe_control.models import (
    EventType,
    ExecutionReceipt,
    FindingClass,
    LifecycleEvent,
    ObjectKind,
    PermissionChangeKind,
    QueryRequest,
    ReceiptReasonCode,
    ReceiptVerifyRequest,
    RunOutcome,
    ScanRequest,
)
from lethe_control.service import DEMO_PURPOSE, LetheService, _parse_time

FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures"


def event_fixture(event_type: EventType) -> tuple[LifecycleEvent, str]:
    fixtures = json.loads((FIXTURE_ROOT / "demo-events.json").read_text(encoding="utf-8"))
    for fixture in fixtures["events"]:
        if fixture["event"]["event_type"] == event_type.value:
            return LifecycleEvent.model_validate(fixture["event"]), fixture["signature"]
    raise AssertionError(f"missing event fixture: {event_type}")


def signed_copy(event: LifecycleEvent, **updates: object) -> tuple[LifecycleEvent, str]:
    candidate = LifecycleEvent.model_validate(event.model_dump(mode="python") | updates)
    key = load_demo_key_fixture("event_issuer")
    return candidate, sign_event(candidate.model_dump(mode="json"), key.private_seed)


def test_delete_quarantines_exact_untracked_and_reports_semantic_risk(
    service: LetheService,
) -> None:
    labels = json.loads(
        (service.settings.state_dir / "seeded-inventory.json").read_text(encoding="utf-8")
    )["records"]
    exact_refs = {item["content_ref"] for item in labels if item["label"] == "exact_positive"}
    semantic_refs = {item["content_ref"] for item in labels if item["label"] == "semantic_positive"}

    event, signature = event_fixture(EventType.DELETE)
    accepted = service.accept_event(event, signature)
    scan = service.scan(
        ScanRequest(**service.scope.model_dump(), root_version_ids=[event.target_version_id])
    )
    assert (
        sum(finding.finding_class is FindingClass.EXACT_UNTRACKED for finding in scan.findings) == 2
    )
    service.drain_actions(force=True)
    receipt = service.get_receipt(accepted.run_id)

    assert receipt.outcome is RunOutcome.SUCCEEDED
    assert all(not service.objects.exists(ref) for ref in exact_refs)
    assert all(service.objects.exists(ref) for ref in semantic_refs)
    assert any(
        risk.risk_code is ReceiptReasonCode.UNREGISTERED_COPY and risk.count == 2
        for risk in receipt.residual_risks
    )


def test_rejections_are_reason_coded_audited_and_nonce_replay_is_blocked(
    service: LetheService,
) -> None:
    base, signature = event_fixture(EventType.PERMISSION_CHANGE)
    accepted = service.accept_event(base, signature)

    replay, replay_signature = signed_copy(
        base,
        event_id="evt_nonce_replay",
        idempotency_key="idem_nonce_replay",
    )
    with pytest.raises(ConflictError) as replayed:
        service.accept_event(replay, replay_signature)
    assert replayed.value.code == "replayed_nonce"

    unknown, unknown_signature = signed_copy(
        base,
        event_id="evt_unknown_issuer",
        idempotency_key="idem_unknown_issuer",
        nonce="nonce_unknown_issuer",
        issuer_key_id="unknown_key",
    )
    with pytest.raises(AuthorizationError) as unknown_error:
        service.accept_event(unknown, unknown_signature)
    assert unknown_error.value.code == "unknown_issuer"

    rows = service.state.fetch_all(
        """
        SELECT rejection_reason_code FROM rejected_event_audit
        WHERE tenant_id=? AND workspace_id=? AND environment_id=?
        """,
        (
            service.scope.tenant_id,
            service.scope.workspace_id,
            service.scope.environment_id,
        ),
    )
    assert {row["rejection_reason_code"] for row in rows} >= {
        "replayed_nonce",
        "unknown_issuer",
    }
    assert service.run_status(accepted.run_id).phase == "queued"


def test_tampered_mutation_approval_blocks_physical_actions(
    service: LetheService,
) -> None:
    service.max_attempts = 1
    event, signature = event_fixture(EventType.DELETE)
    accepted = service.accept_event(event, signature)
    source = service.state.get_knowledge_object(service.scope, event.target_version_id)
    assert source is not None and source.content_ref is not None

    with service.state.transaction() as connection:
        connection.execute(
            """
            UPDATE mutation_approvals SET signature='tampered'
            WHERE tenant_id=? AND workspace_id=? AND environment_id=?
            """,
            (
                service.scope.tenant_id,
                service.scope.workspace_id,
                service.scope.environment_id,
            ),
        )
    service.drain_actions(force=True)
    receipt = service.get_receipt(accepted.run_id)

    assert receipt.outcome is RunOutcome.FAILED
    assert receipt.counts.actions_succeeded == 0
    assert service.objects.exists(source.content_ref)
    assert not service.gate_version(
        scope=service.scope,
        version_id=event.target_version_id,
        principal_ref="principal://demo/alice",
        purpose_ref=DEMO_PURPOSE,
    )[0]


def test_authorized_widening_stays_old_policy_until_all_actions_verify(
    service: LetheService,
) -> None:
    narrow, narrow_signature = event_fixture(EventType.PERMISSION_CHANGE)
    service.accept_event(narrow, narrow_signature)
    service.drain_actions(force=True)
    assert not service.gate_version(
        scope=service.scope,
        version_id=narrow.target_version_id,
        principal_ref="principal://demo/bob",
        purpose_ref=DEMO_PURPOSE,
    )[0]

    assert narrow.permission_change is not None
    widening_payload = narrow.permission_change.model_copy(
        update={
            "change": PermissionChangeKind.WIDEN,
            "new_acl_ref": "acl://demo/nightjar/5",
            "new_policy_version": 5,
        }
    )
    widening, widening_signature = signed_copy(
        narrow,
        event_id="evt_permission_widen_authorized",
        idempotency_key="idem_permission_widen_authorized",
        nonce="nonce_permission_widen_authorized",
        authority_ref="authority://demo/data-owner",
        actor_ref="actor://demo/data-owner",
        source_sequence=5,
        policy_version=5,
        permission_change=widening_payload,
    )
    accepted = service.accept_event(widening, widening_signature)

    assert not service.gate_version(
        scope=service.scope,
        version_id=widening.target_version_id,
        principal_ref="principal://demo/bob",
        purpose_ref=DEMO_PURPOSE,
    )[0]
    assert service.process_next_action(force=True)
    assert not service.gate_version(
        scope=service.scope,
        version_id=widening.target_version_id,
        principal_ref="principal://demo/bob",
        purpose_ref=DEMO_PURPOSE,
    )[0]

    service.drain_actions(force=True)
    assert service.get_receipt(accepted.run_id).outcome is RunOutcome.SUCCEEDED
    assert service.gate_version(
        scope=service.scope,
        version_id=widening.target_version_id,
        principal_ref="principal://demo/bob",
        purpose_ref=DEMO_PURPOSE,
    )[0]


def test_correction_rebuild_action_recovers_source_only_partial_branch(
    settings: Settings,
) -> None:
    service = LetheService(settings)
    service.initialize()
    event, signature = event_fixture(EventType.CORRECT)
    accepted = service.accept_event(event, signature)
    assert event.correction is not None
    entry = next(
        item
        for item in service.manifest["corpus"]
        if item.get("version_id") == event.correction.replacement_version_id
    )
    text = (FIXTURE_ROOT / str(entry["path"])).read_text(encoding="utf-8")
    policy = service._acl_policy(
        acl_ref=str(entry["acl_ref"]),
        policy_version=event.policy_version,
        allowed_principals=list(entry["allowed_principals"]),
        valid_until=_parse_time(str(entry["valid_until"])),
    )
    service._store_acl(policy)
    service._publish_source(
        object_id=str(entry["object_id"]),
        version_id=str(entry["version_id"]),
        text=text,
        acl_ref=str(entry["acl_ref"]),
        policy_version=event.policy_version,
        valid_from=event.effective_at,
        valid_until=_parse_time(str(entry["valid_until"])),
        supersedes=event.target_version_id,
    )

    restarted = LetheService(settings, clock=service.clock)
    restarted.initialize(seed_if_empty=False)
    restarted.drain_actions(force=True)
    receipt = restarted.get_receipt(accepted.run_id)
    descendants = restarted.state.descendant_version_ids(
        restarted.scope, event.correction.replacement_version_id, include_self=False
    )
    kinds = {
        envelope.kind
        for version_id in descendants
        if (envelope := restarted.state.get_knowledge_object(restarted.scope, version_id))
        is not None
    }

    assert receipt.outcome is RunOutcome.SUCCEEDED
    assert {
        ObjectKind.CHUNK,
        ObjectKind.EMBEDDING,
        ObjectKind.CACHE,
        ObjectKind.SUMMARY,
        ObjectKind.MEMORY,
    } <= kinds
    mixed = [
        restarted.state.get_knowledge_object(restarted.scope, version_id)
        for version_id in descendants
    ]
    assert any(
        item is not None
        and len(item.parent_version_ids) == 2
        and "ver_demo_policy_parent_001" in item.parent_version_ids
        for item in mixed
    )
    response = restarted.query(
        QueryRequest(query="What is the current Nightjar canary phrase?", purpose_ref=DEMO_PURPOSE),
        principal_ref="principal://demo/alice",
    )
    assert "blue-orchid-842" in (response.answer or "").casefold()


def test_receipt_verifier_rejects_wrong_role_signing_key(service: LetheService) -> None:
    event, signature = event_fixture(EventType.DELETE)
    accepted = service.accept_event(event, signature)
    service.drain_actions(force=True)
    receipt = service.get_receipt(accepted.run_id)

    document = receipt.model_dump(mode="json", by_alias=True)
    document["key_id"] = "issuer_demo_01"
    document["signature"] = "pending00"
    document["signature"] = sign_receipt(
        document, load_demo_key_fixture("event_issuer").private_seed
    )
    forged = ExecutionReceipt.model_validate(document)
    forged_head = receipt_entry_hash(forged.model_dump(mode="json", by_alias=True))
    result = service.verify_receipts(
        ReceiptVerifyRequest(
            receipts=[forged],
            expected_sequence=1,
            expected_head=forged_head,
        )
    )
    assert not result.valid
    assert "untrusted_enrollment" in result.reason_codes


def test_restart_repairs_run_status_from_atomically_persisted_receipt(
    settings: Settings,
) -> None:
    service = LetheService(settings)
    service.initialize()
    event, signature = event_fixture(EventType.DELETE)
    accepted = service.accept_event(event, signature)
    service.drain_actions(force=True)
    with service.state.transaction() as connection:
        connection.execute(
            """
            UPDATE propagation_runs
            SET phase='queued', outcome=NULL, completed_at=NULL
            WHERE tenant_id=? AND workspace_id=? AND environment_id=? AND run_id=?
            """,
            (
                service.scope.tenant_id,
                service.scope.workspace_id,
                service.scope.environment_id,
                accepted.run_id,
            ),
        )

    restarted = LetheService(settings, clock=service.clock)
    restarted.initialize(seed_if_empty=False)
    status = restarted.run_status(accepted.run_id)
    assert status.phase == "completed"
    assert status.outcome is RunOutcome.SUCCEEDED


def test_restart_reconciles_crash_after_external_mutation_before_completion(
    settings: Settings,
) -> None:
    service = LetheService(settings)
    service.initialize()
    event, signature = event_fixture(EventType.DELETE)
    accepted = service.accept_event(event, signature)
    action = service.state.fetch_one(
        """
        SELECT action_id, target_version_id FROM propagation_actions
        WHERE tenant_id=? AND workspace_id=? AND environment_id=? AND run_id=?
          AND target_kind='chunk' LIMIT 1
        """,
        (
            service.scope.tenant_id,
            service.scope.workspace_id,
            service.scope.environment_id,
            accepted.run_id,
        ),
    )
    assert action is not None
    envelope = service.state.get_knowledge_object(service.scope, str(action["target_version_id"]))
    assert envelope is not None and envelope.content_ref is not None
    assert service.objects.delete(envelope.content_ref)
    with service.state.transaction() as connection:
        for table in ("propagation_actions", "action_outbox"):
            connection.execute(
                f"""
                UPDATE {table} SET state='leased', attempt_count=1,
                    lease_owner='crashed-worker', lease_expires_at='2099-01-01T00:00:00Z'
                WHERE tenant_id=? AND workspace_id=? AND environment_id=? AND action_id=?
                """,
                (
                    service.scope.tenant_id,
                    service.scope.workspace_id,
                    service.scope.environment_id,
                    action["action_id"],
                ),
            )

    restarted = LetheService(settings, clock=service.clock)
    restarted.initialize(seed_if_empty=False)
    restarted.drain_actions(force=True)
    receipt = restarted.get_receipt(accepted.run_id)
    recovered = next(item for item in receipt.actions if item.action_id == action["action_id"])
    assert receipt.outcome is RunOutcome.SUCCEEDED
    assert recovered.attempt_count == 2
    assert not restarted.objects.exists(envelope.content_ref)


def test_worker_schedules_expiry_without_external_caller(service: LetheService) -> None:
    parent_until = _parse_time(service.manifest["clock"]["policy_parent_valid_until"])
    clock = service.clock
    clock.advance(parent_until - clock.now() + timedelta(seconds=1))  # type: ignore[attr-defined]
    service.start_worker(poll_interval=0.02)
    try:
        deadline = time.monotonic() + 4
        row = None
        while time.monotonic() < deadline:
            row = service.state.fetch_one(
                """
                SELECT run_id FROM lifecycle_events
                WHERE tenant_id=? AND workspace_id=? AND environment_id=?
                  AND target_version_id='ver_demo_policy_parent_001'
                  AND event_type='expire'
                """,
                (
                    service.scope.tenant_id,
                    service.scope.workspace_id,
                    service.scope.environment_id,
                ),
            )
            if row is not None:
                break
            time.sleep(0.02)
        assert row is not None
    finally:
        service.stop_worker()


def test_scanner_denominator_exposes_missing_physical_record(service: LetheService) -> None:
    embedding = next(
        envelope
        for version_id in service.state.descendant_version_ids(
            service.scope, "ver_demo_canary_001", include_self=False
        )
        if (envelope := service.state.get_knowledge_object(service.scope, version_id)) is not None
        and envelope.kind is ObjectKind.EMBEDDING
    )
    service.vectors.delete(**service.scope.model_dump(), version_id=embedding.version_id)
    scan = service.scan(
        ScanRequest(**service.scope.model_dump(), root_version_ids=["ver_demo_canary_001"])
    )
    tracked_ids = {
        finding.target_version_id
        for finding in scan.findings
        if finding.finding_class is FindingClass.TRACKED
    }
    assert scan.denominator == 15
    assert embedding.version_id not in tracked_ids
    assert len(tracked_ids) == 14


def test_gate_fails_closed_for_missing_lineage_and_stale_policy(
    service: LetheService,
) -> None:
    allowed, reasons = service.gate_version(
        scope=service.scope,
        version_id="ver_missing",
        principal_ref="principal://demo/alice",
        purpose_ref=DEMO_PURPOSE,
    )
    assert not allowed and "missing_lineage" in reasons

    with service.state.transaction() as connection:
        connection.execute(
            """
            UPDATE knowledge_objects SET policy_version=999
            WHERE tenant_id=? AND workspace_id=? AND environment_id=?
              AND version_id='ver_demo_canary_001'
            """,
            (
                service.scope.tenant_id,
                service.scope.workspace_id,
                service.scope.environment_id,
            ),
        )
    allowed, reasons = service.gate_version(
        scope=service.scope,
        version_id="ver_demo_canary_001",
        principal_ref="principal://demo/alice",
        purpose_ref=DEMO_PURPOSE,
    )
    assert not allowed
    assert "missing_policy" in reasons or "stale_policy" in reasons
