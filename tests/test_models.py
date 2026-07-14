from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from lethe_control.models import (
    ACLPolicy,
    ActionState,
    ConnectorCapabilityVersion,
    CorrectionPayload,
    EventType,
    EvidenceLevel,
    ExecutionReceipt,
    GovernanceAnnotations,
    KnowledgeEnvelope,
    LifecycleEvent,
    LifecycleState,
    MutationApproval,
    ObjectKind,
    PermissionChangeKind,
    PermissionChangePayload,
    PolicyAction,
    Provenance,
    ReceiptCounts,
    ReceiptScope,
    RunOutcome,
    ScopeKey,
)

NOW = datetime(2026, 7, 14, 10, tzinfo=UTC)
SCOPE = {
    "tenant_id": "ten_demo",
    "workspace_id": "ws_demo",
    "environment_id": "env_local",
}


def source_envelope(**updates: object) -> KnowledgeEnvelope:
    values: dict[str, object] = {
        **SCOPE,
        "object_id": "obj_source",
        "version_id": "ver_source",
        "kind": ObjectKind.SOURCE,
        "parent_version_ids": [],
        "root_version_ids": ["ver_source"],
        "lifecycle_state": LifecycleState.ACTIVE,
        "created_at": NOW,
        "valid_from": NOW,
        "policy_ref": "policy://demo/default",
        "policy_version": 1,
        "acl_ref": "acl://demo/default/1",
        "content_ref": "local://objects/ver_source",
        "local_content_fingerprint": "fingerprint-v1",
        "provenance": Provenance(
            activity_type="ingest",
            connector_ref="connector://local-files",
            run_id="run_ingest",
        ),
        "governance": GovernanceAnnotations(),
    }
    values.update(updates)
    return KnowledgeEnvelope.model_validate(values)


def lifecycle_event(**updates: object) -> LifecycleEvent:
    values: dict[str, object] = {
        **SCOPE,
        "event_id": "evt_delete",
        "idempotency_key": "delete-1",
        "issuer_key_id": "issuer_demo",
        "audience": "agent_demo",
        "authority_ref": "authority://demo/source-admin",
        "nonce": "nonce-delete-1",
        "target_version_id": "ver_source",
        "event_type": EventType.DELETE,
        "source_sequence": 1,
        "policy_version": 1,
        "occurred_at": NOW,
        "effective_at": NOW,
        "command_expires_at": NOW + timedelta(minutes=5),
        "actor_ref": "actor://demo/admin",
        "reason_code": "source_deleted",
    }
    values.update(updates)
    return LifecycleEvent.model_validate(values)


def receipt(**updates: object) -> ExecutionReceipt:
    receipt_scope = ReceiptScope(
        scope_manifest_hash="manifest-hash",
        scope_selected_by="authority://demo/data-owner",
        requested_evidence_level=EvidenceLevel.L3,
        scan_cutoff=NOW,
        registered_stores=5,
        registered_connectors=5,
        reachable_connectors=5,
        connector_capability_versions=[
            ConnectorCapabilityVersion(
                connector_ref="connector://local",
                capability_version="1",
                supports_inventory=True,
                supports_mutation=True,
                supports_read_back=True,
            )
        ],
        unsupported_connectors=[],
        graph_snapshot_hash="graph-hash",
    )
    values: dict[str, object] = {
        **SCOPE,
        "agent_id": "agent_demo",
        "key_id": "receipt_key",
        "chain_sequence": 1,
        "previous_receipt_hash": None,
        "event_id": "evt_delete",
        "run_id": "run_delete",
        "source_version_ids": ["ver_source"],
        "policy_version": 1,
        "scope": receipt_scope,
        "coverage_level": EvidenceLevel.L3,
        "counts": ReceiptCounts(
            expected_tracked=6,
            found_tracked=6,
            scanner_candidates=0,
            actions_attempted=6,
            actions_succeeded=6,
            actions_failed=0,
            targets_verified=6,
        ),
        "actions": [],
        "verification_results": [],
        "exclusions": [],
        "unsupported_sinks": [],
        "residual_risks": [],
        "outcome": RunOutcome.SUCCEEDED,
        "started_at": NOW,
        "completed_at": NOW + timedelta(seconds=1),
        "evidence_manifest_hash": "evidence-hash",
        "signature": "receipt-signature",
    }
    values.update(updates)
    return ExecutionReceipt.model_validate(values)


def test_scope_key_is_hashable_and_immutable() -> None:
    scope = ScopeKey(**SCOPE)
    assert {scope: "value"}[scope] == "value"
    with pytest.raises(ValidationError):
        scope.tenant_id = "changed"


def test_knowledge_envelope_rejects_bad_graph_and_non_utc_time() -> None:
    source = source_envelope()
    assert source.scope == ScopeKey(**SCOPE)

    with pytest.raises(ValidationError, match="include itself as a root"):
        source_envelope(root_version_ids=["other"])
    with pytest.raises(ValidationError, match="UTC"):
        source_envelope(created_at=NOW.replace(tzinfo=None))
    with pytest.raises(ValidationError, match="Extra inputs"):
        source_envelope(unapproved_field="leak")


def test_lifecycle_event_requires_type_specific_payload() -> None:
    delete = lifecycle_event()
    assert delete.event_type is EventType.DELETE

    correction = lifecycle_event(
        event_type=EventType.CORRECT,
        event_id="evt_correct",
        idempotency_key="correct-1",
        nonce="nonce-correct-1",
        correction=CorrectionPayload(
            replacement_version_id="ver_source_2",
            replacement_content_ref="local-object://registered/ver_source_2",
            replacement_fingerprint="replacement-fingerprint",
        ),
    )
    assert correction.correction is not None

    with pytest.raises(ValidationError, match="requires only correction"):
        lifecycle_event(event_type=EventType.CORRECT)
    with pytest.raises(ValidationError, match="policy versions must match"):
        lifecycle_event(
            event_type=EventType.PERMISSION_CHANGE,
            permission_change=PermissionChangePayload(
                change=PermissionChangeKind.NARROW,
                new_acl_ref="acl://demo/default/2",
                new_policy_version=2,
            ),
        )


def test_acl_policy_rejects_conflicting_principals() -> None:
    with pytest.raises(ValidationError, match="both allowed and denied"):
        ACLPolicy(
            **SCOPE,
            acl_ref="acl://demo/default/1",
            policy_version=1,
            allowed_principal_refs=["principal://alice"],
            denied_principal_refs=["principal://alice"],
            permitted_actions=[PolicyAction.RETRIEVE],
            valid_from=NOW,
            source_authority_ref="authority://demo/source-admin",
            group_snapshot_version="groups-v1",
            group_snapshot_expires_at=NOW + timedelta(hours=1),
            created_at=NOW,
            signature="policy-signature",
        )


def test_mutation_approval_requires_bounded_unique_targets() -> None:
    with pytest.raises(ValidationError, match="target_version_refs"):
        MutationApproval(
            **SCOPE,
            approval_id="approval_1",
            action_plan_hash="action-plan-hash",
            connector_refs=["connector://local"],
            target_version_refs=["ver_source", "ver_source"],
            maximum_action_count=2,
            requester_ref="actor://demo/operator",
            approver_ref="actor://demo/data-owner",
            approver_authority="mutation_approve",
            policy_snapshot_hash="policy-snapshot-hash",
            legal_hold_snapshot_hash="legal-hold-hash",
            nonce="approval-nonce",
            expires_at=NOW + timedelta(hours=1),
            signature="approval-signature",
        )


def test_succeeded_receipt_requires_complete_nonzero_coverage() -> None:
    valid = receipt()
    assert valid.scope_record.requested_evidence_level is EvidenceLevel.L3
    assert valid.model_dump(mode="json", by_alias=True)["scope"]["registered_stores"] == 5

    with pytest.raises(ValidationError, match="nonzero denominator"):
        receipt(
            counts=ReceiptCounts(
                expected_tracked=0,
                found_tracked=0,
                scanner_candidates=0,
                actions_attempted=0,
                actions_succeeded=0,
                actions_failed=0,
                targets_verified=0,
            )
        )
    with pytest.raises(ValidationError, match="previous_receipt_hash"):
        receipt(chain_sequence=2)


def test_closed_child_models_reject_unknown_fields() -> None:
    with pytest.raises(ValidationError, match="Extra inputs"):
        ReceiptCounts(
            expected_tracked=1,
            found_tracked=1,
            scanner_candidates=0,
            actions_attempted=1,
            actions_succeeded=1,
            actions_failed=0,
            targets_verified=1,
            raw_path="/private/document.txt",
        )

    assert ActionState("retryable") is ActionState.RETRYABLE
