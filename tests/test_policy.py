from __future__ import annotations

from datetime import UTC, datetime, timedelta

from lethe_control.models import (
    ACLPolicy,
    GovernanceAnnotations,
    KnowledgeEnvelope,
    LifecycleState,
    ObjectKind,
    PolicyAction,
    Provenance,
)
from lethe_control.policy import earliest_expiry, evaluate_contributors

NOW = datetime(2026, 7, 14, tzinfo=UTC)


def envelope(version_id: str, acl_ref: str, *, until=None) -> KnowledgeEnvelope:  # type: ignore[no-untyped-def]
    return KnowledgeEnvelope(
        tenant_id="tenant",
        workspace_id="workspace",
        environment_id="local",
        object_id=f"object-{version_id}",
        version_id=version_id,
        kind=ObjectKind.SOURCE,
        parent_version_ids=[],
        root_version_ids=[version_id],
        lifecycle_state=LifecycleState.ACTIVE,
        created_at=NOW,
        valid_from=NOW,
        valid_until=until,
        policy_ref="policy://demo",
        policy_version=1,
        acl_ref=acl_ref,
        content_ref=f"local://objects/{version_id}",
        local_content_fingerprint="fingerprint-v1",
        provenance=Provenance(
            activity_type="ingest", connector_ref="connector://objects", run_id="seed"
        ),
        governance=GovernanceAnnotations(),
    )


def policy(acl_ref: str, principals: list[str]) -> ACLPolicy:
    return ACLPolicy(
        tenant_id="tenant",
        workspace_id="workspace",
        environment_id="local",
        acl_ref=acl_ref,
        policy_version=1,
        allowed_principal_refs=principals,
        permitted_purpose_refs=["purpose://qa"],
        permitted_actions=[PolicyAction.RETRIEVE, PolicyAction.GENERATE],
        valid_from=NOW,
        source_authority_ref="authority://demo",
        group_snapshot_version="groups-v1",
        group_snapshot_expires_at=NOW + timedelta(days=365),
        created_at=NOW,
        signature="signed-policy-v1",
    )


def test_multi_parent_acl_is_conjunctive() -> None:
    contributors = [envelope("one", "acl://one"), envelope("two", "acl://two")]
    policies = {
        ("acl://one", 1): policy("acl://one", ["alice", "bob"]),
        ("acl://two", 1): policy("acl://two", ["alice"]),
    }

    alice = evaluate_contributors(
        envelopes=contributors,
        policies=policies,
        principal_ref="alice",
        purpose_ref="purpose://qa",
        action=PolicyAction.RETRIEVE,
        now=NOW,
        tombstoned_version_ids=set(),
        superseded_version_ids=set(),
    )
    bob = evaluate_contributors(
        envelopes=contributors,
        policies=policies,
        principal_ref="bob",
        purpose_ref="purpose://qa",
        action=PolicyAction.RETRIEVE,
        now=NOW,
        tombstoned_version_ids=set(),
        superseded_version_ids=set(),
    )

    assert alice.allowed is True
    assert bob.allowed is False
    assert "principal_not_allowed" in bob.reason_codes


def test_earliest_parent_expiry_wins() -> None:
    early = NOW + timedelta(hours=1)
    late = NOW + timedelta(hours=2)
    assert (
        earliest_expiry(
            [envelope("one", "acl://one", until=late), envelope("two", "acl://two", until=early)]
        )
        == early
    )
