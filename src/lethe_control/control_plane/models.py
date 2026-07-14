"""Control-plane contract models (metadata-only, alpha).

Deferred to v1 and documented here so the boundary is explicit: mTLS and
certificate-bound enrollment, key rotation/revocation, WORM/externally
retained checkpoints, per-tenant envelope encryption, database-enforced
row-level security (alpha enforces scoping in the query layer under test),
billing, and rate limits.
"""

from __future__ import annotations

from pydantic import Field

from lethe_control.models import (
    ContractModel,
    LifecycleEvent,
    OpaqueId,
    ScopedContract,
    UtcDateTime,
)


class AgentEnrollment(ScopedContract):
    agent_id: OpaqueId
    role: OpaqueId
    token_digest: OpaqueId
    created_at: UtcDateTime


class QueuedControlEvent(ContractModel):
    queue_seq: int = Field(ge=1)
    event: LifecycleEvent
    signature: OpaqueId
    enqueued_at: UtcDateTime


class EventPullResponse(ContractModel):
    events: list[QueuedControlEvent] = Field(default_factory=list)
    cursor: int = Field(ge=0)


class UploadAccepted(ContractModel):
    run_id: OpaqueId
    stored: bool


class RejectionRecord(ContractModel):
    reason_code: OpaqueId
    document_type: OpaqueId
    recorded_at: UtcDateTime
