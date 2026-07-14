import type { ExecutionReceipt, ReceiptVerifyResponse } from "../api/types";

function compact(value: string | null | undefined, lead = 13): string {
  if (!value) return "—";
  if (value.length <= lead + 8) return value;
  return `${value.slice(0, lead)}…${value.slice(-7)}`;
}

export function ReceiptPanel({
  receipt,
  verification,
  verifying,
  checkpoint,
  onCheckpointChange,
  onVerify,
}: {
  receipt: ExecutionReceipt | null;
  verification: ReceiptVerifyResponse | null;
  verifying: boolean;
  checkpoint: {
    expectedSequence: string;
    expectedHead: string;
    priorTrustedHead: string;
  };
  onCheckpointChange: (
    field: "expectedSequence" | "expectedHead" | "priorTrustedHead",
    value: string,
  ) => void;
  onVerify: () => void;
}) {
  const checkpointReady = /^[1-9]\d*$/.test(checkpoint.expectedSequence) && Boolean(checkpoint.expectedHead.trim());

  return (
    <section className="receipt-section" aria-labelledby="receipt-heading">
      <div className="receipt-title-block">
        <span className="section-number">08 / Evidence</span>
        <h2 id="receipt-heading">Signed execution receipt</h2>
        <p>Scoped signer assertion. Record integrity—not universal deletion completeness.</p>
      </div>

      {!receipt ? (
        <div className="receipt-empty">
          <span aria-hidden="true">∅</span>
          <p>Receipt appears after declared actions and read-back reach a terminal outcome.</p>
        </div>
      ) : (
        <div className="receipt-ledger">
          <div className="receipt-seal" aria-hidden="true">
            <span>L3</span>
            <small>{receipt.outcome}</small>
          </div>
          <dl>
            <div><dt>Sequence</dt><dd>{receipt.chain_sequence}</dd></div>
            <div><dt>Agent</dt><dd>{receipt.agent_id}</dd></div>
            <div><dt>Algorithm</dt><dd>{receipt.signature_algorithm}</dd></div>
            <div><dt>Entry hash</dt><dd title={verification?.computed_head ?? undefined}>{compact(verification?.computed_head)}</dd></div>
            <div><dt>Previous</dt><dd title={receipt.previous_receipt_hash ?? undefined}>{compact(receipt.previous_receipt_hash)}</dd></div>
            <div><dt>Manifest</dt><dd title={receipt.scope.scope_manifest_hash}>{compact(receipt.scope.scope_manifest_hash)}</dd></div>
          </dl>
          <div className="receipt-counts" aria-label="Receipt action counts">
            <div><strong>{receipt.counts.actions_attempted}</strong><span>attempted</span></div>
            <div><strong>{receipt.counts.actions_succeeded}</strong><span>succeeded</span></div>
            <div><strong>{receipt.counts.actions_failed}</strong><span>failed</span></div>
            <div><strong>{receipt.counts.targets_verified}</strong><span>verified</span></div>
          </div>
          <div className="signature-strip">
            <span>Signature</span>
            <code title={receipt.signature}>{compact(receipt.signature, 25)}</code>
          </div>
          <fieldset className="checkpoint-fields">
            <legend>Trusted checkpoint</legend>
            <p>Enter values obtained independently. Receipt fields never prefill this checkpoint.</p>
            <label htmlFor="trusted-sequence">
              Expected sequence
              <input
                id="trusted-sequence"
                inputMode="numeric"
                min="1"
                step="1"
                type="number"
                value={checkpoint.expectedSequence}
                onChange={(event) => onCheckpointChange("expectedSequence", event.target.value)}
              />
            </label>
            <label htmlFor="trusted-final-head">
              Expected final head
              <input
                id="trusted-final-head"
                autoComplete="off"
                spellCheck="false"
                value={checkpoint.expectedHead}
                onChange={(event) => onCheckpointChange("expectedHead", event.target.value)}
              />
            </label>
            <label htmlFor="trusted-prior-head">
              Prior trusted head (empty for genesis)
              <input
                id="trusted-prior-head"
                autoComplete="off"
                spellCheck="false"
                value={checkpoint.priorTrustedHead}
                onChange={(event) => onCheckpointChange("priorTrustedHead", event.target.value)}
              />
            </label>
          </fieldset>
          <div className="verify-row">
            <button className="button button--ink" type="button" onClick={onVerify} disabled={verifying || !checkpointReady}>
              {verifying ? "Verifying…" : "Verify signature + chain"}
            </button>
            {verification && (
              <output className={`verify-result verify-result--${verification.valid ? "valid" : "invalid"}`}>
                <span aria-hidden="true">{verification.valid ? "✓" : "×"}</span>
                {verification.valid ? "Signature and supplied chain head verify" : verification.reason_codes?.[0]?.replaceAll("_", " ") ?? "Verification failed"}
              </output>
            )}
          </div>
        </div>
      )}
    </section>
  );
}
