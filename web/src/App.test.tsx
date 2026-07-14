import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import type { DemoEventFixture, ExecutionReceipt, LifecycleEvent, PostureAssessmentReport } from "./api/types";

const deleteEvent: LifecycleEvent = {
  schema_version: "1",
  event_id: "evt_delete_01",
  idempotency_key: "demo-delete-01",
  tenant_id: "ten_demo",
  workspace_id: "ws_demo",
  environment_id: "env_local",
  issuer_key_id: "issuer_demo_01",
  audience: "agent_demo_01",
  authority_ref: "authority://demo/source-admin",
  nonce: "nonce-delete-01",
  target_version_id: "ver_01",
  event_type: "delete",
  source_sequence: 42,
  policy_version: 2,
  occurred_at: "2026-07-14T10:00:00Z",
  effective_at: "2026-07-14T10:00:00Z",
  command_expires_at: "2099-07-14T10:05:00Z",
  actor_ref: "actor://demo/admin",
  reason_code: "source_deleted",
  correction: null,
  permission_change: null,
};

const fixture: DemoEventFixture = {
  label: "Delete policy handbook",
  event: deleteEvent,
  signature: "signed-fixture-value",
};

const receipt: ExecutionReceipt = {
  receipt_version: "1",
  tenant_id: "ten_demo",
  workspace_id: "ws_demo",
  environment_id: "env_local",
  agent_id: "agent_demo_01",
  key_id: "key_demo_01",
  chain_sequence: 7,
  previous_receipt_hash: "previous-hash-value",
  event_id: "evt_delete_01",
  run_id: "run_01",
  source_version_ids: ["ver_01"],
  policy_version: 2,
  scope: {
    scope_manifest_hash: "manifest-hash-value",
    scope_selected_by: "authority://demo/data-owner",
    requested_evidence_level: "L3",
    scan_cutoff: "2026-07-14T10:00:00Z",
    registered_stores: 5,
    registered_connectors: 5,
    reachable_connectors: 5,
    connector_capability_versions: [],
    unsupported_connectors: [],
    graph_snapshot_hash: "graph-hash-value",
  },
  coverage_level: "L3",
  counts: {
    expected_tracked: 6,
    found_tracked: 6,
    scanner_candidates: 1,
    actions_attempted: 6,
    actions_succeeded: 6,
    actions_failed: 0,
    targets_verified: 6,
  },
  actions: [],
  verification_results: [],
  exclusions: [],
  unsupported_sinks: [],
  residual_risks: [],
  outcome: "succeeded",
  started_at: "2026-07-14T10:00:00Z",
  completed_at: "2026-07-14T10:00:03Z",
  evidence_manifest_hash: "evidence-hash-value",
  signature_algorithm: "Ed25519",
  signature: "receipt-signature-value",
};

const postureReport: PostureAssessmentReport = {
  tenant_id: "ten_demo",
  workspace_id: "ws_demo",
  environment_id: "env_local",
  schema_version: "1",
  assessment_id: "assess_01",
  scan_id: "scan_01",
  read_only: true,
  coverage_level: "L2",
  scope_manifest_hash: "manifest-hash-value",
  denominators: { chunk: 0 },
  connector_freshness: [
    {
      connector_ref: "connector://sqlite/cache",
      store_ref: null,
      capability_version: "1",
      freshness_cursor: "cursor-01",
      reachable: true,
      kinds: ["cache"],
    },
  ],
  findings: [],
  lineage_gaps: [],
  unsupported_scope: [],
  incidents: [],
  severity_counts: { critical: 0, high: 0, medium: 0, informational: 0 },
  outcome: "succeeded",
  started_at: "2026-07-14T10:00:00Z",
  completed_at: "2026-07-14T10:00:03Z",
};

function json(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function pathOf(input: RequestInfo | URL): string {
  const value = typeof input === "string" ? input : input instanceof Request ? input.url : input.toString();
  return new URL(value, "http://localhost").pathname;
}

function installApiMock() {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
    const path = pathOf(input);
    if (path === "/healthz") return json({ status: "ok", profile: "demo" });
    if (path === "/demo-events.json") return json({ events: [fixture] });
    if (path === "/api/v1/demo/unsafe-query") {
      return json({ denied: false, answer: "The restricted canary is Topaz-17.", supporting_version_ids: ["ver_cache_01"], reason_codes: ["unsafe_demo_bypass"] });
    }
    if (path === "/api/v1/query") return json({ denied: true, answer: null, supporting_version_ids: [], reason_codes: ["lifecycle_denied"] });
    if (path === "/api/v1/events") return json({ event_id: "evt_delete_01", run_id: "run_01", status: "accepted" }, 202);
    if (path === "/api/v1/graph/ver_01") {
      return json({
        tenant_id: "ten_demo",
        workspace_id: "ws_demo",
        environment_id: "env_local",
        graph_snapshot_hash: "graph-hash-value",
        nodes: [
          { object_id: "obj_01", version_id: "ver_01", kind: "source", lifecycle_state: "denied" },
          { object_id: "obj_chunk_01", version_id: "ver_chunk_01", kind: "chunk", lifecycle_state: "tombstoned" },
        ],
        edges: [{ parent_version_id: "ver_01", child_version_id: "ver_chunk_01" }],
      });
    }
    if (path === "/api/v1/runs/run_01") {
      return json({
        tenant_id: "ten_demo",
        workspace_id: "ws_demo",
        environment_id: "env_local",
        run_id: "run_01",
        phase: "complete",
        outcome: "succeeded",
        counts: { expected: 6, attempted: 6, succeeded: 6, failed: 0, verified: 6 },
        failures: [],
        exclusions: [],
        residual_risks: [],
      });
    }
    if (path === "/api/v1/scans") {
      return json({
        tenant_id: "ten_demo",
        workspace_id: "ws_demo",
        environment_id: "env_local",
        scan_id: "scan_01",
        denominator: 6,
        findings: [{
          finding_id: "finding_01",
          finding_class: "exact_untracked",
          derivative_kind: "cache",
          connector_ref: "connector://sqlite/cache",
          target_version_id: "ver_cache_01",
          confidence: 1,
        }],
      });
    }
    if (path === "/api/v1/assessments") return json(postureReport);
    if (path === "/api/v1/receipts/run_01") return json(receipt);
    if (path === "/api/v1/receipts/verify") return json({ valid: true, signature_valid: true, chain_valid: true });
    return json({ detail: "not found" }, 404);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("Lethe dashboard", () => {
  beforeEach(() => {
    installApiMock();
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("labels internal scope and never persists fixture credentials", async () => {
    const storageWrite = vi.spyOn(Storage.prototype, "setItem");
    render(<App />);

    expect(document.title).toBe("Lethe — internal codename.");
    expect(screen.getByText("Internal codename")).toBeInTheDocument();
    expect(screen.getByText("Scoped signer assertion. Record integrity—not universal deletion completeness.")).toBeInTheDocument();
    expect(await screen.findByText("Delete policy handbook")).toBeInTheDocument();
    expect(storageWrite).not.toHaveBeenCalled();
  });

  it("contrasts unsafe disclosure with a fail-closed gated answer", async () => {
    const user = userEvent.setup();
    const fetchMock = installApiMock();
    render(<App />);

    await user.click(screen.getByRole("button", { name: "Run unsafe baseline" }));
    expect(await screen.findByText("The restricted canary is Topaz-17.")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Run through gate" }));
    expect(await screen.findByText("No answer released.")).toBeInTheDocument();
    expect(screen.getByText("lifecycle_denied")).toBeInTheDocument();

    const queryCall = fetchMock.mock.calls.find(([input]) => pathOf(input as RequestInfo | URL) === "/api/v1/query");
    expect(queryCall).toBeDefined();
    expect(new Headers(queryCall?.[1]?.headers).get("Authorization")).toBe("Bearer lethe-alice-demo");
  });

  it("sends exact signed event, polls run, scans, and verifies receipt", async () => {
    const user = userEvent.setup();
    const fetchMock = installApiMock();
    render(<App />);

    await screen.findByText("Delete policy handbook");
    await user.click(screen.getByRole("checkbox", { name: "I confirm this bounded local fixture action." }));
    await user.click(screen.getByRole("button", { name: "Install deny fence + queue repair" }));

    expect(await screen.findByText("DENY FENCE ACTIVE")).toBeInTheDocument();
    expect((await screen.findAllByText("ver_chunk_01")).length).toBeGreaterThan(0);
    expect(await screen.findByText("Ed25519")).toBeInTheDocument();

    const eventCall = fetchMock.mock.calls.find(([input]) => pathOf(input as RequestInfo | URL) === "/api/v1/events");
    expect(eventCall).toBeDefined();
    expect(new Headers(eventCall?.[1]?.headers).get("Authorization")).toBe("Bearer lethe-control-demo");
    expect(new Headers(eventCall?.[1]?.headers).get("X-Lethe-Event-Signature")).toBe("signed-fixture-value");
    expect(eventCall?.[1]?.body).toBe(JSON.stringify(deleteEvent));

    await user.click(screen.getByRole("button", { name: /Scan declared stores/i }));
    expect(await screen.findByText("exact untracked")).toBeInTheDocument();

    await user.type(screen.getByLabelText("Expected sequence"), "7");
    await user.type(screen.getByLabelText("Expected final head"), "trusted-final-head");
    expect(screen.getByLabelText("Prior trusted head (empty for genesis)")).toHaveValue("");
    await user.click(screen.getByRole("button", { name: "Verify signature + chain" }));
    expect(await screen.findByText("Signature and supplied chain head verify")).toBeInTheDocument();

    const verifyCall = fetchMock.mock.calls.find(([input]) => pathOf(input as RequestInfo | URL) === "/api/v1/receipts/verify");
    expect(JSON.parse(String(verifyCall?.[1]?.body))).toEqual({
      receipts: [receipt],
      expected_sequence: 7,
      expected_head: "trusted-final-head",
      prior_trusted_head: null,
    });

    await waitFor(() => {
      expect(fetchMock.mock.calls.some(([input]) => pathOf(input as RequestInfo | URL) === "/api/v1/runs/run_01")).toBe(true);
    });
  });

  it("runs a posture assessment and shows read-only evidence framing", async () => {
    const user = userEvent.setup();
    installApiMock();
    render(<App />);

    await user.click(screen.getByRole("button", { name: "Run posture assessment" }));

    expect(await screen.findByText("observed — not enforced")).toBeInTheDocument();
    const outcomeBadge = await screen.findByText("succeeded");
    expect(outcomeBadge).toHaveClass("badge--outcome-succeeded");
  });
});
