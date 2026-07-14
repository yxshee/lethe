from __future__ import annotations

from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)


def _require_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("timestamp must be timezone-aware UTC")
    return value.astimezone(UTC)


UtcDateTime = Annotated[datetime, AfterValidator(_require_utc)]
OpaqueId = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=512)]
OpaqueRef = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2048)]
Digest = Annotated[str, StringConstraints(strip_whitespace=True, min_length=8, max_length=1024)]


class ContractModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        validate_assignment=True,
        str_strip_whitespace=True,
        populate_by_name=True,
    )


class ObjectKind(StrEnum):
    SOURCE = "source"
    CHUNK = "chunk"
    EMBEDDING = "embedding"
    CACHE = "cache"
    SUMMARY = "summary"
    MEMORY = "memory"


class LifecycleState(StrEnum):
    ACTIVE = "active"
    DENIED = "denied"
    TOMBSTONED = "tombstoned"


class EventType(StrEnum):
    DELETE = "delete"
    CORRECT = "correct"
    EXPIRE = "expire"
    PERMISSION_CHANGE = "permission_change"


class PermissionChangeKind(StrEnum):
    NARROW = "narrow"
    WIDEN = "widen"


class RunOutcome(StrEnum):
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"
    UNKNOWN = "unknown"


class ActionState(StrEnum):
    PENDING = "pending"
    LEASED = "leased"
    RETRYABLE = "retryable"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    DEAD_LETTER = "dead_letter"


class FindingClass(StrEnum):
    TRACKED = "tracked"
    EXACT_UNTRACKED = "exact_untracked"
    SEMANTIC_CANDIDATE = "semantic_candidate"


class EvidenceLevel(StrEnum):
    L0 = "L0"
    L1 = "L1"
    L2 = "L2"
    L3 = "L3"
    L4 = "L4"


class ActionCode(StrEnum):
    DENY = "deny"
    DELETE = "delete"
    QUARANTINE = "quarantine"
    TOMBSTONE = "tombstone"
    EVICT = "evict"
    SUPPRESS = "suppress"
    REBUILD = "rebuild"
    UPDATE_ACL = "update_acl"


class VerificationCode(StrEnum):
    PAYLOAD_ABSENT = "payload_absent"
    TOMBSTONE_PRESENT = "tombstone_present"
    GATE_DENIED = "gate_denied"
    METADATA_UPDATED = "metadata_updated"
    BRANCH_REBUILT = "branch_rebuilt"
    RESURRECTION_BLOCKED = "resurrection_blocked"
    STORE_READ_BACK = "store_read_back"


class VerificationStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    UNKNOWN = "unknown"


class ReceiptReasonCode(StrEnum):
    DECLARED_EXCLUSION = "declared_exclusion"
    CONNECTOR_UNREACHABLE = "connector_unreachable"
    CONNECTOR_UNSUPPORTED = "connector_unsupported"
    STORE_MUTATION_FAILED = "store_mutation_failed"
    READ_BACK_FAILED = "read_back_failed"
    POLICY_STALE = "policy_stale"
    LEGAL_HOLD = "legal_hold"
    OUTSIDE_SCOPE = "outside_scope"
    SCOPE_UNKNOWN = "scope_unknown"
    RESIDUAL_PAYLOAD = "residual_payload"
    DIRECT_STORE_BYPASS = "direct_store_bypass"
    UNREGISTERED_COPY = "unregistered_copy"
    HOST_TRUST = "host_trust"


class PolicyAction(StrEnum):
    RETRIEVE = "retrieve"
    GENERATE = "generate"
    CACHE = "cache"
    READ = "read"
    MUTATE = "mutate"


class ScopeKey(ContractModel):
    model_config = ConfigDict(
        extra="forbid", validate_assignment=True, str_strip_whitespace=True, frozen=True
    )

    tenant_id: OpaqueId
    workspace_id: OpaqueId
    environment_id: OpaqueId


class ScopedContract(ContractModel):
    tenant_id: OpaqueId
    workspace_id: OpaqueId
    environment_id: OpaqueId

    @property
    def scope(self) -> ScopeKey:
        return ScopeKey(
            tenant_id=self.tenant_id,
            workspace_id=self.workspace_id,
            environment_id=self.environment_id,
        )


class Provenance(ContractModel):
    activity_type: OpaqueId
    connector_ref: OpaqueRef
    run_id: OpaqueId


class GovernanceAnnotations(ContractModel):
    owner_ref: OpaqueRef | None = None
    purpose_refs: list[OpaqueRef] = Field(default_factory=list)
    jurisdictions: list[OpaqueId] = Field(default_factory=list)
    consent_ref: OpaqueRef | None = None
    license_ref: OpaqueRef | None = None
    retention_ref: OpaqueRef | None = None
    revocation_connector_ref: OpaqueRef | None = None
    residual_risk_class: OpaqueId = "standard"


class KnowledgeEnvelope(ScopedContract):
    schema_version: Literal["1"] = "1"
    object_id: OpaqueId
    version_id: OpaqueId
    kind: ObjectKind
    parent_version_ids: list[OpaqueId]
    root_version_ids: list[OpaqueId]
    lifecycle_state: LifecycleState
    created_at: UtcDateTime
    valid_from: UtcDateTime
    valid_until: UtcDateTime | None = None
    policy_ref: OpaqueRef
    policy_version: Annotated[int, Field(ge=1)]
    acl_ref: OpaqueRef
    supersedes_version_id: OpaqueId | None = None
    content_ref: OpaqueRef | None = None
    local_content_fingerprint: Digest | None = None
    provenance: Provenance
    governance: GovernanceAnnotations = Field(default_factory=GovernanceAnnotations)

    @model_validator(mode="after")
    def validate_graph_and_validity(self) -> KnowledgeEnvelope:
        if len(set(self.parent_version_ids)) != len(self.parent_version_ids):
            raise ValueError("parent_version_ids must be unique")
        if len(set(self.root_version_ids)) != len(self.root_version_ids):
            raise ValueError("root_version_ids must be unique")
        if not self.root_version_ids:
            raise ValueError("root_version_ids must not be empty")
        if self.version_id in self.parent_version_ids:
            raise ValueError("version cannot be its own parent")
        if self.kind is ObjectKind.SOURCE and self.parent_version_ids:
            raise ValueError("source envelope cannot have parent versions")
        if self.kind is ObjectKind.SOURCE and self.version_id not in self.root_version_ids:
            raise ValueError("source envelope must include itself as a root")
        if self.valid_until is not None and self.valid_until <= self.valid_from:
            raise ValueError("valid_until must be later than valid_from")
        if self.supersedes_version_id == self.version_id:
            raise ValueError("version cannot supersede itself")
        return self


class CorrectionPayload(ContractModel):
    replacement_version_id: OpaqueId
    replacement_content_ref: OpaqueRef
    replacement_fingerprint: Digest


class PermissionChangePayload(ContractModel):
    change: PermissionChangeKind
    new_acl_ref: OpaqueRef
    new_policy_version: Annotated[int, Field(ge=1)]


class LifecycleEvent(ScopedContract):
    schema_version: Literal["1"] = "1"
    event_id: OpaqueId
    idempotency_key: OpaqueId
    issuer_key_id: OpaqueId
    audience: OpaqueId
    authority_ref: OpaqueRef
    nonce: OpaqueId
    target_version_id: OpaqueId
    event_type: EventType
    source_sequence: Annotated[int, Field(ge=1)]
    policy_version: Annotated[int, Field(ge=1)]
    occurred_at: UtcDateTime
    effective_at: UtcDateTime
    command_expires_at: UtcDateTime
    actor_ref: OpaqueRef
    reason_code: OpaqueId
    correction: CorrectionPayload | None = None
    permission_change: PermissionChangePayload | None = None

    @model_validator(mode="after")
    def validate_event_payload(self) -> LifecycleEvent:
        if self.command_expires_at <= self.occurred_at:
            raise ValueError("command_expires_at must be later than occurred_at")
        if self.event_type is EventType.CORRECT:
            if self.correction is None or self.permission_change is not None:
                raise ValueError("correct event requires only correction payload")
            if self.correction.replacement_version_id == self.target_version_id:
                raise ValueError("replacement version must differ from target")
        elif self.event_type is EventType.PERMISSION_CHANGE:
            if self.permission_change is None or self.correction is not None:
                raise ValueError("permission_change event requires only permission_change payload")
            if self.permission_change.new_policy_version != self.policy_version:
                raise ValueError("permission payload and event policy versions must match")
        elif self.correction is not None or self.permission_change is not None:
            raise ValueError("event payload does not match event_type")
        return self


class ConnectorCapabilityVersion(ContractModel):
    connector_ref: OpaqueRef
    capability_version: OpaqueId
    supports_inventory: bool
    supports_mutation: bool
    supports_read_back: bool


class ManifestExclusion(ContractModel):
    target_ref: OpaqueRef | None = None
    connector_ref: OpaqueRef | None = None
    derivative_kind: ObjectKind | None = None
    reason_code: ReceiptReasonCode
    count: Annotated[int, Field(ge=0)] = 0


class ScopeManifest(ScopedContract):
    schema_version: Literal["1"] = "1"
    manifest_id: OpaqueId
    manifest_hash: Digest
    selected_by: OpaqueRef
    requested_evidence_level: EvidenceLevel
    scan_cutoff: UtcDateTime
    registered_store_refs: list[OpaqueRef]
    registered_connector_refs: list[OpaqueRef]
    connector_capability_versions: list[ConnectorCapabilityVersion]
    derivative_classes: list[ObjectKind]
    freshness_cursors: dict[str, OpaqueId] = Field(default_factory=dict)
    denominators: dict[ObjectKind, Annotated[int, Field(ge=0)]]
    exclusions: list[ManifestExclusion] = Field(default_factory=list)
    created_at: UtcDateTime

    @model_validator(mode="after")
    def validate_manifest(self) -> ScopeManifest:
        if len(set(self.registered_store_refs)) != len(self.registered_store_refs):
            raise ValueError("registered_store_refs must be unique")
        if len(set(self.registered_connector_refs)) != len(self.registered_connector_refs):
            raise ValueError("registered_connector_refs must be unique")
        if len(set(self.derivative_classes)) != len(self.derivative_classes):
            raise ValueError("derivative_classes must be unique")
        return self


class ACLPolicy(ScopedContract):
    schema_version: Literal["1"] = "1"
    acl_ref: OpaqueRef
    policy_version: Annotated[int, Field(ge=1)]
    allowed_principal_refs: list[OpaqueRef] = Field(default_factory=list)
    denied_principal_refs: list[OpaqueRef] = Field(default_factory=list)
    allowed_group_refs: list[OpaqueRef] = Field(default_factory=list)
    denied_group_refs: list[OpaqueRef] = Field(default_factory=list)
    permitted_purpose_refs: list[OpaqueRef] = Field(default_factory=list)
    permitted_actions: list[PolicyAction] = Field(default_factory=list)
    valid_from: UtcDateTime
    valid_until: UtcDateTime | None = None
    source_authority_ref: OpaqueRef
    group_snapshot_version: OpaqueId
    group_snapshot_expires_at: UtcDateTime
    created_at: UtcDateTime
    signature: Digest

    @model_validator(mode="after")
    def validate_acl(self) -> ACLPolicy:
        if set(self.allowed_principal_refs) & set(self.denied_principal_refs):
            raise ValueError("principal cannot be both allowed and denied")
        if set(self.allowed_group_refs) & set(self.denied_group_refs):
            raise ValueError("group cannot be both allowed and denied")
        if self.valid_until is not None and self.valid_until <= self.valid_from:
            raise ValueError("valid_until must be later than valid_from")
        if self.group_snapshot_expires_at <= self.created_at:
            raise ValueError("group snapshot must expire after policy creation")
        return self


class MutationApproval(ScopedContract):
    schema_version: Literal["1"] = "1"
    approval_id: OpaqueId
    action_plan_hash: Digest
    connector_refs: list[OpaqueRef]
    target_version_refs: list[OpaqueId]
    maximum_action_count: Annotated[int, Field(ge=1)]
    requester_ref: OpaqueRef
    approver_ref: OpaqueRef
    approver_authority: Literal["mutation_approve"]
    policy_snapshot_hash: Digest
    legal_hold_snapshot_hash: Digest
    nonce: OpaqueId
    expires_at: UtcDateTime
    signature: Digest

    @model_validator(mode="after")
    def validate_approval(self) -> MutationApproval:
        if not self.connector_refs or len(set(self.connector_refs)) != len(self.connector_refs):
            raise ValueError("connector_refs must be nonempty and unique")
        if not self.target_version_refs or len(set(self.target_version_refs)) != len(
            self.target_version_refs
        ):
            raise ValueError("target_version_refs must be nonempty and unique")
        return self


class ReceiptScope(ContractModel):
    scope_manifest_hash: Digest
    scope_selected_by: OpaqueRef
    requested_evidence_level: EvidenceLevel
    scan_cutoff: UtcDateTime
    registered_stores: Annotated[int, Field(ge=0)]
    registered_connectors: Annotated[int, Field(ge=0)]
    reachable_connectors: Annotated[int, Field(ge=0)]
    connector_capability_versions: list[ConnectorCapabilityVersion]
    unsupported_connectors: list[OpaqueRef]
    graph_snapshot_hash: Digest

    @model_validator(mode="after")
    def validate_connector_counts(self) -> ReceiptScope:
        if self.reachable_connectors > self.registered_connectors:
            raise ValueError("reachable_connectors cannot exceed registered_connectors")
        return self


class ReceiptCounts(ContractModel):
    expected_tracked: Annotated[int, Field(ge=0)]
    found_tracked: Annotated[int, Field(ge=0)]
    scanner_candidates: Annotated[int, Field(ge=0)]
    actions_attempted: Annotated[int, Field(ge=0)]
    actions_succeeded: Annotated[int, Field(ge=0)]
    actions_failed: Annotated[int, Field(ge=0)]
    targets_verified: Annotated[int, Field(ge=0)]

    @model_validator(mode="after")
    def validate_counts(self) -> ReceiptCounts:
        if self.found_tracked > self.expected_tracked:
            raise ValueError("found_tracked cannot exceed expected_tracked")
        if self.actions_succeeded + self.actions_failed > self.actions_attempted:
            raise ValueError("terminal action counts cannot exceed attempted actions")
        return self


class ReceiptAction(ContractModel):
    action_id: OpaqueId
    target_version_ref: OpaqueId
    derivative_kind: ObjectKind
    connector_ref: OpaqueRef
    action_code: ActionCode
    state: ActionState
    attempt_count: Annotated[int, Field(ge=0)]
    started_at: UtcDateTime | None = None
    completed_at: UtcDateTime | None = None
    reason_code: ReceiptReasonCode | None = None


class ReceiptVerificationResult(ContractModel):
    verification_id: OpaqueId
    target_version_ref: OpaqueId
    connector_ref: OpaqueRef
    verification_code: VerificationCode
    result: VerificationStatus
    checked_at: UtcDateTime
    reason_code: ReceiptReasonCode | None = None


class ReceiptExclusion(ContractModel):
    target_ref: OpaqueRef | None = None
    connector_ref: OpaqueRef | None = None
    derivative_kind: ObjectKind | None = None
    reason_code: ReceiptReasonCode
    count: Annotated[int, Field(ge=0)] = 0


class UnsupportedSink(ContractModel):
    connector_ref: OpaqueRef
    derivative_kind: ObjectKind | None = None
    capability_version: OpaqueId | None = None
    reason_code: ReceiptReasonCode = ReceiptReasonCode.CONNECTOR_UNSUPPORTED


class ResidualRisk(ContractModel):
    risk_code: ReceiptReasonCode
    target_ref: OpaqueRef | None = None
    connector_ref: OpaqueRef | None = None
    count: Annotated[int, Field(ge=0)] = 0


class ExecutionReceipt(ScopedContract):
    receipt_version: Literal["1"] = "1"
    agent_id: OpaqueId
    key_id: OpaqueId
    chain_sequence: Annotated[int, Field(ge=1)]
    previous_receipt_hash: Digest | None
    event_id: OpaqueId
    run_id: OpaqueId
    source_version_ids: list[OpaqueId]
    policy_version: Annotated[int, Field(ge=1)]
    scope_record: ReceiptScope = Field(alias="scope", serialization_alias="scope")
    coverage_level: EvidenceLevel
    counts: ReceiptCounts
    actions: list[ReceiptAction]
    verification_results: list[ReceiptVerificationResult]
    exclusions: list[ReceiptExclusion]
    unsupported_sinks: list[UnsupportedSink]
    residual_risks: list[ResidualRisk]
    outcome: RunOutcome
    started_at: UtcDateTime
    completed_at: UtcDateTime
    evidence_manifest_hash: Digest
    signature_algorithm: Literal["Ed25519"] = "Ed25519"
    signature: Digest

    @model_validator(mode="after")
    def validate_receipt(self) -> ExecutionReceipt:
        if not self.source_version_ids:
            raise ValueError("source_version_ids must not be empty")
        if self.completed_at < self.started_at:
            raise ValueError("completed_at cannot precede started_at")
        if self.chain_sequence == 1 and self.previous_receipt_hash is not None:
            raise ValueError("genesis receipt cannot have previous_receipt_hash")
        if self.chain_sequence > 1 and self.previous_receipt_hash is None:
            raise ValueError("non-genesis receipt requires previous_receipt_hash")
        if self.outcome is RunOutcome.SUCCEEDED:
            if self.counts.expected_tracked == 0:
                raise ValueError("succeeded receipt requires a nonzero denominator")
            if self.scope_record.reachable_connectors != self.scope_record.registered_connectors:
                raise ValueError("succeeded receipt requires every connector reachable")
            if self.scope_record.unsupported_connectors or self.unsupported_sinks:
                raise ValueError("succeeded receipt cannot contain unsupported connectors")
            if self.counts.found_tracked != self.counts.expected_tracked:
                raise ValueError("succeeded receipt requires complete tracked coverage")
            if self.counts.actions_failed != 0:
                raise ValueError("succeeded receipt cannot contain failed actions")
            if self.counts.actions_succeeded != self.counts.actions_attempted:
                raise ValueError("succeeded receipt requires every attempted action to succeed")
            if self.counts.targets_verified < self.counts.expected_tracked:
                raise ValueError("succeeded receipt requires every tracked target verified")
        return self


class EventAccepted(ScopedContract):
    event_id: OpaqueId
    run_id: OpaqueId
    duplicate: bool = False
    gate_state: LifecycleState


class RunStatusResponse(ScopedContract):
    run_id: OpaqueId
    event_id: OpaqueId
    phase: OpaqueId
    counts: dict[str, Annotated[int, Field(ge=0)]] = Field(default_factory=dict)
    failures: list[ReceiptAction] = Field(default_factory=list)
    exclusions: list[ReceiptExclusion] = Field(default_factory=list)
    outcome: RunOutcome | None = None
    started_at: UtcDateTime
    completed_at: UtcDateTime | None = None


class GraphNode(ContractModel):
    version_id: OpaqueId
    object_id: OpaqueId
    kind: ObjectKind
    lifecycle_state: LifecycleState


class GraphEdge(ContractModel):
    parent_version_id: OpaqueId
    child_version_id: OpaqueId


class GraphResponse(ScopedContract):
    requested_version_id: OpaqueId
    root_version_ids: list[OpaqueId]
    nodes: list[GraphNode]
    edges: list[GraphEdge]


class QueryRequest(ContractModel):
    query: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=10_000)]
    purpose_ref: OpaqueRef | None = None


class QueryResponse(ContractModel):
    answer: str | None
    supporting_version_ids: list[OpaqueId] = Field(default_factory=list)
    denied: bool
    reason_codes: list[OpaqueId] = Field(default_factory=list)


class ScanRequest(ScopedContract):
    root_version_ids: list[OpaqueId] = Field(default_factory=list)


class ScanFinding(ContractModel):
    finding_id: OpaqueId
    finding_class: FindingClass
    derivative_kind: ObjectKind
    connector_ref: OpaqueRef
    target_version_id: OpaqueId | None = None
    confidence: Annotated[float, Field(ge=0, le=1)] | None = None


class ScanResponse(ScopedContract):
    scan_id: OpaqueId
    findings: list[ScanFinding]
    denominator: Annotated[int, Field(ge=0)]
    completed_at: UtcDateTime


class ReceiptVerifyRequest(ContractModel):
    receipts: list[ExecutionReceipt]
    expected_sequence: Annotated[int, Field(ge=1)]
    expected_head: Digest
    prior_trusted_head: Digest | None = None


class ReceiptVerifyResponse(ContractModel):
    valid: bool
    verified_sequence: Annotated[int, Field(ge=0)]
    computed_head: Digest | None = None
    reason_codes: list[OpaqueId] = Field(default_factory=list)


class HealthResponse(ContractModel):
    status: Literal["ok", "degraded", "failed"]
    checks: dict[str, Literal["ok", "degraded", "failed"]] = Field(default_factory=dict)


JsonObject = dict[str, Any]
