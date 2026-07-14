import { expect, test } from "@playwright/test";

const deleteEvent = {
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

test.beforeEach(async ({ page }) => {
  await page.route("**/*", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    if (path === "/healthz") return route.fulfill({ json: { status: "ok" } });
    if (path === "/demo-events.json") {
      return route.fulfill({ json: { events: [{ label: "Delete policy handbook", event: deleteEvent, signature: "signed-fixture-value" }] } });
    }
    if (path === "/api/v1/demo/unsafe-query") {
      return route.fulfill({ json: { denied: false, answer: "The restricted canary is Topaz-17.", supporting_version_ids: ["ver_cache_01"], reason_codes: ["unsafe_demo_bypass"] } });
    }
    if (path === "/api/v1/query") {
      return route.fulfill({ json: { denied: true, answer: null, supporting_version_ids: [], reason_codes: ["lifecycle_denied"] } });
    }
    if (path === "/api/v1/events") {
      return route.fulfill({ status: 202, json: { tenant_id: "ten_demo", workspace_id: "ws_demo", environment_id: "env_local", event_id: "evt_delete_01", run_id: "run_delete_01", duplicate: false, gate_state: "denied" } });
    }
    if (path === "/api/v1/graph/ver_01") {
      return route.fulfill({ json: { tenant_id: "ten_demo", workspace_id: "ws_demo", environment_id: "env_local", requested_version_id: "ver_01", root_version_ids: ["ver_01"], nodes: [{ version_id: "ver_01", object_id: "obj_01", kind: "source", lifecycle_state: "denied" }, { version_id: "ver_chunk_01", object_id: "obj_chunk_01", kind: "chunk", lifecycle_state: "denied" }], edges: [{ parent_version_id: "ver_01", child_version_id: "ver_chunk_01" }] } });
    }
    if (path === "/api/v1/scans") {
      return route.fulfill({ json: { tenant_id: "ten_demo", workspace_id: "ws_demo", environment_id: "env_local", scan_id: "scan_01", denominator: 2, completed_at: "2026-07-14T10:00:00Z", findings: [{ finding_id: "finding_01", finding_class: "tracked", derivative_kind: "chunk", connector_ref: "connector://local-objects", target_version_id: "ver_chunk_01", confidence: 1 }] } });
    }
    if (path === "/api/v1/runs/run_delete_01") {
      return route.fulfill({ json: { tenant_id: "ten_demo", workspace_id: "ws_demo", environment_id: "env_local", run_id: "run_delete_01", event_id: "evt_delete_01", phase: "completed", outcome: "succeeded", counts: { succeeded: 2, failed: 0 }, failures: [], exclusions: [], started_at: "2026-07-14T10:00:00Z", completed_at: "2026-07-14T10:00:01Z" } });
    }
    if (path === "/api/v1/receipts/run_delete_01") {
      return route.fulfill({ json: { tenant_id: "ten_demo", workspace_id: "ws_demo", environment_id: "env_local", receipt_version: "1", agent_id: "agent_demo_01", key_id: "key_demo_01", chain_sequence: 1, previous_receipt_hash: null, event_id: "evt_delete_01", run_id: "run_delete_01", source_version_ids: ["ver_01"], policy_version: 2, scope: { scope_manifest_hash: "manifest_head_01", scope_selected_by: "authority://demo/data-owner", requested_evidence_level: "L3", scan_cutoff: "2026-07-14T10:00:00Z", registered_stores: 5, registered_connectors: 5, reachable_connectors: 5, connector_capability_versions: [], unsupported_connectors: [], graph_snapshot_hash: "graph_hash_01" }, coverage_level: "L3", counts: { expected_tracked: 2, found_tracked: 2, scanner_candidates: 0, actions_attempted: 2, actions_succeeded: 2, actions_failed: 0, targets_verified: 2 }, actions: [], verification_results: [], exclusions: [], unsupported_sinks: [], residual_risks: [], outcome: "succeeded", started_at: "2026-07-14T10:00:00Z", completed_at: "2026-07-14T10:00:01Z", evidence_manifest_hash: "evidence_hash_01", signature_algorithm: "Ed25519", signature: "fixture_signature_01" } });
    }
    if (path === "/api/v1/receipts/verify") {
      return route.fulfill({ json: { valid: true, verified_sequence: 1, computed_head: "trusted_head_01", reason_codes: [] } });
    }
    return route.continue();
  });
});

test("runs disclosure comparison from one control room", async ({ page }) => {
  await page.goto("/");

  await expect(page).toHaveTitle("Lethe — internal codename.");
  await expect(page.getByRole("heading", { name: /Lifecycle control/i })).toBeVisible();
  await expect(page.getByText("Internal codename")).toBeVisible();

  await page.getByRole("button", { name: "Run unsafe baseline" }).click();
  await expect(page.getByText("The restricted canary is Topaz-17.")).toBeVisible();

  await page.getByRole("button", { name: "Run through gate" }).click();
  await expect(page.getByText("No answer released.")).toBeVisible();
  await expect(page.getByText("lifecycle_denied")).toBeVisible();

  await page.getByLabel("I confirm this bounded local fixture action.").check();
  await page.getByRole("button", { name: "Install deny fence + queue repair" }).click();
  await expect(page.getByText("DENY FENCE ACTIVE")).toBeVisible();
  await expect(page.getByText("run_delete_01").first()).toBeVisible();
  await expect(page.getByRole("heading", { name: "Signed execution receipt" })).toBeVisible();
  await expect(page.locator(".receipt-ledger").getByText("succeeded", { exact: true }).first()).toBeVisible();

  await page.getByRole("button", { name: /Scan declared stores/ }).click();
  await expect(page.getByText("tracked", { exact: true })).toBeVisible();

  await page.getByLabel("Expected sequence").fill("1");
  await page.getByLabel("Expected final head").fill("trusted_head_01");
  await page.getByRole("button", { name: "Verify signature + chain" }).click();
  await expect(page.getByText("Signature and supplied chain head verify")).toBeVisible();
});
