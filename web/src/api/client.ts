import {
  DEMO_SCOPE,
  FIXTURE_TOKENS,
  type DemoEventBundle,
  type DemoEventFixture,
  type EventAccepted,
  type ExecutionReceipt,
  type GraphResponse,
  type HealthResponse,
  type LifecycleEvent,
  type PostureAssessmentReport,
  type QueryRequest,
  type QueryResponse,
  type ReceiptVerifyRequest,
  type ReceiptVerifyResponse,
  type RunStatus,
  type ScanResponse,
} from "./types";

export class ApiError extends Error {
  readonly status: number;
  readonly reasonCode?: string;

  constructor(message: string, status: number, reasonCode?: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.reasonCode = reasonCode;
  }
}

interface ErrorBody {
  detail?: string | { message?: string; reason_code?: string };
  message?: string;
  reason_code?: string;
}

function messageFromError(body: ErrorBody | null, status: number): string {
  if (typeof body?.detail === "string") return body.detail;
  if (body?.detail && typeof body.detail === "object" && body.detail.message) {
    return body.detail.message;
  }
  return body?.message ?? `Request failed (${status})`;
}

function reasonFromError(body: ErrorBody | null): string | undefined {
  if (body?.detail && typeof body.detail === "object") return body.detail.reason_code;
  return body?.reason_code;
}

async function requestJson<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: {
      Accept: "application/json",
      ...(init.body ? { "Content-Type": "application/json" } : {}),
      ...init.headers,
    },
  });

  if (!response.ok) {
    let body: ErrorBody | null = null;
    try {
      body = (await response.json()) as ErrorBody;
    } catch {
      // Non-JSON backend failures still produce a useful status error.
    }
    throw new ApiError(messageFromError(body, response.status), response.status, reasonFromError(body));
  }

  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

function bearer(token: string): HeadersInit {
  return { Authorization: `Bearer ${token}` };
}

export const api = {
  health: () => requestJson<HealthResponse>("/healthz"),

  query: (body: QueryRequest, token: string) =>
    requestJson<QueryResponse>("/api/v1/query", {
      method: "POST",
      headers: bearer(token),
      body: JSON.stringify(body),
    }),

  unsafeQuery: (body: QueryRequest) =>
    requestJson<QueryResponse>("/api/v1/demo/unsafe-query", {
      method: "POST",
      headers: bearer(FIXTURE_TOKENS.unsafe),
      body: JSON.stringify(body),
    }),

  submitEvent: (event: LifecycleEvent, signature: string) =>
    requestJson<EventAccepted>("/api/v1/events", {
      method: "POST",
      headers: {
        ...bearer(FIXTURE_TOKENS.operator),
        "X-Lethe-Event-Signature": signature,
      },
      body: JSON.stringify(event),
    }),

  getRun: (runId: string) =>
    requestJson<RunStatus>(`/api/v1/runs/${encodeURIComponent(runId)}`, {
      headers: bearer(FIXTURE_TOKENS.operator),
    }),

  getGraph: (versionId: string) =>
    requestJson<GraphResponse>(`/api/v1/graph/${encodeURIComponent(versionId)}`, {
      headers: bearer(FIXTURE_TOKENS.operator),
    }),

  scan: (targetVersionId: string) =>
    requestJson<ScanResponse>("/api/v1/scans", {
      method: "POST",
      headers: bearer(FIXTURE_TOKENS.operator),
      body: JSON.stringify({ ...DEMO_SCOPE, root_version_ids: [targetVersionId] }),
    }),

  assess: (targetVersionId: string) =>
    requestJson<PostureAssessmentReport>("/api/v1/assessments", {
      method: "POST",
      headers: bearer(FIXTURE_TOKENS.operator),
      body: JSON.stringify({ ...DEMO_SCOPE, root_version_ids: [targetVersionId] }),
    }),

  getReceipt: (runId: string) =>
    requestJson<ExecutionReceipt>(`/api/v1/receipts/${encodeURIComponent(runId)}`, {
      headers: bearer(FIXTURE_TOKENS.operator),
    }),

  verifyReceipt: (body: ReceiptVerifyRequest) =>
    requestJson<ReceiptVerifyResponse>("/api/v1/receipts/verify", {
      method: "POST",
      headers: bearer(FIXTURE_TOKENS.operator),
      body: JSON.stringify(body),
    }),
};

function isEventFixture(value: unknown): value is DemoEventFixture {
  if (!value || typeof value !== "object") return false;
  const fixture = value as Partial<DemoEventFixture>;
  return (
    typeof fixture.label === "string" &&
    typeof fixture.signature === "string" &&
    Boolean(fixture.event) &&
    typeof fixture.event?.event_type === "string"
  );
}

export async function loadDemoEventFixtures(): Promise<DemoEventFixture[]> {
  const response = await fetch("/demo-events.json", {
    headers: { Accept: "application/json" },
    cache: "no-store",
  });
  if (!response.ok) throw new ApiError("Signed demo events are not available", response.status);

  const body = (await response.json()) as DemoEventBundle | DemoEventFixture[];
  const events = Array.isArray(body) ? body : body.events;
  if (!Array.isArray(events) || !events.every(isEventFixture)) {
    throw new ApiError("Signed demo event bundle has an invalid shape", 500, "invalid_fixture_bundle");
  }
  return events;
}
