"""Read-only posture assessment.

Builds the Revocation Posture Assessment deliverable: declared denominators,
connector freshness, stale-copy findings, lineage gaps, unsupported scope,
and incident summary. Read-only means no mutation of registered stores and no
destructive connector permissions; the scan's ``scanner_findings`` rows are
local bookkeeping in the agent's own state database. Evidence is capped at
L1/L2 by construction — no connector read-back and no gate-denial tests run
as actions, so the report never implies enforcement.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from lethe_control.deterministic import stable_id
from lethe_control.models import (
    AssessmentFinding,
    AssessmentGapCode,
    AssessmentIncident,
    AssessmentSeverity,
    ConnectorFreshness,
    EvidenceLevel,
    FindingClass,
    KnowledgeEnvelope,
    ObjectKind,
    PostureAssessmentReport,
    ReceiptReasonCode,
    RunOutcome,
    ScanFinding,
    ScanRequest,
)

if TYPE_CHECKING:
    from lethe_control.service import LetheService

_DEMO_PRINCIPALS = ("principal://demo/alice", "principal://demo/bob")
_VECTOR_POLICY_KEYS = ("acl_ref", "policy_version", "policy_binding")


def _utc_text(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _denied_for_every_principal(service: LetheService, version_id: str) -> bool:
    for principal_ref in _DEMO_PRINCIPALS:
        allowed, _reasons = service.gate_version(
            scope=service.scope,
            version_id=version_id,
            principal_ref=principal_ref,
            purpose_ref=None,
        )
        if allowed:
            return False
    return True


def _classify_scan_finding(
    service: LetheService, scan_finding: ScanFinding
) -> AssessmentFinding | None:
    if scan_finding.finding_class is FindingClass.TRACKED:
        if scan_finding.target_version_id is None:
            return None
        if not _denied_for_every_principal(service, scan_finding.target_version_id):
            return None
        return AssessmentFinding(
            finding_id=stable_id("assessfinding", scan_finding.finding_id, "critical"),
            severity=AssessmentSeverity.CRITICAL,
            finding_class=scan_finding.finding_class,
            derivative_kind=scan_finding.derivative_kind,
            connector_ref=scan_finding.connector_ref,
            target_version_id=scan_finding.target_version_id,
            evidence_level=EvidenceLevel.L2,
            confidence=scan_finding.confidence,
            reason_code=ReceiptReasonCode.RESIDUAL_PAYLOAD,
        )
    if scan_finding.finding_class is FindingClass.EXACT_UNTRACKED:
        return AssessmentFinding(
            finding_id=stable_id("assessfinding", scan_finding.finding_id, "high"),
            severity=AssessmentSeverity.HIGH,
            finding_class=scan_finding.finding_class,
            derivative_kind=scan_finding.derivative_kind,
            connector_ref=scan_finding.connector_ref,
            target_version_id=scan_finding.target_version_id,
            evidence_level=EvidenceLevel.L2,
            confidence=scan_finding.confidence,
            reason_code=ReceiptReasonCode.UNREGISTERED_COPY,
        )
    return AssessmentFinding(
        finding_id=stable_id("assessfinding", scan_finding.finding_id, "informational"),
        severity=AssessmentSeverity.INFORMATIONAL,
        finding_class=scan_finding.finding_class,
        derivative_kind=scan_finding.derivative_kind,
        connector_ref=scan_finding.connector_ref,
        target_version_id=scan_finding.target_version_id,
        evidence_level=EvidenceLevel.L1,
        confidence=scan_finding.confidence,
    )


def _lineage_gap_findings(
    service: LetheService, scan_id: str, tracked: list[KnowledgeEnvelope]
) -> list[AssessmentFinding]:
    findings: list[AssessmentFinding] = []
    for envelope in tracked:
        for parent_version_id in envelope.parent_version_ids:
            if service.state.get_knowledge_object(service.scope, parent_version_id) is not None:
                continue
            findings.append(
                AssessmentFinding(
                    finding_id=stable_id(
                        "assessfinding", scan_id, envelope.version_id, "missing-parent"
                    ),
                    severity=AssessmentSeverity.MEDIUM,
                    finding_class=FindingClass.TRACKED,
                    derivative_kind=envelope.kind,
                    connector_ref=service.connectors[envelope.kind],
                    target_version_id=envelope.version_id,
                    evidence_level=EvidenceLevel.L2,
                    gap_code=AssessmentGapCode.MISSING_PARENT_EDGE,
                )
            )

    tracked_embeddings = {
        envelope.version_id for envelope in tracked if envelope.kind is ObjectKind.EMBEDDING
    }
    for version_id, metadata in service.vectors.inventory(**service.scope.model_dump()):
        if version_id not in tracked_embeddings:
            continue
        if all(metadata.get(key) not in (None, "") for key in _VECTOR_POLICY_KEYS):
            continue
        findings.append(
            AssessmentFinding(
                finding_id=stable_id(
                    "assessfinding", scan_id, version_id, "missing-policy-metadata"
                ),
                severity=AssessmentSeverity.MEDIUM,
                finding_class=FindingClass.TRACKED,
                derivative_kind=ObjectKind.EMBEDDING,
                connector_ref=service.connectors[ObjectKind.EMBEDDING],
                target_version_id=version_id,
                evidence_level=EvidenceLevel.L2,
                gap_code=AssessmentGapCode.MISSING_POLICY_METADATA,
            )
        )
    return findings


def _connector_freshness(
    service: LetheService, freshness_cursors: dict[str, str], now: datetime
) -> list[ConnectorFreshness]:
    kinds_by_connector: dict[str, list[ObjectKind]] = {}
    for kind, connector_ref in service.connectors.items():
        kinds_by_connector.setdefault(connector_ref, []).append(kind)
    entries: list[ConnectorFreshness] = []
    for connector_ref in sorted(kinds_by_connector):
        store_ref = "store://" + connector_ref.removeprefix("connector://")
        if store_ref not in service.registered_stores:
            store_ref = ""
        entries.append(
            ConnectorFreshness(
                connector_ref=connector_ref,
                store_ref=store_ref or None,
                capability_version="1",
                freshness_cursor=freshness_cursors.get(store_ref) or _utc_text(now),
                reachable=True,
                kinds=sorted(kinds_by_connector[connector_ref]),
            )
        )
    return entries


def run_posture_assessment(service: LetheService, request: ScanRequest) -> PostureAssessmentReport:
    started_at = service.clock.now()
    scan = service.scan(request)

    roots = request.root_version_ids or ["ver_demo_canary_001"]
    tracked_ids: set[str] = set()
    for root in roots:
        tracked_ids.update(
            service.state.descendant_version_ids(service.scope, root, include_self=True)
        )
    tracked = [
        envelope
        for version_id in sorted(tracked_ids)
        if (envelope := service.state.get_knowledge_object(service.scope, version_id)) is not None
    ]

    manifest = service._manifest_for_targets(
        seed=scan.scan_id,
        scope=service.scope,
        targets=tracked,
        now=started_at,
        requested_evidence_level=EvidenceLevel.L2,
    )

    findings = [
        assessed
        for scan_finding in scan.findings
        if (assessed := _classify_scan_finding(service, scan_finding)) is not None
    ]
    findings.extend(_lineage_gap_findings(service, scan.scan_id, tracked))

    completed_at = service.clock.now()
    incidents = [
        AssessmentIncident(
            incident_id=stable_id("incident", scan.scan_id, finding.finding_id),
            severity=finding.severity,
            finding_ids=[finding.finding_id],
            reason_code=finding.reason_code or ReceiptReasonCode.RESIDUAL_PAYLOAD,
            detected_at=completed_at,
        )
        for finding in findings
        if finding.severity is AssessmentSeverity.CRITICAL
    ]

    severity_counts = {severity: 0 for severity in AssessmentSeverity}
    for finding in findings:
        severity_counts[finding.severity] += 1

    declared_total = sum(manifest.denominators.values())
    if declared_total == 0:
        coverage_level = EvidenceLevel.L1
        outcome = RunOutcome.UNKNOWN
    else:
        coverage_level = EvidenceLevel.L2
        outcome = RunOutcome.SUCCEEDED

    return PostureAssessmentReport(
        **service.scope.model_dump(),
        assessment_id=stable_id("assessment", scan.scan_id),
        scan_id=scan.scan_id,
        coverage_level=coverage_level,
        scope_manifest_hash=manifest.manifest_hash,
        denominators=manifest.denominators,
        connector_freshness=_connector_freshness(
            service, dict(manifest.freshness_cursors), started_at
        ),
        findings=findings,
        lineage_gaps=[finding for finding in findings if finding.gap_code is not None],
        incidents=incidents,
        severity_counts=severity_counts,
        outcome=outcome,
        started_at=started_at,
        completed_at=completed_at,
    )
