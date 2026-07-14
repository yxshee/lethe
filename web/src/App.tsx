import { useEffect, useMemo, useState } from "react";
import type { FormEvent } from "react";
import { api, ApiError, loadDemoEventFixtures } from "./api/client";
import {
  DEMO_SCOPE,
  FIXTURE_TOKENS,
  type DemoEventFixture,
  type DemoPrincipal,
  type EventType,
  type ExecutionReceipt,
  type GraphResponse,
  type QueryResponse,
  type ReceiptVerifyResponse,
  type RunStatus,
  type ScanResponse,
} from "./api/types";
import { LineageMap } from "./components/LineageMap";
import { ReceiptPanel } from "./components/ReceiptPanel";
import { RunInspector } from "./components/RunInspector";
import "./styles.css";

type BusyAction = "unsafe" | "query" | "event" | "scan" | "graph" | "verify" | null;
type ApiState = "checking" | "online" | "offline";

interface TrustedCheckpointInput {
  expectedSequence: string;
  expectedHead: string;
  priorTrustedHead: string;
}

const emptyCheckpoint: TrustedCheckpointInput = {
  expectedSequence: "",
  expectedHead: "",
  priorTrustedHead: "",
};

const eventOptions: Array<{ value: EventType; label: string }> = [
  { value: "delete", label: "Delete" },
  { value: "correct", label: "Correct" },
  { value: "expire", label: "Expire" },
  { value: "permission_change", label: "Permission change" },
];

const workflowSteps = [
  ["01", "Ingest"],
  ["02", "Derive"],
  ["03", "Ghost"],
  ["04", "Fence"],
  ["05", "Scan"],
  ["06", "Repair"],
  ["07", "Probe"],
  ["08", "Receipt"],
] as const;

function isTerminal(run: RunStatus | null): boolean {
  return Boolean(run?.outcome && ["succeeded", "partial", "failed", "unknown"].includes(run.outcome));
}

function decisionAllowed(result: QueryResponse | null): boolean | null {
  if (!result) return null;
  return !result.denied;
}

function explainError(error: unknown): string {
  if (error instanceof ApiError) {
    const reason = error.reasonCode ? ` · ${error.reasonCode.replaceAll("_", " ")}` : "";
    return `${error.message}${reason}`;
  }
  if (error instanceof Error) return error.message;
  return "Unexpected dashboard error";
}

function shortRef(value: string | undefined): string {
  if (!value) return "—";
  return value.length > 28 ? `${value.slice(0, 19)}…${value.slice(-7)}` : value;
}

function QueryResult({ mode, result }: { mode: "unsafe" | "gated"; result: QueryResponse | null }) {
  const allowed = decisionAllowed(result);
  const label = mode === "unsafe" ? "Untreated baseline" : "Authoritative gate";

  return (
    <div className={`query-result query-result--${mode} ${result ? "query-result--ready" : ""}`}>
      <div className="query-result-heading">
        <span>{label}</span>
        {result && (
          <strong className={`decision decision--${allowed === false ? "deny" : "allow"}`}>
            {allowed === false ? "denied" : allowed === true ? "disclosed" : "observed"}
          </strong>
        )}
      </div>
      {!result ? (
        <p>Run {mode === "unsafe" ? "baseline" : "gated query"} to record evidence.</p>
      ) : (
        <>
          <blockquote>{result.answer ?? (allowed === false ? "No answer released." : "No answer returned.")}</blockquote>
          <div className="result-meta">
            <span>{result.reason_codes?.[0] ?? (mode === "unsafe" ? "gate bypassed" : "policy evaluated")}</span>
            <span>{result.supporting_version_ids?.length ?? 0} contributing versions</span>
          </div>
        </>
      )}
    </div>
  );
}

export default function App() {
  const [apiState, setApiState] = useState<ApiState>("checking");
  const [busy, setBusy] = useState<BusyAction>(null);
  const [error, setError] = useState<string | null>(null);
  const [fixtures, setFixtures] = useState<DemoEventFixture[]>([]);
  const [fixtureError, setFixtureError] = useState<string | null>(null);
  const [eventType, setEventType] = useState<EventType>("delete");
  const [approvalConfirmed, setApprovalConfirmed] = useState(false);
  const [principal, setPrincipal] = useState<DemoPrincipal>("alice");
  const [query, setQuery] = useState("What is the restricted canary fact?");
  const [unsafeResult, setUnsafeResult] = useState<QueryResponse | null>(null);
  const [gatedResult, setGatedResult] = useState<QueryResponse | null>(null);
  const [runId, setRunId] = useState<string | null>(null);
  const [run, setRun] = useState<RunStatus | null>(null);
  const [scan, setScan] = useState<ScanResponse | null>(null);
  const [graph, setGraph] = useState<GraphResponse | null>(null);
  const [receipt, setReceipt] = useState<ExecutionReceipt | null>(null);
  const [verification, setVerification] = useState<ReceiptVerifyResponse | null>(null);
  const [checkpoint, setCheckpoint] = useState<TrustedCheckpointInput>(emptyCheckpoint);

  const selectedFixture = useMemo(
    () => fixtures.find((fixture) => fixture.event.event_type === eventType) ?? null,
    [eventType, fixtures],
  );
  const targetVersionId = selectedFixture?.event.target_version_id ?? "ver_01";

  useEffect(() => {
    let active = true;
    document.title = "Lethe — internal codename.";
    api.health()
      .then(() => active && setApiState("online"))
      .catch(() => active && setApiState("offline"));
    loadDemoEventFixtures()
      .then((loaded) => {
        if (!active) return;
        setFixtures(loaded);
        setFixtureError(null);
      })
      .catch((loadError: unknown) => active && setFixtureError(explainError(loadError)));
    return () => { active = false; };
  }, []);

  useEffect(() => {
    if (!runId) return;
    let active = true;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const poll = async () => {
      try {
        const latest = await api.getRun(runId);
        if (!active) return;
        setRun(latest);
        if (isTerminal(latest)) {
          try {
            const signed = await api.getReceipt(runId);
            if (active) setReceipt(signed);
          } catch (receiptError) {
            if (!(receiptError instanceof ApiError && receiptError.status === 404)) {
              setError(explainError(receiptError));
            }
          }
          return;
        }
      } catch (pollError) {
        if (active) setError(explainError(pollError));
      }
      if (active) timer = setTimeout(poll, 1_250);
    };

    void poll();
    return () => {
      active = false;
      if (timer) clearTimeout(timer);
    };
  }, [runId]);

  const completed = [
    apiState === "online",
    Boolean(graph?.nodes.length),
    Boolean(unsafeResult),
    Boolean(runId),
    Boolean(scan),
    isTerminal(run),
    decisionAllowed(gatedResult) === false,
    Boolean(receipt),
  ];

  async function runAction<T>(kind: Exclude<BusyAction, null>, action: () => Promise<T>): Promise<T | null> {
    setBusy(kind);
    setError(null);
    try {
      return await action();
    } catch (actionError) {
      setError(explainError(actionError));
      return null;
    } finally {
      setBusy(null);
    }
  }

  async function handleUnsafe() {
    const result = await runAction("unsafe", () =>
      api.unsafeQuery({ query, purpose_ref: "purpose://demo/answer" }),
    );
    if (result) setUnsafeResult(result);
  }

  async function handleQuery(event: FormEvent) {
    event.preventDefault();
    const result = await runAction("query", () =>
      api.query(
        { query, purpose_ref: "purpose://demo/answer" },
        FIXTURE_TOKENS[principal],
      ),
    );
    if (result) setGatedResult(result);
  }

  async function handleEvent(event: FormEvent) {
    event.preventDefault();
    if (!selectedFixture || !approvalConfirmed) return;
    const accepted = await runAction("event", () =>
      api.submitEvent(selectedFixture.event, selectedFixture.signature),
    );
    if (!accepted) return;

    setRunId(accepted.run_id);
    setRun(null);
    setReceipt(null);
    setVerification(null);
    setCheckpoint(emptyCheckpoint);

    const declaredGraph = await runAction("graph", () => api.getGraph(selectedFixture.event.target_version_id));
    if (declaredGraph) setGraph(declaredGraph);
  }

  async function handleGraph() {
    const declaredGraph = await runAction("graph", () => api.getGraph(targetVersionId));
    if (declaredGraph) setGraph(declaredGraph);
  }

  async function handleScan() {
    const result = await runAction("scan", () => api.scan(targetVersionId));
    if (result) setScan(result);
  }

  async function handleVerify() {
    if (!receipt) return;
    const expectedSequence = Number(checkpoint.expectedSequence);
    if (!Number.isSafeInteger(expectedSequence) || expectedSequence < 1 || !checkpoint.expectedHead.trim()) {
      setError("Enter an independently trusted sequence and final head before verification.");
      return;
    }
    const result = await runAction("verify", () => api.verifyReceipt({
      receipts: [receipt],
      expected_sequence: expectedSequence,
      expected_head: checkpoint.expectedHead.trim(),
      prior_trusted_head: checkpoint.priorTrustedHead.trim() || null,
    }));
    if (result) setVerification(result);
  }

  function updateCheckpoint(field: keyof TrustedCheckpointInput, value: string) {
    setCheckpoint((current) => ({ ...current, [field]: value }));
    setVerification(null);
  }

  return (
    <div className="app-shell">
      <header className="masthead">
        <a className="wordmark" href="#top" aria-label="Lethe control room home">
          <span>LE</span><span>THE</span>
        </a>
        <div className="codename-warning">
          <strong>Internal codename</strong>
          <span>Pending trademark + domain clearance</span>
        </div>
        <div className="system-markers" aria-label="System status">
          <span>localhost / v0.1</span>
          <span>closed-world proof</span>
          <span className={`api-state api-state--${apiState}`}>
            <i aria-hidden="true" /> API {apiState}
          </span>
        </div>
      </header>

      <main id="top">
        <section className="hero">
          <div className="hero-copy">
            <p className="eyebrow">Derived-data revocation control plane</p>
            <h1>Lifecycle control<br /><em>after</em> ingestion.</h1>
            <p className="hero-description">
              Install denial first. Inventory descendants next. Repair declared stores. Record exactly what remains.
            </p>
          </div>
          <dl className="proof-legend">
            <div><dt>Scope</dt><dd>1 workspace</dd></div>
            <div><dt>Stores</dt><dd>5 declared</dd></div>
            <div><dt>Evidence</dt><dd>L3 target</dd></div>
            <div><dt>Trust</dt><dd>local agent</dd></div>
          </dl>
        </section>

        <nav className="workflow-rail" aria-label="Eight-step demo progress">
          {workflowSteps.map(([number, label], index) => (
            <div className={`workflow-step ${completed[index] ? "workflow-step--done" : ""}`} key={number}>
              <span>{number}</span>
              <strong>{label}</strong>
            </div>
          ))}
        </nav>

        {error && (
          <div className="error-banner" role="alert">
            <strong>Control request failed</strong>
            <span>{error}</span>
            <button type="button" onClick={() => setError(null)} aria-label="Dismiss error">×</button>
          </div>
        )}

        <section className="query-section" aria-labelledby="query-heading">
          <div className="section-intro">
            <span className="section-number">03 + 07 / Disclosure test</span>
            <h2 id="query-heading">Ask once. Compare paths.</h2>
            <p>Unsafe endpoint proves ghost knowledge. Gated path evaluates current lineage and policy again before release.</p>
          </div>
          <form className="query-form" onSubmit={handleQuery}>
            <label htmlFor="query">Fixed recovery probe</label>
            <div className="query-input-row">
              <input id="query" value={query} onChange={(event) => setQuery(event.target.value)} required />
              <select
                aria-label="Query identity"
                value={principal}
                onChange={(event) => setPrincipal(event.target.value as DemoPrincipal)}
              >
                <option value="alice">as alice</option>
                <option value="bob">as bob</option>
              </select>
            </div>
            <div className="query-actions">
              <button className="button button--warning" type="button" onClick={handleUnsafe} disabled={busy !== null}>
                {busy === "unsafe" ? "Running…" : "Run unsafe baseline"}
              </button>
              <button className="button button--ink" type="submit" disabled={busy !== null}>
                {busy === "query" ? "Checking…" : "Run through gate"}
              </button>
            </div>
          </form>
          <div className="query-results">
            <QueryResult mode="unsafe" result={unsafeResult} />
            <QueryResult mode="gated" result={gatedResult} />
          </div>
        </section>

        <section className="dispatch-section" aria-labelledby="dispatch-heading">
          <div className="dispatch-context">
            <span className="section-number">04 / Immediate deny</span>
            <h2 id="dispatch-heading">Dispatch signed lifecycle event</h2>
            <p>Fixture authority signs exact body. Event acknowledgement means deny fence and durable action outbox committed—not cleanup complete.</p>
            <div className="approval-context">
              <strong>Pre-authorized fixture mutation</strong>
              <dl>
                <div><dt>Scope</dt><dd>ten_demo / ws_demo / env_local</dd></div>
                <div><dt>Plan</dt><dd>declared local stores only</dd></div>
                <div><dt>Maximum</dt><dd>100 actions</dd></div>
                <div><dt>Authority</dt><dd>demo data owner</dd></div>
              </dl>
            </div>
          </div>

          <form className="event-form" onSubmit={handleEvent}>
            <div className="form-field">
              <label htmlFor="event-type">Lifecycle trigger</label>
              <select id="event-type" value={eventType} onChange={(event) => setEventType(event.target.value as EventType)}>
                {eventOptions.map((option) => (
                  <option value={option.value} key={option.value}>{option.label}</option>
                ))}
              </select>
            </div>
            <div className="signed-fields">
              <div><span>Signed fixture</span><strong>{selectedFixture?.label ?? "Unavailable"}</strong></div>
              <div><span>Target version</span><code>{shortRef(selectedFixture?.event.target_version_id)}</code></div>
              <div><span>Source sequence</span><strong>{selectedFixture?.event.source_sequence ?? "—"}</strong></div>
              <div><span>Reason</span><strong>{selectedFixture?.event.reason_code?.replaceAll("_", " ") ?? "—"}</strong></div>
            </div>
            {fixtureError && <p className="fixture-error">{fixtureError}. Run fixture generation before dispatch.</p>}
            <label className="approval-check">
              <input
                type="checkbox"
                checked={approvalConfirmed}
                onChange={(event) => setApprovalConfirmed(event.target.checked)}
              />
              <span>I confirm this bounded local fixture action.</span>
            </label>
            <button
              className="button button--fence"
              type="submit"
              disabled={busy !== null || !selectedFixture || !approvalConfirmed}
            >
              {busy === "event" ? "Committing fence…" : "Install deny fence + queue repair"}
            </button>
            <div className={`fence-state ${runId ? "fence-state--active" : ""}`} aria-live="polite">
              <span>{runId ? "DENY FENCE ACTIVE" : "FENCE NOT INSTALLED"}</span>
              <code>{runId ?? "awaiting signed event"}</code>
            </div>
          </form>
        </section>

        <section className="evidence-grid">
          <div className="lineage-section">
            <div className="lineage-heading">
              <div>
                <span className="section-number">02 / Immutable graph</span>
                <h2>Declared descendants</h2>
              </div>
              <div className="lineage-actions">
                <span>{graph ? `root ${shortRef(graph.requested_version_id)}` : "No graph loaded"}</span>
                <button className="text-button" type="button" onClick={handleGraph} disabled={busy !== null}>
                  {busy === "graph" ? "Loading…" : "Refresh graph"}
                </button>
              </div>
            </div>
            <LineageMap graph={graph} />
          </div>

          <div className="inspector-column">
            <button className="scan-trigger" type="button" onClick={handleScan} disabled={busy !== null}>
              <span>{busy === "scan" ? "Inventory running…" : "Scan declared stores"}</span>
              <small>tracked · exact untracked · semantic candidate</small>
            </button>
            <RunInspector run={run} scan={scan} />
          </div>
        </section>

        <ReceiptPanel
          receipt={receipt}
          verification={verification}
          verifying={busy === "verify"}
          checkpoint={checkpoint}
          onCheckpointChange={updateCheckpoint}
          onVerify={handleVerify}
        />
      </main>

      <footer>
        <span>LETHe / local evidence surface</span>
        <p>Raw paths, fingerprints, credentials, and ACL identities stay outside this dashboard.</p>
        <span>v0.1 · {DEMO_SCOPE.workspace_id}</span>
      </footer>
    </div>
  );
}
