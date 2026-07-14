# {{PRODUCT_NAME}} — Narrated Demo Walkthrough

A spoken-word script for the eight-stage deterministic demo. Read the narration aloud as written; it is calibrated to the honest-evidence framing in `docs/gtm/one-pager.md`. Total running time is about 8 minutes.

Every number in this script comes from the real deterministic run recorded in `.lethe/reports/demo-report.json`. Do not improvise figures.

---

## Setup checklist (before anyone is watching)

- [ ] `make bootstrap` has been run once on this machine.
- [ ] `make dev` is running: API at `http://127.0.0.1:8000`, dashboard at `http://127.0.0.1:5173`.
- [ ] Dashboard open in a browser, terminal beside it. Both visible on screen at once.
- [ ] Do one full `make demo` rehearsal beforehand so `.lethe/` state is known-good, then `uv run lethe-control reset` to start clean.
- [ ] Decide your mode:
  - **One-shot:** `make demo` runs all eight stages and writes `.lethe/reports/demo-report.json`. Good for a fallback, bad for narration pacing.
  - **Granular (recommended for live demos):** the per-stage `uv run lethe-control ...` commands below. You control the tempo.
- [ ] Keep a scratch note ready: stage 4 prints a `run_id` you will paste into stages 6 and 8.
- [ ] Budget: ~30 seconds of setup talk, then roughly one minute per stage.

**Recording note:** capture at a resolution where terminal JSON is legible. The two money shots are stage 3 (unsafe answer) and stage 8 (receipt verification) — plan your zooms there.

---

## Opening (15 seconds, before stage 1)

> "Every AI stack copies meaning, not just records. A document becomes chunks, embeddings, caches, summaries, agent memories. Delete the document, and none of those descendants reliably follow. I'm going to show you that failure live, and then show you what a control plane for it looks like. Everything you'll see is a closed-world local demo — real mechanics, honestly scoped."

---

## Stage 1 — Ingest

**Command:**

```sh
uv run lethe-control reset
uv run lethe-control seed
```

**Show:** Terminal for the commands, then the dashboard **workflow rail** at the top — steps 01 Ingest and 02 Derive light up.

**Narration:**

> "First I seed a closed world the control plane fully owns: five declared stores — object storage, a vector database, a cache, summaries, and agent memories. Into it goes one sensitive source document containing a canary fact: a made-up secret phrase, amber-lantern-731. If that phrase ever comes out of the system after we revoke it, we'll know instantly that deletion failed."

**The fact to notice:** the seeded target is `ver_demo_canary_001` — one source that will fan out into **15 tracked objects across 5 stores**.

---

## Stage 2 — Derive and answer

**Command:** none — `seed` already built the derivation graph. Drive this stage from the dashboard.

**Show:** The **declared-descendants graph** (the "Declared descendants" lineage map — hit "Refresh graph"), then the **disclosure test panel** ("Ask once. Compare paths."). Ask the fixed question and click **Run through gate**.

**Narration:**

> "The source has already been processed the way any RAG pipeline would: parsed into chunks, embedded, cached, summarized, written into agent memory. The difference is that here, every one of those steps was recorded as immutable lineage — you're looking at the actual derivation graph. Now I ask the system the canary question through the governed path, and it answers correctly, citing its supporting context."

**The fact to notice:** the answer says **"amber-lantern-731"** — and the supporting version is a **chunk**, not the source document. The answer is already living in a derivative.

**Recording note:** pause 2 seconds on the graph so viewers register source → chunk → embedding → cache → summary → memory before moving on.

---

## Stage 3 — The unsafe baseline (the gut-punch)

**Command:**

```sh
uv run lethe-control baseline
```

**Show:** Terminal output first, then the **disclosure test panel** — the unsafe-path result card.

**Narration:**

> "Now, the way deletion works in most stacks today: I delete the source document. Only the source. That's what 'we deleted it' usually means. Watch — `source_payload_removed: true`. The file is genuinely gone. And now I ask the same question again, bypassing any gate, the way an ordinary retrieval pipeline would."
>
> *(point at the answer)*
>
> "There it is. Amber-lantern-731. The document is deleted and the system just told me its secret anyway, straight out of a surviving chunk. This is ghost knowledge — not a hypothetical, it's live on this screen. Nothing in this pipeline even knows those descendants exist."

**The fact to notice:** `source_payload_removed: true` **and** the answer still contains **"amber-lantern-731"** in the same JSON payload. Two lines apart.

**Recording note:** this is money shot #1. Zoom on the terminal so `"source_payload_removed": true` and the leaked phrase are both in frame. Hold for a full 3 seconds of silence before the next line.

---

## Stage 4 — Register the event, deny fence goes up

**Command:**

```sh
uv run lethe-control event-only delete
```

Copy the `run_id` from the output — you need it in stages 6 and 8.

**Show:** The **dispatch panel** ("Dispatch signed lifecycle event") — the fence state flips to **DENY FENCE ACTIVE**. Then back to the **disclosure test panel**: run the gated query again.

**Narration:**

> "Now the control plane's answer to that. I register a signed deletion event for the source. Before any cleanup happens — before a single byte is repaired — the retrieval gate flips to deny. Ask the same question through the governed path now: no answer, zero supporting context, and two machine-readable reasons — lifecycle_denied and tombstoned. Logical denial comes first; slower physical repair follows. The window where stage 3 was possible just closed."

**The fact to notice:** the gated response is `denied: true` with reason codes **`lifecycle_denied`** and **`tombstoned`**, and the event lands with `gate_state: "denied"` immediately — before propagation runs.

**Recording note:** zoom briefly on the two reason codes. They are the contrast to stage 3's leak.

---

## Stage 5 — Scan the declared stores

**Command:**

```sh
uv run lethe-control scan --root-version-id ver_demo_canary_001
```

**Show:** The **scanner findings** — click "Scan declared stores" and show the run inspector's finding classes: tracked, exact untracked, semantic candidate.

**Narration:**

> "Next question: where does this fact actually live? The scanner works in three layers. Tracked lineage finds all fifteen objects the graph already knows about — the denominator is fifteen, and it finds fifteen. Then exact tenant-keyed fingerprints catch two copies that existed outside the graph. And a bounded semantic pass flags two more look-alike candidates at 0.95 confidence. Those semantic matches are findings for a human to review — they are never automatic deletion authority, because a similarity score is not a mandate to destroy data."

**The fact to notice:** denominator **15, found 15** — plus **4 scanner candidates beyond the graph** (2 exact fingerprint copies, 2 semantic candidates), reported but handled differently on purpose.

---

## Stage 6 — Propagate the repair

**Command:**

```sh
uv run lethe-control propagate --run-id RUN_ID
```

(`RUN_ID` is the value you copied in stage 4.)

**Show:** Terminal counts, and the run inspector in the dashboard for the action states.

**Narration:**

> "Now the physical repair, store by store: deletes across the object store, the vector database, summaries, and agent memories, plus a cache eviction — each one a store-appropriate action, not one generic 'delete' pretending every backend works the same. Fifteen actions attempted, fifteen succeeded, zero failed, zero dead-lettered. And every action was verified by reading the store back afterward — payload absent — not by trusting the store's return code."

**The fact to notice:** **15 of 15 actions succeeded** — and each has an independent read-back verification (`payload_absent`) behind it.

---

## Stage 7 — Resurrection attacks

**Command:**

```sh
uv run lethe-control probe delete
```

**Show:** Terminal output — the four attack results and the two fixed query suites. Optionally re-run the gated query in the **disclosure test panel** to show it still denies.

**Narration:**

> "Deleting once is easy. Staying deleted is the hard part, so we attack our own system. Someone re-ingests the same document — it gets quarantined. A stale cache tries to serve the old answer — the gate blocks it. A replayed job from before the deletion — rejected. A duplicate event — returns the original run instead of running twice. Then two fixed query suites: a hundred adversarial attempts to extract the fact — zero disclosures. And a hundred unrelated questions — zero false blocks. We're not just proving it stays gone; we're proving we didn't break everything else to get there."

**The fact to notice:** across **200 probe queries: 0 prohibited disclosures and 0 false blocks**. All four resurrection attacks held.

**Recording note:** pause on the `fixed_query_suites` block — the paired zeros are the point.

---

## Stage 8 — The signed receipt (the payoff)

**Command:**

```sh
uv run lethe-control receipt RUN_ID --output .lethe/reports/receipt.json
uv run lethe-control verify-receipt .lethe/reports/receipt.json \
  --expected-sequence 1 \
  --expected-head INDEPENDENTLY_RETAINED_HEAD
```

(The expected head comes from your independently retained checkpoint — in the deterministic demo it is printed when the receipt is chained. The verifier never derives trust inputs from the receipt itself.)

**Show:** The **receipt panel** in the dashboard: signature, chain verification result, coverage level, and — most importantly — the residual-risks section.

**Narration:**

> "Everything you just watched is now one artifact: an Ed25519-signed, hash-chained receipt. It records the scope — five connectors, a named graph snapshot — the fifteen actions, the nineteen verification checks, and the chain verifies as valid against a checkpoint we retained independently. Your auditors can check this offline without trusting us.
>
> "But here's the part I actually want you to look at: the residual risks. This receipt says, in machine-readable form, what we did *not* cover — trust in the host itself, the possibility of direct store bypass, and two unregistered copies outside our declared scope. The coverage level is L3: graph traversal plus connector read-back plus gate denial tests — and the receipt says L3, not 'everything.' Any vendor can hand you a certificate that says 'deletion complete.' A receipt that names its own gaps is the one you can actually defend in front of a regulator."

**The fact to notice:** `verification.valid: true` on the chain — sitting next to **three named residual risk codes** (`host_trust`, `direct_store_bypass`, `unregistered_copy` × 2) and coverage level **L3**. The report's top-level field literally reads `universal_deletion_claim: false`.

**Recording note:** money shot #2. Zoom on the residual-risks block, not the signature. The signature is table stakes; the named exclusions are the differentiator. Hold the frame through the last narration sentence.

---

## 30-second wrap

> "So, in eight minutes: we watched a standard pipeline leak a deleted secret — that's ghost knowledge, and it's in your stack today. Then the control plane put up a deny fence before cleanup, inventoried fifteen descendants across five stores, repaired all fifteen with read-back verification, survived four resurrection attacks and two hundred probe queries with zero leaks and zero false blocks, and signed a receipt for the whole thing. And the receipt tells you what it did not cover — because a completion claim without a boundary isn't evidence, it's marketing. That boundary is the product. That's what we'd like to map inside your environment, read-only, in ten business days or less."

---

## Q&A pointers

**"Why does the receipt name exclusions and residual risks? Isn't that admitting weakness?"**
Because a receipt that says "complete" with no scope is unverifiable — and unverifiable evidence is worthless in an audit. Named exclusions are what let a security team check the claim: covered stores are listed with capability versions, the graph snapshot is hashed, and what's outside the boundary is stated rather than implied. The honest boundary is what makes the rest of the receipt trustworthy.

**"Why are semantic matches report-only? Why not delete them automatically?"**
A 0.95 similarity score is a lead, not a legal basis. Auto-deleting on semantic similarity means an embedding model can silently destroy data it merely thinks looks similar — that's a new incident class, not a control. So tracked lineage and exact tenant-keyed fingerprints carry deletion authority; semantic candidates are surfaced as findings for human review.

**"What does L3 actually mean?"**
Coverage levels describe how a completion claim was verified. L3 means: transitive traversal of the declared derivation graph, plus connector read-back confirming each payload is absent, plus runtime gate denial tests. It does not include independent external attestation — that's L4, and a local checkpoint is deliberately labeled "not L4" in the report. The demo claims exactly what it can prove and labels the level, which is the whole philosophy in one field.

**"Does this delete the fact from model weights?"**
No, and the receipt will never say it does. No claims about model unlearning, physical media erasure, or stores the control plane isn't connected to. Scope is declared stores, a named snapshot, a stated verification method.

---

## Recording pause/zoom summary

| Moment | Action |
|---|---|
| Stage 2, lineage graph | Pause 2s on source → chunk → embedding → cache → summary → memory |
| Stage 3, baseline JSON | **Zoom**: `source_payload_removed: true` + leaked phrase in one frame; hold 3s |
| Stage 4, gated response | Zoom on `lifecycle_denied`, `tombstoned` |
| Stage 5, findings | Brief pause on the three finding classes |
| Stage 7, probe suites | Pause on 100/0 and 100/0 |
| Stage 8, receipt panel | **Zoom**: residual risks + `coverage_level: L3`; hold through final line |
