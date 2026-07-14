# {{PRODUCT_NAME}}

## Revocation control for derived AI data

**For:** Head of AI Platform

---

## When your source of truth changes, your AI keeps believing the old version.

Every RAG pipeline and agent stack copies meaning, not just records. One document becomes parsed chunks, then embeddings, then retrieval caches, then summaries, then agent memories written from earlier conversations. Delete or correct the source and none of those descendants reliably follow. The old claim stays retrievable, stays ranked, and keeps shaping answers. We call this **ghost knowledge**: information that remains influential after its governing source changed. Deleting the source doesn't kill the descendants — today, nothing in most AI stacks even knows where they all are.

## Five events that make this your problem

1. **A deletion right is exercised.** The document is gone; its chunks, vectors, and cached answers are not.
2. **A permission is revoked.** The doc system updates instantly; embeddings and caches keep serving the old ACL.
3. **A correction lands.** The superseded fact often stays ranked above its replacement.
4. **A license or consent expires.** The source is blocked, but derivatives built from it stay active.
5. **A poisoned or bad document is discovered.** You need every downstream trace found and fenced, fast.

## What {{PRODUCT_NAME}} does

{{PRODUCT_NAME}} is a control plane that turns a source lifecycle event into a bounded, verified revocation across the AI systems you declare to it:

- **Derivation graph.** Immutable version-to-version lineage: source → chunk → embedding → cache → summary → memory, including multi-parent derivatives.
- **Deny-first fence.** Invalid context is blocked at retrieval time immediately, before slower repair runs. Logical denial precedes physical cleanup.
- **Three-layer scan.** Tracked lineage, exact tenant-keyed fingerprints, and bounded semantic candidates. Semantic matches are findings for review, never automatic deletion authority.
- **Repair and rebuild.** Store-specific delete, tombstone, suppress, ACL update, cache eviction, and rebuild of mixed-parent derivatives from surviving valid parents — across declared, connected stores.
- **Resurrection probes.** Tests whether revoked knowledge returns through re-ingestion, cache repopulation, stale jobs, or paraphrase queries.
- **Signed evidence.** Every run emits an Ed25519-signed, hash-chained receipt stating scope, actions, verification method, failures, exclusions, unsupported systems, and residual risk — with an explicit coverage level (v0.1 targets L3: transitive graph traversal plus connector read-back and gate denial tests).

**What we will not tell you:** that everything is deleted. {{PRODUCT_NAME}} does not claim universal erasure, cannot remove knowledge from closed model weights, and cannot control stores it isn't connected to. Every receipt names what was covered, what was excluded, and what risk remains. If a vendor hands you a receipt that says "complete" with no scope, that receipt is the risk.

## Measured results (v0.1 reference demo)

Measured on the deterministic localhost reference environment — a closed-world proof of mechanics, not production claims:

| Metric | Result |
|---|---|
| Runtime gate overhead, p95 | 3.35 ms (target ≤ 20 ms) |
| Scanner precision / recall on seeded inventory | 1.0 / 1.0 |
| Propagation across 10,000 derivatives | 16.5 s |
| Prohibited disclosures across 100 adversarial probes | 0 |
| False blocks across 100 unrelated queries | 0 |

Receipts are verifiable offline with an independently runnable verifier — your security and audit teams check the evidence without trusting us.

## How you start

**Land: read-only Revocation Posture Assessment.** We connect read-only to your declared stores and show you every place your AI still believes the old version — a lineage map, a ghost-knowledge inventory, and a scoped report. No mutations, no content leaving your environment.

**Expand, at your pace:** continuous read-only monitoring → approval-gated propagation (every mutation reviewed before execution) → runtime enforcement at the retrieval gate.

## What we ask of a design partner

- Read-only access to **one document store and one vector store** you already operate.
- A working session to declare scope and confirm connector fit.
- **Ten business days or less**, elapsed. You keep the posture report and lineage map either way.

---

*All completion claims are scoped to declared stores, a named graph snapshot, and a stated verification method. That boundary is the product.*
