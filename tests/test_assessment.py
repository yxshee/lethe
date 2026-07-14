from __future__ import annotations

import json
from pathlib import Path

import pytest

from lethe_control.models import (
    AssessmentFinding,
    AssessmentGapCode,
    AssessmentSeverity,
    EventType,
    EvidenceLevel,
    FindingClass,
    LifecycleEvent,
    ObjectKind,
    ReceiptReasonCode,
    RunOutcome,
    ScanRequest,
)
from lethe_control.service import LetheService


def event_fixture(event_type: EventType) -> tuple[LifecycleEvent, str]:
    path = Path(__file__).resolve().parents[1] / "fixtures/demo-events.json"
    for fixture in json.loads(path.read_text(encoding="utf-8"))["events"]:
        if fixture["event"]["event_type"] == event_type.value:
            return LifecycleEvent.model_validate(fixture["event"]), fixture["signature"]
    raise AssertionError(f"missing event fixture: {event_type}")


def scan_request(service: LetheService) -> ScanRequest:
    return ScanRequest(**service.scope.model_dump())


def tracked_envelopes(service: LetheService, root: str = "ver_demo_canary_001") -> list:
    version_ids = service.state.descendant_version_ids(service.scope, root, include_self=True)
    return [
        envelope
        for version_id in sorted(version_ids)
        if (envelope := service.state.get_knowledge_object(service.scope, version_id)) is not None
    ]


def test_ghost_state_yields_critical_findings_and_full_deliverable(
    service: LetheService,
) -> None:
    event, signature = event_fixture(EventType.DELETE)
    service.accept_event(event, signature)  # deny fence installed, repairs NOT drained

    report = service.assess(scan_request(service))

    assert report.read_only is True
    assert report.coverage_level is EvidenceLevel.L2
    assert sum(report.denominators.values()) > 0
    assert report.outcome is RunOutcome.SUCCEEDED

    critical = [
        finding for finding in report.findings if finding.severity is AssessmentSeverity.CRITICAL
    ]
    assert critical, "payload present + gate denied must produce critical findings"
    assert all(finding.reason_code is ReceiptReasonCode.RESIDUAL_PAYLOAD for finding in critical)
    assert all(finding.evidence_level is EvidenceLevel.L2 for finding in critical)

    assert report.incidents
    critical_ids = {finding.finding_id for finding in critical}
    for incident in report.incidents:
        assert set(incident.finding_ids) <= critical_ids

    connector_refs = {entry.connector_ref for entry in report.connector_freshness}
    assert connector_refs == set(service.connectors.values())
    assert all(entry.reachable for entry in report.connector_freshness)

    assert report.severity_counts[AssessmentSeverity.CRITICAL] == len(critical)


def test_healthy_world_has_no_critical_findings(service: LetheService) -> None:
    report = service.assess(scan_request(service))

    assert not [
        finding for finding in report.findings if finding.severity is AssessmentSeverity.CRITICAL
    ]
    assert not report.incidents
    assert report.outcome is RunOutcome.SUCCEEDED


def test_exact_stale_copy_is_high_severity(service: LetheService) -> None:
    summary = next(
        envelope for envelope in tracked_envelopes(service) if envelope.kind is ObjectKind.SUMMARY
    )
    payload = service._payload_for_envelope(summary)
    assert payload is not None
    service.objects.put(
        **service.scope.model_dump(),
        kind="untracked",
        storage_id="planted-stale-copy",
        payload=payload.encode("utf-8"),
    )

    report = service.assess(scan_request(service))

    high = [finding for finding in report.findings if finding.severity is AssessmentSeverity.HIGH]
    assert any(
        finding.finding_class is FindingClass.EXACT_UNTRACKED
        and finding.target_version_id == summary.version_id
        and finding.reason_code is ReceiptReasonCode.UNREGISTERED_COPY
        and finding.evidence_level is EvidenceLevel.L2
        for finding in high
    )


def test_missing_policy_metadata_is_medium_gap(service: LetheService) -> None:
    embedding = next(
        envelope for envelope in tracked_envelopes(service) if envelope.kind is ObjectKind.EMBEDDING
    )
    service.vectors.delete(**service.scope.model_dump(), version_id=embedding.version_id)
    service.vectors.upsert(
        **service.scope.model_dump(),
        version_id=embedding.version_id,
        embedding=[1.0] + [0.0] * 63,
        metadata={"lifecycle_state": "active"},
    )

    report = service.assess(scan_request(service))

    gap = [
        finding
        for finding in report.findings
        if finding.gap_code is AssessmentGapCode.MISSING_POLICY_METADATA
        and finding.target_version_id == embedding.version_id
    ]
    assert gap
    assert gap[0].severity is AssessmentSeverity.MEDIUM
    assert gap[0] in report.lineage_gaps


def test_semantic_candidates_are_informational_and_l1(service: LetheService) -> None:
    service.objects.put(
        **service.scope.model_dump(),
        kind="untracked",
        storage_id="planted-paraphrase",
        payload=b"the amber lantern glows near marker 731 in the paraphrased note",
    )

    report = service.assess(scan_request(service))

    semantic = [
        finding
        for finding in report.findings
        if finding.finding_class is FindingClass.SEMANTIC_CANDIDATE
    ]
    assert semantic
    assert all(
        finding.severity is AssessmentSeverity.INFORMATIONAL
        and finding.evidence_level is EvidenceLevel.L1
        for finding in semantic
    )


def test_finding_model_rejects_evidence_above_l2() -> None:
    with pytest.raises(ValueError, match="limited to L1 or L2"):
        AssessmentFinding(
            finding_id="finding-1",
            severity=AssessmentSeverity.HIGH,
            finding_class=FindingClass.TRACKED,
            derivative_kind=ObjectKind.EMBEDDING,
            connector_ref="connector://chroma",
            evidence_level=EvidenceLevel.L3,
        )


def test_zero_denominator_cannot_succeed(service: LetheService) -> None:
    report = service.assess(
        ScanRequest(**service.scope.model_dump(), root_version_ids=["ver_absent_000"])
    )

    assert sum(report.denominators.values()) == 0
    assert report.outcome is not RunOutcome.SUCCEEDED
    assert report.coverage_level is EvidenceLevel.L1


def test_assessment_is_read_only(service: LetheService) -> None:
    event, signature = event_fixture(EventType.DELETE)
    service.accept_event(event, signature)

    def snapshot() -> tuple:
        objects = tuple(
            ref for ref, _payload in service.objects.inventory(**service.scope.model_dump())
        )
        vectors = tuple(
            sorted(
                version_id
                for version_id, _m in service.vectors.inventory(**service.scope.model_dump())
            )
        )
        lifecycles = tuple(
            (envelope.version_id, envelope.lifecycle_state)
            for envelope in tracked_envelopes(service)
        )
        table_counts = tuple(
            service.state.fetch_all(
                f"SELECT COUNT(*) AS n FROM {table} "
                "WHERE tenant_id=? AND workspace_id=? AND environment_id=?",
                (
                    service.scope.tenant_id,
                    service.scope.workspace_id,
                    service.scope.environment_id,
                ),
            )[0]["n"]
            for table in ("cache_entries", "summaries", "memories", "action_outbox")
        )
        return objects, vectors, lifecycles, table_counts

    before = snapshot()
    service.assess(scan_request(service))
    assert snapshot() == before
