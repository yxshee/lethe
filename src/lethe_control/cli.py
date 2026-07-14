from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any

import uvicorn

from lethe_control.app import create_app
from lethe_control.clock import MutableClock
from lethe_control.config import Settings
from lethe_control.deterministic import fixture_manifest_hash, stable_id
from lethe_control.models import (
    EventType,
    ExecutionReceipt,
    LifecycleEvent,
    ObjectKind,
    QueryRequest,
    ReceiptVerifyRequest,
    ScanRequest,
)
from lethe_control.service import DEMO_PURPOSE, LetheService, _json, _parse_time


def _fixture_events() -> list[dict[str, Any]]:
    path = Path(__file__).resolve().parents[2] / "fixtures/demo-events.json"
    return list(json.loads(path.read_text(encoding="utf-8"))["events"])


def _event_fixture(event_type: str) -> dict[str, Any]:
    for fixture in _fixture_events():
        if fixture["event"]["event_type"] == event_type:
            return fixture
    raise ValueError(f"unknown fixture event: {event_type}")


def _deterministic_service(settings: Settings | None = None) -> LetheService:
    configured = settings or Settings.from_env()
    initial = _parse_time("2026-07-14T10:00:00Z")
    return LetheService(configured, clock=MutableClock(initial))


def _write_report(settings: Settings, name: str, report: dict[str, Any]) -> Path:
    settings.reports_dir.mkdir(parents=True, exist_ok=True)
    path = settings.reports_dir / name
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def run_demo(settings: Settings) -> int:
    service = _deterministic_service(settings)
    service.initialize(seed_if_empty=False)
    service.reset_demo()
    query = QueryRequest(
        query="What is Project Nightjar's revocation canary phrase?",
        purpose_ref=DEMO_PURPOSE,
    )
    pre_event = service.query(query, principal_ref="principal://demo/alice")
    source_removed = service.delete_source_only_control()
    unsafe = service.unsafe_query(query)

    fixture = _event_fixture(EventType.DELETE.value)
    event = LifecycleEvent.model_validate(fixture["event"])
    accepted = service.accept_event(event, fixture["signature"])
    immediate = service.query(query, principal_ref="principal://demo/alice")
    scan = service.scan(
        ScanRequest(**service.scope.model_dump(), root_version_ids=[event.target_version_id])
    )
    processed = service.drain_actions(force=True)
    status = service.run_status(accepted.run_id)
    receipt = service.get_receipt(accepted.run_id)
    trusted_head = service.state.receipt_head(service.scope, receipt.agent_id)
    if trusted_head is None:
        raise RuntimeError("local trusted checkpoint missing")
    expected_head = str(trusted_head["entry_hash"])
    expected_sequence = int(trusted_head["chain_sequence"])
    prior_row = service.state.fetch_one(
        """
        SELECT entry_hash FROM receipt_chain
        WHERE tenant_id=? AND environment_id=? AND agent_id=?
          AND chain_sequence=?
        """,
        (
            service.scope.tenant_id,
            service.scope.environment_id,
            receipt.agent_id,
            expected_sequence - 1,
        ),
    )
    prior_trusted_head = str(prior_row["entry_hash"]) if prior_row is not None else None
    verification = service.verify_receipts(
        ReceiptVerifyRequest(
            receipts=[receipt],
            expected_sequence=expected_sequence,
            expected_head=expected_head,
            prior_trusted_head=prior_trusted_head,
        )
    )
    report = {
        "demo": "lethe-v0.1-eight-stage",
        "scope_boundary": "closed-world demo-owned stores",
        "universal_deletion_claim": False,
        "stages": {
            "1_ingest": {"seeded": True, "target": event.target_version_id},
            "2_derive_and_answer": pre_event.model_dump(mode="json"),
            "3_unsafe_baseline": {
                "source_payload_removed": source_removed,
                "response": unsafe.model_dump(mode="json"),
            },
            "4_register_and_fence": {
                "accepted": accepted.model_dump(mode="json"),
                "gated_response": immediate.model_dump(mode="json"),
            },
            "5_scan": scan.model_dump(mode="json"),
            "6_propagate": {"actions_processed": processed, "run": status.model_dump(mode="json")},
            "7_resurrection_probes": {
                "attacks": service.run_resurrection_attacks(event),
                "fixed_query_suites": service.run_probes(event),
            },
            "8_receipt": {
                "receipt": receipt.model_dump(mode="json", by_alias=True),
                "entry_hash": expected_head,
                "local_trusted_checkpoint": {
                    "expected_sequence": expected_sequence,
                    "expected_head": expected_head,
                    "prior_trusted_head": prior_trusted_head,
                    "evidence_level": "local checkpoint; not L4",
                },
                "verification": verification.model_dump(mode="json"),
            },
        },
    }
    report_path = _write_report(settings, "demo-report.json", report)
    print(json.dumps({"report": str(report_path), "outcome": receipt.outcome.value}, indent=2))
    return 0 if verification.valid and receipt.outcome.value == "succeeded" else 1


def run_event(settings: Settings, event_type: str) -> int:
    service = _deterministic_service(settings)
    service.initialize(seed_if_empty=False)
    service.reset_demo()
    fixture = _event_fixture(event_type)
    event = LifecycleEvent.model_validate(fixture["event"])
    accepted = service.accept_event(event, fixture["signature"])
    scan = service.scan(
        ScanRequest(**service.scope.model_dump(), root_version_ids=[event.target_version_id])
    )
    service.drain_actions(force=True)
    receipt = service.get_receipt(accepted.run_id)
    result: dict[str, Any] = {
        "event_type": event_type,
        "run": service.run_status(accepted.run_id).model_dump(mode="json"),
        "scan_findings": len(scan.findings),
        "receipt": receipt.model_dump(mode="json", by_alias=True),
    }
    if event_type == EventType.CORRECT.value:
        result["replacement_query"] = service.query(
            QueryRequest(
                query="What is the current Nightjar canary phrase?",
                purpose_ref=DEMO_PURPOSE,
            ),
            principal_ref="principal://demo/alice",
        ).model_dump(mode="json")
    if event_type == EventType.PERMISSION_CHANGE.value:
        query = QueryRequest(query="What is the Nightjar canary phrase?", purpose_ref=DEMO_PURPOSE)
        result["alice"] = service.query(query, principal_ref="principal://demo/alice").model_dump(
            mode="json"
        )
        result["bob"] = service.query(query, principal_ref="principal://demo/bob").model_dump(
            mode="json"
        )
    path = _write_report(settings, f"{event_type}-report.json", result)
    print(json.dumps({"report": str(path), "outcome": receipt.outcome.value}, indent=2))
    return 0 if receipt.outcome.value == "succeeded" else 1


def run_baseline(settings: Settings) -> int:
    service = _deterministic_service(settings)
    service.initialize()
    query = QueryRequest(
        query="What is Project Nightjar's revocation canary phrase?",
        purpose_ref=DEMO_PURPOSE,
    )
    source_removed = service.delete_source_only_control()
    unsafe = service.unsafe_query(query)
    result = {
        "source_payload_removed": source_removed,
        "unsafe_derivative_disclosure": "amber-lantern-731" in (unsafe.answer or "").casefold(),
        "response": unsafe.model_dump(mode="json"),
    }
    print(json.dumps(result, indent=2))
    return 0 if source_removed and result["unsafe_derivative_disclosure"] else 1


def accept_event_only(settings: Settings, event_type: str) -> int:
    service = _deterministic_service(settings)
    service.initialize()
    fixture = _event_fixture(event_type)
    accepted = service.accept_event(
        LifecycleEvent.model_validate(fixture["event"]), fixture["signature"]
    )
    print(accepted.model_dump_json(indent=2))
    return 0


def run_scan_stage(settings: Settings, root_version_id: str) -> int:
    service = _deterministic_service(settings)
    service.initialize()
    scan = service.scan(
        ScanRequest(**service.scope.model_dump(), root_version_ids=[root_version_id])
    )
    print(scan.model_dump_json(indent=2))
    return 0


def run_assessment_stage(settings: Settings, root_version_id: str, output: Path | None) -> int:
    service = _deterministic_service(settings)
    service.initialize()
    report = service.assess(
        ScanRequest(**service.scope.model_dump(), root_version_ids=[root_version_id])
    )
    rendered = report.model_dump_json(indent=2)
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if report.outcome.value != "failed" else 1


def run_propagation_stage(settings: Settings, run_id: str | None) -> int:
    service = _deterministic_service(settings)
    service.initialize(seed_if_empty=False)
    processed = service.drain_actions(force=True)
    result: dict[str, Any] = {"actions_processed": processed}
    if run_id:
        result["run"] = service.run_status(run_id).model_dump(mode="json")
        result["receipt"] = service.get_receipt(run_id).model_dump(mode="json", by_alias=True)
    print(json.dumps(result, indent=2))
    return 0


def run_probe_stage(settings: Settings, event_type: str) -> int:
    service = _deterministic_service(settings)
    service.initialize(seed_if_empty=False)
    event = LifecycleEvent.model_validate(_event_fixture(event_type)["event"])
    attacks = service.run_resurrection_attacks(event)
    result = {
        "attacks": attacks,
        "fixed_query_suites": service.run_probes(event),
    }
    print(json.dumps(result, indent=2))
    return 0 if all(attacks.values()) else 1


def export_receipt(settings: Settings, run_id: str, output: Path) -> int:
    service = _deterministic_service(settings)
    service.initialize(seed_if_empty=False)
    receipt = service.get_receipt(run_id)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(receipt.model_dump_json(by_alias=True, indent=2) + "\n", encoding="utf-8")
    print(output)
    return 0


def _add_synthetic_cache_derivatives(service: LetheService, count: int) -> None:
    source = service.state.get_knowledge_object(service.scope, "ver_demo_canary_001")
    if source is None:
        raise RuntimeError("benchmark source missing")
    now = source.created_at
    created_at = now.isoformat().replace("+00:00", "Z")
    expires_at = (
        source.valid_until.isoformat().replace("+00:00", "Z")
        if source.valid_until is not None
        else None
    )
    with service.state.transaction() as connection:
        for ordinal in range(count):
            version_id = stable_id("ver", source.version_id, "benchmark-cache", ordinal)
            payload = {"ordinal": ordinal, "root": source.version_id}
            envelope = service._envelope(
                object_id=stable_id("obj", source.object_id, "benchmark-cache", ordinal),
                version_id=version_id,
                kind=ObjectKind.CACHE,
                parents=[source.version_id],
                roots=[source.version_id],
                acl_ref=source.acl_ref,
                policy_version=source.policy_version,
                valid_from=now,
                valid_until=source.valid_until,
                content_ref=f"sqlite://cache/{version_id}",
                payload=payload,
                activity="benchmark_cache",
            )
            service.state.insert_knowledge_object(connection, envelope)
            connection.execute(
                """
                INSERT INTO cache_entries (
                    tenant_id, workspace_id, environment_id, cache_key, version_id,
                    principal_ref, policy_version, payload, created_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    service.scope.tenant_id,
                    service.scope.workspace_id,
                    service.scope.environment_id,
                    stable_id("cache", version_id),
                    version_id,
                    "principal://demo/alice",
                    source.policy_version,
                    _json(payload),
                    created_at,
                    expires_at,
                ),
            )


def run_benchmark(settings: Settings) -> int:
    service = _deterministic_service(settings)
    service.initialize(seed_if_empty=False)
    service.reset_demo()

    gate_samples: list[float] = []
    for _ in range(10_000):
        started = time.perf_counter()
        service.gate_version(
            scope=service.scope,
            version_id="ver_demo_canary_001",
            principal_ref="principal://demo/alice",
            purpose_ref=DEMO_PURPOSE,
        )
        gate_samples.append((time.perf_counter() - started) * 1_000)
    gate_p95 = statistics.quantiles(gate_samples, n=100)[94]

    scan_started = time.perf_counter()
    scan = service.scan(
        ScanRequest(**service.scope.model_dump(), root_version_ids=["ver_demo_canary_001"])
    )
    scanner_evaluation_seconds = time.perf_counter() - scan_started
    _add_synthetic_cache_derivatives(service, 10_000 - 14)
    fixture = _event_fixture(EventType.DELETE.value)
    event = LifecycleEvent.model_validate(fixture["event"])
    event_started = time.perf_counter()
    accepted = service.accept_event(event, fixture["signature"])
    event_acceptance_seconds = time.perf_counter() - event_started
    propagation_started = time.perf_counter()
    service.drain_actions(force=True)
    propagation_seconds = time.perf_counter() - propagation_started
    total_event_to_receipt_seconds = time.perf_counter() - event_started

    labels = json.loads((settings.state_dir / "seeded-inventory.json").read_text(encoding="utf-8"))[
        "records"
    ]
    expected_positive_ids = {
        stable_id(
            "finding",
            scan.scan_id,
            record["content_ref"],
            "exact" if record["label"] == "exact_positive" else "semantic",
        )
        for record in labels
        if record["label"] != "negative"
    }
    observed_candidate_ids = {
        finding.finding_id for finding in scan.findings if finding.finding_class.value != "tracked"
    }
    true_positives = len(expected_positive_ids & observed_candidate_ids)
    false_positives = len(observed_candidate_ids - expected_positive_ids)
    false_negatives = len(expected_positive_ids - observed_candidate_ids)
    recall = true_positives / len(expected_positive_ids) if expected_positive_ids else 0.0
    precision = true_positives / len(observed_candidate_ids) if observed_candidate_ids else 0.0
    receipt = service.get_receipt(accepted.run_id)
    probes = service.run_probes(event)
    report = {
        "fixture_manifest_hash": fixture_manifest_hash(service.manifest),
        "reference_hardware": service.manifest["reference_hardware"],
        "scanner": {
            "true_positives": true_positives,
            "false_positives": false_positives,
            "false_negatives": false_negatives,
            "recall": recall,
            "precision": precision,
            "target_recall": 0.95,
            "target_precision": 0.90,
            "evaluation_seconds": scanner_evaluation_seconds,
        },
        "gate": {"samples": 10_000, "p95_ms": gate_p95, "target_p95_ms": 20.0},
        "propagation": {
            "derivatives": 10_000,
            "seconds": propagation_seconds,
            "target_seconds": 60.0,
            "event_acceptance_seconds": event_acceptance_seconds,
            "total_event_to_receipt_seconds": total_event_to_receipt_seconds,
        },
        "probes": probes,
        "receipt_outcome": receipt.outcome.value,
        "targets_are_hypotheses": True,
    }
    path = _write_report(settings, "benchmark-report.json", report)
    print(json.dumps({"report": str(path), **report}, indent=2))
    return 0


def write_openapi(settings: Settings, output: Path) -> int:
    service = _deterministic_service(settings)
    app = create_app(settings=settings, service=service, start_worker=False)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(app.openapi(), indent=2) + "\n", encoding="utf-8")
    print(output)
    return 0


def verify_receipt_file(
    settings: Settings,
    path: Path,
    expected_head: str,
    expected_sequence: int,
    prior_trusted_head: str | None,
) -> int:
    service = _deterministic_service(settings)
    service.initialize(seed_if_empty=False)
    body = json.loads(path.read_text(encoding="utf-8"))
    receipts = body if isinstance(body, list) else [body]
    parsed = [ExecutionReceipt.model_validate(item) for item in receipts]
    result = service.verify_receipts(
        ReceiptVerifyRequest(
            receipts=parsed,
            expected_sequence=expected_sequence,
            expected_head=expected_head,
            prior_trusted_head=prior_trusted_head,
        )
    )
    print(result.model_dump_json(indent=2))
    return 0 if result.valid else 1


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="lethe-control")
    subcommands = root.add_subparsers(dest="command", required=True)
    subcommands.add_parser("demo")
    subcommands.add_parser("seed")
    subcommands.add_parser("baseline")
    event = subcommands.add_parser("event")
    event.add_argument("event_type", choices=[item.value for item in EventType])
    event_only = subcommands.add_parser("event-only")
    event_only.add_argument("event_type", choices=[item.value for item in EventType])
    scan = subcommands.add_parser("scan")
    scan.add_argument("--root-version-id", default="ver_demo_canary_001")
    assess = subcommands.add_parser("assess")
    assess.add_argument("--root-version-id", default="ver_demo_canary_001")
    assess.add_argument("--output", type=Path)
    propagate = subcommands.add_parser("propagate")
    propagate.add_argument("--run-id")
    probe = subcommands.add_parser("probe")
    probe.add_argument("event_type", choices=[item.value for item in EventType])
    receipt = subcommands.add_parser("receipt")
    receipt.add_argument("run_id")
    receipt.add_argument("--output", type=Path, required=True)
    subcommands.add_parser("benchmark")
    subcommands.add_parser("reset")
    serve = subcommands.add_parser("serve")
    serve.add_argument("--host", default="127.0.0.1", choices=["127.0.0.1", "localhost"])
    serve.add_argument("--port", type=int, default=8000)
    openapi = subcommands.add_parser("openapi")
    openapi.add_argument("--output", type=Path, required=True)
    verify = subcommands.add_parser("verify-receipt")
    verify.add_argument("path", type=Path)
    verify.add_argument("--expected-head", required=True)
    verify.add_argument("--expected-sequence", required=True, type=int)
    verify.add_argument("--prior-trusted-head")
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    settings = Settings.from_env()
    if args.command == "demo":
        return run_demo(settings)
    if args.command == "seed":
        service = _deterministic_service(settings)
        service.initialize()
        print(settings.state_dir)
        return 0
    if args.command == "baseline":
        return run_baseline(settings)
    if args.command == "event":
        return run_event(settings, args.event_type)
    if args.command == "event-only":
        return accept_event_only(settings, args.event_type)
    if args.command == "scan":
        return run_scan_stage(settings, args.root_version_id)
    if args.command == "assess":
        return run_assessment_stage(settings, args.root_version_id, args.output)
    if args.command == "propagate":
        return run_propagation_stage(settings, args.run_id)
    if args.command == "probe":
        return run_probe_stage(settings, args.event_type)
    if args.command == "receipt":
        return export_receipt(settings, args.run_id, args.output)
    if args.command == "benchmark":
        return run_benchmark(settings)
    if args.command == "reset":
        service = _deterministic_service(settings)
        service.initialize(seed_if_empty=False)
        service.reset_demo()
        print(settings.state_dir)
        return 0
    if args.command == "serve":
        uvicorn.run(create_app(settings=settings), host=args.host, port=args.port)
        return 0
    if args.command == "openapi":
        return write_openapi(settings, args.output)
    if args.command == "verify-receipt":
        return verify_receipt_file(
            settings,
            args.path,
            args.expected_head,
            args.expected_sequence,
            args.prior_trusted_head,
        )
    return 2


if __name__ == "__main__":
    sys.exit(main())
