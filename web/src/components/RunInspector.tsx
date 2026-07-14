import type { ReceiptAction, ReceiptExclusion, RunStatus, ScanResponse } from "../api/types";

type RunRecord = ReceiptAction | ReceiptExclusion;

function displayReason(record: RunRecord): string {
  return record.reason_code?.replaceAll("_", " ") ?? "unspecified failure";
}

function recordRef(record: RunRecord): string {
  if ("target_version_ref" in record) return record.target_version_ref;
  return record.target_ref ?? record.connector_ref ?? "scope";
}

function RecordList({ title, records }: { title: string; records: RunRecord[] | undefined }) {
  if (!records?.length) return null;
  return (
    <div className="record-block">
      <h4>{title}</h4>
      <ul className="reason-list">
        {records.map((record, index) => (
          <li key={`${record.reason_code}:${recordRef(record)}:${index}`}>
            <span>{displayReason(record)}</span>
            <code>{recordRef(record)}</code>
          </li>
        ))}
      </ul>
    </div>
  );
}

function progressFor(run: RunStatus): { done: number; total: number; percent: number } {
  const total = run.counts?.expected ?? run.counts?.attempted ?? 0;
  const done = (run.counts?.succeeded ?? 0) + (run.counts?.failed ?? 0);
  return { done, total, percent: total ? Math.min(100, (done / total) * 100) : 0 };
}

export function RunInspector({ run, scan }: { run: RunStatus | null; scan: ScanResponse | null }) {
  const progress = run ? progressFor(run) : null;
  const inspected = scan?.findings.length ?? 0;
  const denominator = scan?.denominator;

  return (
    <aside className="inspector" aria-label="Run and scanner status">
      <section className="inspector-section">
        <div className="section-kicker-row">
          <span className="section-number">04—06</span>
          <span className={`status-pip ${run ? "status-pip--active" : ""}`} aria-hidden="true" />
        </div>
        <h3>Propagation run</h3>
        {!run ? (
          <p className="quiet-copy">Submit a signed event. Denial commits before store cleanup begins.</p>
        ) : (
          <>
            <div className="run-heading">
              <strong>{run.outcome ?? run.phase}</strong>
              <code title={run.run_id}>{run.run_id}</code>
            </div>
            <div
              className="progress-track"
              role="progressbar"
              aria-label="Propagation progress"
              aria-valuemin={0}
              aria-valuemax={progress?.total || 1}
              aria-valuenow={progress?.done || 0}
            >
              <span style={{ transform: `scaleX(${(progress?.percent ?? 0) / 100})` }} />
            </div>
            <div className="count-line">
              <span>{progress?.done ?? 0} resolved</span>
              <span>{run.counts?.verified ?? 0} read back</span>
              <span>{run.counts?.failed ?? 0} failed</span>
            </div>
            <RecordList title="Failures" records={run.failures} />
            <RecordList title="Exclusions" records={run.exclusions} />
          </>
        )}
      </section>

      <section className="inspector-section inspector-section--scan">
        <div className="section-kicker-row">
          <span className="section-number">05 / Inventory</span>
          <span>{inspected} inspected</span>
        </div>
        <h3>Scanner findings</h3>
        {!scan ? (
          <p className="quiet-copy">Inventory declared stores after fence installation. Semantic matches remain report-only.</p>
        ) : (
          <>
            <div className="scan-denominator">
              <strong>{denominator ?? "—"}</strong>
              <span>declared denominator</span>
            </div>
            {scan.findings.length ? (
              <ul className="finding-list">
                {scan.findings.map((finding) => {
                  const findingClass = finding.finding_class;
                  return (
                  <li key={finding.finding_id}>
                    <div>
                      <span className={`finding-class finding-class--${findingClass}`}>
                        {findingClass.replaceAll("_", " ")}
                      </span>
                      <strong>{finding.derivative_kind}</strong>
                    </div>
                    <span>{finding.confidence == null ? "exact" : `${Math.round(finding.confidence * 100)}%`}</span>
                  </li>
                  );
                })}
              </ul>
            ) : (
              <p className="quiet-copy">No active candidates found in declared stores.</p>
            )}
          </>
        )}
      </section>
    </aside>
  );
}
