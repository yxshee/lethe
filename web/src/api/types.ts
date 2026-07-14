import type { components } from "./schema";

// API-facing types are aliases of the checked-in OpenAPI generation. Keep
// dashboard-only fixture metadata below this block so schema drift stays loud.
export type EventType = components["schemas"]["EventType"];
export type ObjectKind = components["schemas"]["ObjectKind"];
export type LifecycleState = components["schemas"]["LifecycleState"];
export type RunOutcome = components["schemas"]["RunOutcome"];
export type FindingClass = components["schemas"]["FindingClass"];

export type CorrectionPayload = components["schemas"]["CorrectionPayload"];
export type PermissionChangePayload = components["schemas"]["PermissionChangePayload"];
export type LifecycleEvent = components["schemas"]["LifecycleEvent"];
export type QueryRequest = components["schemas"]["QueryRequest"];
export type QueryResponse = components["schemas"]["QueryResponse"];
export type EventAccepted = components["schemas"]["EventAccepted"];
export type RunStatus = components["schemas"]["RunStatusResponse"];
export type KnowledgeNode = components["schemas"]["GraphNode"];
export type LineageEdge = components["schemas"]["GraphEdge"];
export type GraphResponse = components["schemas"]["GraphResponse"];
export type ScannerFinding = components["schemas"]["ScanFinding"];
export type ScanRequest = components["schemas"]["ScanRequest"];
export type ScanResponse = components["schemas"]["ScanResponse"];
export type ReceiptAction = components["schemas"]["ReceiptAction"];
export type ReceiptExclusion = components["schemas"]["ReceiptExclusion"];
export type ExecutionReceipt = components["schemas"]["ExecutionReceipt"];
export type ReceiptVerifyRequest = components["schemas"]["ReceiptVerifyRequest"];
export type ReceiptVerifyResponse = components["schemas"]["ReceiptVerifyResponse"];
export type HealthResponse = components["schemas"]["HealthResponse"];

export type ScopeIds = Pick<
  LifecycleEvent,
  "tenant_id" | "workspace_id" | "environment_id"
>;

export const DEMO_SCOPE = {
  tenant_id: "ten_demo",
  workspace_id: "ws_demo",
  environment_id: "env_local",
} as const satisfies ScopeIds;

export const FIXTURE_TOKENS = {
  operator: "lethe-control-demo",
  alice: "lethe-alice-demo",
  bob: "lethe-bob-demo",
  unsafe: "lethe-unsafe-demo",
} as const;

export type DemoPrincipal = "alice" | "bob";

// The signed fixture bundle is a static dashboard asset, not an API contract.
export interface DemoEventFixture {
  label: string;
  event: LifecycleEvent;
  signature: string;
}

export interface DemoEventBundle {
  events: DemoEventFixture[];
}
