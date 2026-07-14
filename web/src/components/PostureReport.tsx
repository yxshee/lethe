import type { AssessmentFinding, ConnectorFreshness, PostureAssessmentReport } from "../api/types";

const severityOrder = ["critical", "high", "medium", "informational"] as const;

function shortRef(value: string | null | undefined): string {
  if (!value) return "—";
  return value.length > 28 ? `${value.slice(0, 19)}…${value.slice(-7)}` : value;
}

function FindingRow({ finding }: { finding: AssessmentFinding }) {
  return (
    <tr>
      <td><span className={`severity-text severity-text--${finding.severity}`}>{finding.severity}</span></td>
      <td><span className={`finding-class finding-class--${finding.finding_class}`}>{finding.finding_class.replaceAll("_", " ")}</span></td>
      <td><span className="kind-mark">{finding.derivative_kind}</span></td>
      <td title={finding.target_version_id ?? undefined}>{shortRef(finding.target_version_id)}</td>
      <td>{finding.evidence_level}</td>
      <td>{finding.gap_code?.replaceAll("_", " ") ?? "—"}</td>
    </tr>
  );
}

function ConnectorRow({ connector }: { connector: ConnectorFreshness }) {
  return (
    <li>
      <span>{connector.connector_ref}</span>
      <code>{connector.reachable ? "reachable" : "unreachable"}</code>
    </li>
  );
}

export function PostureReport({ report }: { report: PostureAssessmentReport | null }) {
  return (
    <div className="posture-report">
      <div className="posture-report-heading">
        <h3>Assessment evidence</h3>
        <div className="posture-tags">
          <span className="section-number posture-framing">observed — not enforced</span>
          {report?.fixture_metrics && <span className="badge badge--fixture">fixture evidence</span>}
        </div>
      </div>

      {!report ? (
        <p className="quiet-copy">Run a posture assessment to inventory current gaps against declared scope. Nothing here mutates state.</p>
      ) : (
        <>
          <div className="posture-badges">
            <span className={`badge badge--outcome-${report.outcome}`}>{report.outcome}</span>
            <span className="badge badge--coverage">{report.coverage_level}</span>
          </div>

          <div className="receipt-counts" aria-label="Severity counts">
            {severityOrder.map((severity) => (
              <div key={severity}>
                <strong>{report.severity_counts[severity] ?? 0}</strong>
                <span>{severity}</span>
              </div>
            ))}
          </div>

          <div className="table-wrap">
            <table>
              <caption className="visually-hidden">Posture findings</caption>
              <thead>
                <tr>
                  <th>Severity</th>
                  <th>Finding class</th>
                  <th>Derivative</th>
                  <th>Target version</th>
                  <th>Evidence level</th>
                  <th>Gap code</th>
                </tr>
              </thead>
              <tbody>
                {report.findings.length ? (
                  report.findings.map((finding) => <FindingRow finding={finding} key={finding.finding_id} />)
                ) : (
                  <tr>
                    <td colSpan={6}>No findings recorded against declared scope.</td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>

          <div className="record-block">
            <h4>Denominators</h4>
            <dl className="posture-denominators">
              {Object.entries(report.denominators).map(([kind, count]) => (
                <div key={kind}>
                  <dt>{kind}</dt>
                  <dd>{count}</dd>
                </div>
              ))}
            </dl>
          </div>

          <div className="record-block">
            <h4>Connector freshness</h4>
            <ul className="reason-list">
              {report.connector_freshness.map((connector) => (
                <ConnectorRow connector={connector} key={connector.connector_ref} />
              ))}
            </ul>
          </div>
        </>
      )}
    </div>
  );
}
