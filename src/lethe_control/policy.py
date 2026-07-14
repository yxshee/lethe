from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from lethe_control.models import ACLPolicy, KnowledgeEnvelope, LifecycleState, PolicyAction


@dataclass(frozen=True, slots=True)
class GateDecision:
    allowed: bool
    reason_codes: tuple[str, ...]


def evaluate_contributors(
    *,
    envelopes: Iterable[KnowledgeEnvelope],
    policies: dict[tuple[str, int], ACLPolicy],
    principal_ref: str,
    purpose_ref: str | None,
    action: PolicyAction,
    now: datetime,
    tombstoned_version_ids: set[str],
    superseded_version_ids: set[str],
    signature_validity: dict[tuple[str, int], bool] | None = None,
) -> GateDecision:
    """Apply deny-wins policy to every contributing immutable version."""

    contributors = list(envelopes)
    if not contributors:
        return GateDecision(False, ("missing_lineage",))

    reasons: set[str] = set()
    for envelope in contributors:
        if envelope.version_id in tombstoned_version_ids:
            reasons.add("tombstoned")
        if envelope.version_id in superseded_version_ids:
            reasons.add("superseded")
        if envelope.lifecycle_state is not LifecycleState.ACTIVE:
            reasons.add(f"lifecycle_{envelope.lifecycle_state.value}")
        if now < envelope.valid_from:
            reasons.add("not_yet_valid")
        if envelope.valid_until is not None and now >= envelope.valid_until:
            reasons.add("expired")

        policy_key = (envelope.acl_ref, envelope.policy_version)
        policy = policies.get(policy_key)
        if policy is None:
            reasons.add("missing_policy")
            continue
        if signature_validity is not None and not signature_validity.get(policy_key, False):
            reasons.add("invalid_policy_signature")
        if now < policy.valid_from or (
            policy.valid_until is not None and now >= policy.valid_until
        ):
            reasons.add("policy_outside_validity")
        if now >= policy.group_snapshot_expires_at:
            reasons.add("stale_policy")
        if principal_ref in policy.denied_principal_refs:
            reasons.add("principal_denied")
        if principal_ref not in policy.allowed_principal_refs:
            reasons.add("principal_not_allowed")
        if action not in policy.permitted_actions:
            reasons.add("action_not_allowed")
        if policy.permitted_purpose_refs and purpose_ref not in policy.permitted_purpose_refs:
            reasons.add("purpose_not_allowed")

    return GateDecision(not reasons, tuple(sorted(reasons)))


def earliest_expiry(envelopes: Iterable[KnowledgeEnvelope]) -> datetime | None:
    expiries = [item.valid_until for item in envelopes if item.valid_until is not None]
    return min(expiries) if expiries else None
