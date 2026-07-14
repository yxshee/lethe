# Lethe

Lethe is an internal codename for a localhost, closed-world proof of derived-data lifecycle assurance. It demonstrates bounded revocation across demo-owned source files, chunks, embeddings, caches, summaries, and agent memories.

It does **not** prove universal deletion, physical media erasure, model unlearning, or coverage outside its declared stores. See `SPEC.md` for product boundaries and evidence semantics.

The implementation follows `v0.1-build-plan.md`. Python owns lifecycle state, the authoritative gate, worker, local objects, Chroma, SQLite, probes, and signed receipts. The React dashboard only receives content-free control metadata.

## Requirements

- Python 3.12 and `uv`
- Node.js 24 and pnpm 10

## Start

```sh
make bootstrap
make dev
```

API: `http://127.0.0.1:8000`  
Dashboard: `http://127.0.0.1:5173`

Run the deterministic proof:

```sh
make demo
```

Run checks:

```sh
make verify
make benchmark
```

Runtime data and reports live under ignored `.lethe/` paths. Demo credentials and signing keys are fixtures, never production secrets.

## Demo stages

`make demo` resets the closed world and runs all eight stages: ingest, derive, unsafe source-only deletion baseline, deny fence, scan, propagation, resurrection attacks, and locally checkpointed receipt verification.

The same flow is available as granular commands:

```sh
uv run lethe-control reset
uv run lethe-control seed
uv run lethe-control baseline
uv run lethe-control event-only delete
uv run lethe-control scan --root-version-id ver_demo_canary_001
uv run lethe-control propagate --run-id RUN_ID
uv run lethe-control probe delete
uv run lethe-control receipt RUN_ID --output .lethe/reports/receipt.json
uv run lethe-control verify-receipt .lethe/reports/receipt.json \
  --expected-sequence 1 \
  --expected-head INDEPENDENTLY_RETAINED_HEAD
```

For non-genesis bundles, also pass `--prior-trusted-head`. The expected sequence and heads must come from an independently retained checkpoint; the verifier never derives trust inputs from the submitted receipt. Local checkpoints remain L3 evidence plus resilience probes, never L4.

Separate deterministic lifecycle demos are available as:

```sh
./scripts/demo-correction.sh
./scripts/demo-expiry.sh
./scripts/demo-permission-change.sh
```

## Fixture authentication

The dashboard keeps fixture tokens in memory only:

- operator: `lethe-control-demo`
- alice: `lethe-alice-demo`
- bob: `lethe-bob-demo`
- unsafe baseline: `lethe-unsafe-demo`

Event fixtures are additionally signed with the demo issuer key and sent in `X-Lethe-Event-Signature`. These values are deliberately non-production.

## Storage and evidence

- `.lethe/state.db`: WAL-mode lineage, policies, events, approvals, outbox, actions, findings, tombstones, and receipt chain.
- `.lethe/objects/`: application-addressed source and chunk payloads.
- `.lethe/chroma/`: scope-derived physical vector IDs and defense-in-depth metadata.
- `.lethe/reports/`: machine-readable demo and benchmark reports.

Receipts prove which enrolled local agent signed a scoped record and whether its ordered record is intact. They name denominators, actions, read-back, exclusions, candidates, and residual risks; they do not prove that unknown copies do not exist.

## Verification

`make verify` checks OpenAPI drift, formatting, linting, Python typing, backend tests, frontend tests/build, and Chromium Playwright. `make benchmark` records scanner quality, 10,000 warmed gate checks, 100 prohibited and 100 unrelated probes, and 10,000-derivative propagation on the hardware declared in `fixtures/manifest.json`.
