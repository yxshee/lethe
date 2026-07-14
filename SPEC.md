# Lethe

## Full Product Specification

**Status:** Draft for product review  
**Date:** 2026-07-14  
**Codename:** Lethe, internal use only; prohibited for external interviews or launch  
**Business model:** Closed commercial product  
**Initial buyer:** Head of AI Platform  
**Build gate:** No implementation begins until this specification is reviewed and approved

---

## 1. Executive summary

AI applications routinely turn one source into many operational copies: parsed documents, chunks, embeddings, retrieval caches, summaries, agent memories, and downstream prompts. Source systems can delete, correct, expire, or restrict the original, but those changes do not reliably propagate across every derivative. The result is **ghost knowledge**: information that remains retrievable or influential after its governing source changed.

Lethe is a derived-data revocation control plane. It gives AI platform teams a consistent way to:

1. attach lifecycle and policy metadata to knowledge objects;
2. record how derived objects were produced;
3. translate lifecycle events into store-specific repair actions;
4. block invalid context immediately while slower repair runs;
5. report bounded, signed evidence of what was covered and observed; and
6. test whether deleted or superseded knowledge can reappear.

Lethe is not a universal erasure oracle. It cannot prove that information no longer exists anywhere, cannot recall knowledge already incorporated into a closed model's weights, and cannot control stores it cannot discover or access. Its product promise is narrower:

> Lethe coordinates and verifies lifecycle enforcement across declared, instrumented RAG and agent systems, while exposing failures, exclusions, and residual risk.

The initial product is a deterministic localhost demonstration called the **Ghost Knowledge Scanner**. It owns the full demo chain—local documents, chunks, Chroma embeddings, caches, summaries, and simulated memories—so its behavior is reproducible. This proves mechanics, not market demand or brownfield coverage. Commercial validation requires customer-owned systems.

---

## 2. Thesis and problem

### 2.1 Ghost knowledge

The working market hypothesis is narrower than “no one solves deletion”: in heterogeneous customer-built AI stacks, one authenticated source lifecycle event often fails to reach two or more derivative systems outside the source platform's control. An operator may remove a document yet leave behind:

- chunks in an ingestion database;
- vector records keyed by an obsolete source identifier;
- answer or retrieval caches;
- summaries combining multiple parents;
- agent memories written from earlier conversations;
- copied material in another team's pipeline;
- restored data from a backup or re-ingestion job.

This is not only a deletion problem. A correction can leave the old claim ranked above the replacement. An expiry can block the source but not a cached answer. A permission change can update the document system while an embedding retains stale ACL metadata. A license or consent withdrawal can invalidate only some purposes or principals while broad derivatives remain active.

The core systems problem is that AI stacks copy **meaning**, while lifecycle tools usually address records.

### 2.2 “GC for meaning”

“Garbage collection for meaning” is a useful product metaphor:

- source lifecycle events behave like invalidation roots;
- lineage links identify reachable derivatives;
- the runtime gate acts as an immediate deny fence;
- propagation recipes collect, suppress, delete, or rebuild invalid descendants;
- resurrection tests look for stale objects returning.

The metaphor has limits. Conventional garbage collectors operate in a closed memory space with authoritative reachability. Lethe operates across heterogeneous systems, incomplete instrumentation, asynchronous APIs, backups, and probabilistic semantic matching. Therefore every completion claim is scoped to a declared connector set, graph snapshot, policy version, and verification method.

### 2.3 Five lifecycle triggers

The product model recognizes five triggers:

| Trigger | Meaning | v0.1 |
|---|---|---:|
| Delete | Source or version must no longer be available for use | Yes |
| Correct | Old version is superseded by a replacement | Yes |
| Expire | Validity ends at a defined time | Yes |
| Permission change | Allowed principals or groups narrow or widen | Yes |
| Consent/license withdrawal | A party, purpose, territory, or usage right is withdrawn | Roadmap |

Consent/license withdrawal is distinct because it can invalidate a purpose-specific use without requiring global deletion. v0.1 stores consent and license annotations but does not enforce this event type.

### 2.4 Customer job

For a Head of AI Platform operating multiple RAG or agent applications:

> When knowledge becomes invalid, help my team find affected derivatives, block unsafe retrieval immediately, repair supported stores, and produce credible evidence of what happened—without sending sensitive content to another SaaS system.

### 2.5 Desired outcomes

- Shorter incident and lifecycle-remediation time.
- Lower risk of stale or unauthorized RAG context.
- One cross-system lifecycle workflow instead of store-specific runbooks.
- Measurable coverage and explicit unknowns.
- Reusable evidence for security, privacy, internal audit, and customer assurance.
- Safe expansion from read-only scanning to approval-gated repair and runtime enforcement.

### 2.6 Non-goals

Lethe does not promise:

- universal discovery or proof that no copy exists;
- forensic erasure from storage media, WAL files, or backups;
- deletion from closed-model weights;
- automatic unlearning or model retraining in v0.1;
- legal conclusions about whether deletion, retention, consent, or license rules apply;
- safe semantic auto-deletion based only on similarity;
- control of unregistered, unreachable, or unsupported systems;
- replacement of source-system identity, policy, or retention authorities.

---

## 3. Product overview

### 3.1 Capability 1: lifecycle envelope

Every source and derivative receives a versioned **KnowledgeEnvelope**. It carries identity, provenance, lifecycle state, policy references, and optional governance annotations. The envelope travels with the object or remains resolvable through a stable local identifier.

The envelope solves two problems:

1. stores receive enough metadata to make scoped policy decisions; and
2. Lethe can connect an operational row or vector back to immutable lineage.

Envelope metadata is not itself proof that lineage is complete. Connectors report freshness, supported fields, and gaps.

### 3.2 Capability 2: derivation graph

The derivation graph records immutable version-to-version edges such as:

- source version → chunk;
- chunk → embedding;
- source/chunks → summary;
- retrieval result → answer cache;
- answer or source → agent memory.

Edges use immutable version identifiers. Multi-parent derivatives retain every known parent and every root version. Graph traversal is cycle-safe and bounded by configured depth, fanout, and object limits.

Instrumentation captures new edges at derivation time. The scanner supplements the graph with connector inventory, metadata matches, exact tenant-keyed fingerprints, and bounded semantic candidates. Semantic candidates are findings, not automatic deletion authority.

### 3.3 Capability 3: propagation and durable tombstones

Lethe converts a lifecycle event into an ordered propagation run:

1. persist and authenticate the event;
2. install a local deny fence;
3. snapshot the declared descendant set;
4. plan connector-specific actions;
5. execute serial or bounded-parallel mutations;
6. verify each target through read-back;
7. run bounded recovery and resurrection probes;
8. emit a signed receipt.

Actions include delete, tombstone, suppress, ACL metadata update, cache eviction, and rebuild from surviving valid parents.

A durable, payload-free tombstone contains only the minimum identity and policy material required to prevent reappearance: tenant/workspace/environment, opaque object and version identifiers, versioned purpose-separated tenant HMAC, source version, event/version ordering, reason code, and timestamps. The HMAC is still sensitive metadata. The tombstone contains no source text, derivative text, embedding, raw path, or principal list.

Exact lineage and exact tenant-keyed fingerprints may trigger automatic blocking. Similarity findings require review unless an adapter-specific policy explicitly promotes a tested matcher.

### 3.4 Capability 4: runtime context gate

The runtime gate is the authoritative retrieval control. Vector-store ACL metadata is a performance optimization and defense-in-depth layer, not the final authorization decision.

The gate evaluates:

- authenticated tenant/workspace;
- validated principal or workload identity;
- source and derivative lifecycle state;
- immutable version and supersession state;
- valid-from and valid-until;
- current policy reference and version;
- effective multi-parent ACL;
- purpose and applicable consent/license annotations where supported;
- tombstone and resurrection state;
- policy freshness.

The gate checks before context enters a prompt and again before an answer is released. The second check confirms that all contributing versions and authorization snapshots remain current; it is not a claim of semantic output inspection. In enforcement mode, denied, expired, superseded, unknown-lineage, or stale-policy objects fail closed. Scanner-only mode may observe without blocking, but its UI and receipts must never imply enforcement.

For a multi-parent derivative, every parent policy must independently allow the exact request context. Any explicit deny, missing parent, unresolved identity or group, stale decision, expiry, purpose mismatch, or invalid policy signature denies the derivative.

Protected applications must not hold credentials or network paths that bypass the gate. Cache keys bind tenant, workspace, environment, principal/workload, purpose, every contributing root/version, every policy version, and the authorization/group-membership snapshot. Cached retrievals and answers remain derivatives and pass the gate again on read.

### 3.5 Capability 5: scoped evidence receipts

Each propagation run produces a signed JSON receipt. The receipt identifies declared scope, graph snapshot, actions attempted, verification methods, failures, exclusions, unsupported sinks, outcome, and residual risk.

Receipts are **scoped execution evidence and signed agent assertions**. An Ed25519 signature proves possession of the private key corresponding to a trusted public key; key-enrollment records bind that key to a tenant, environment, and agent. The signature does not prove that the agent was uncompromised or that its assertions are true. A hash-linked chain makes certain edits, removals, and reorderings detectable only when a verifier has a previously trusted head and expected sequence. Neither mechanism proves that all descendants were discovered or that physical media was erased.

### 3.6 Capability 6: resurrection testing

Resurrection testing checks whether invalid information can return through:

- source re-ingestion;
- backup or fixture restoration;
- stale queued jobs;
- cache repopulation;
- an older allow event arriving late;
- exact-content copies with missing lineage;
- bounded paraphrase and recovery queries.

Tests report corpus, principals, model or deterministic generator, parameters, thresholds, observation window, false positives, and false negatives. Lethe may say that selected probes did not recover prohibited content within declared scope. It must not claim universal semantic erasure.

### 3.7 Evidence coverage levels

| Level | Required evidence | What it does not mean |
|---|---|---|
| L0 — Recorded | Event authenticated, persisted, policy assessed, scope declared | Descendants found or acted on |
| L1 — Direct | L0 plus known direct descendants enumerated and actions attempted | Transitive graph covered |
| L2 — Transitive | L1 plus registered transitive closure traversed and actions attempted | Adapter read-back performed |
| L3 — Agent-verified | L2 plus agent-observed connector read-back and runtime-gate denial tests | Independent verification, unknown stores, or backup copies covered |
| L4 — Resilience | L3 plus declared resurrection suite and externally retained signed chain checkpoint | Universal erasure or semantic impossibility |

Every level records a nonzero denominator from an immutable scope manifest: registered physical stores, connector instances and capability versions, derivative classes, reachable targets, graph snapshot, expected tracked descendants, discovered candidates, attempted actions, verified actions, failures, exclusions, unsupported systems, freshness, and scan cutoff. A zero or unknown denominator cannot produce succeeded.

v0.1 targets L3. It runs local resurrection tests as additional evidence, but cannot claim L4 because its checkpoint is not independently retained.

---

## 4. Lifecycle semantics

### 4.1 Policy invariants

1. **Deny wins.** A current denial cannot be overridden by an older allow, correction, or permission-expansion event.
2. **Logical denial precedes physical mutation.** Retrieval stops before asynchronous cleanup begins.
3. **Versions are immutable.** Corrections create new versions and new derivative branches.
4. **Permission narrowing is immediate.** Permission widening requires explicit re-evaluation; old derivatives are not silently broadened.
5. **Multi-parent policy is conjunctive.** Every parent policy must allow the exact request context; explicit deny, missing/unresolved parent policy, or purpose mismatch denies. Effective expiry is the earliest parent expiry.
6. **Legal hold changes storage action, not retrieval authorization.** A held object may be retained in quarantine while remaining unavailable to RAG applications.
7. **Unknown is not success.** An inaccessible or unsupported declared target forces a partial or unknown outcome.
8. **Semantic similarity is not mutation authority in v0.1.**

### 4.2 Event behavior

#### Delete

- Install deny fence for target version and descendants.
- Enumerate tracked descendants and exact-fingerprint matches.
- Delete supported payloads or quarantine them when retention policy blocks physical deletion.
- Retain payload-free tombstones.
- Evict affected caches.
- Verify absence from declared active stores and denial at gate.

#### Correct

- Deny and tombstone old version branch.
- Create replacement source version linked by supersession.
- Derive new chunks, embeddings, summaries, caches, and memories from replacement.
- Rebuild mixed-parent derivatives from surviving valid parents; never edit an old semantic payload in place.
- Verify old fact is denied and replacement fact is available to authorized principals.

#### Expire

- At valid-until, install deny fence without waiting for cleanup worker.
- Apply configured retention action asynchronously.
- Verify deterministic before/after behavior using an injectable clock.
- Older clock or event messages cannot reactivate expired versions.

#### Permission change

- Authenticate new source policy version.
- For narrowing, block removed principals immediately.
- Recompute derivative ACL as parent-policy intersection.
- Update Chroma metadata filters and evict principal/policy-bound caches.
- Preserve access for principals who remain authorized.
- For widening, require explicit event and re-evaluate or re-derive before access expands.
- Resolve dynamic group membership at gate time in production; v0.1 uses fixed principals.

#### Consent/license withdrawal

Roadmap behavior:

- evaluate party, purpose, territory, action, and time constraints;
- deny affected uses immediately;
- propagate purpose-scoped repair;
- retain or rebuild derivatives still valid for other purposes;
- report legal or policy conflicts rather than adjudicating them.

### 4.3 Delivery and ordering

Production delivery is at-least-once. Idempotency keys are unique within tenant, environment, issuing authority, and target stream. Each authority/connector/target-version stream owns a durable source-sequence high-water mark; policy versions advance independently inside the referenced policy. A newer denial dominates any older allow. An authorized resync must name the old and new sequence epochs and cannot clear a known denial without an explicit, authorized lifecycle transition.

Command-envelope expiry is distinct from lifecycle effective time. An expired transport command is rejected and audited, then connector reconciliation requests a fresh assertion. Rejection never erases a deletion or expiry already recorded locally. Expiry in the knowledge envelope is enforced at query time using a rollback-detecting clock; a durable scheduler creates the cleanup run at valid-until.

A run remains safe under failure:

- deny fence persists;
- retries reuse the same event and stable action identifiers;
- successful actions are not repeated destructively;
- unresolved targets remain visible;
- no success receipt is issued while required verification is missing.

---

## 5. Product architecture

### 5.1 Target commercial topology

The commercial product is hybrid.

**Customer data plane**

- outbound-only Lethe agent;
- source connectors and lifecycle watchers;
- sink adapters;
- local derivation graph and tombstone state;
- scanner and propagation engine;
- runtime context gate;
- local connector, model, and identity credentials;
- receipt signing key.

**Lethe SaaS control plane**

- tenant and environment registry;
- policy configuration and versions;
- signed declarative event queue;
- run status and connector health;
- receipt ledger and signed chain checkpoints;
- dashboard and auditor export;
- billing and support controls.

The data-plane agent initiates outbound mTLS; the SaaS control plane never initiates an inbound customer-network connection. Customer applications may call a customer-local gate interface over loopback, Unix socket, or an explicitly protected internal service endpoint. The SaaS service may send signed declarative lifecycle events containing registered connector/object scope, policy version, sequence, command TTL, and idempotency key. It may not send shell commands, arbitrary SQL, arbitrary URLs, or connector-specific executable arguments.

Transport authentication is not event authorization. Tenant, workspace, and environment come from the authenticated enrollment/channel and are cross-checked against—not trusted from—the payload. The agent binds issuer key, audience, authority, actor, connector scope, permitted event types, target namespace, sequence, nonce, and freshness to a local authorization policy. Denied events are retained in a payload-free audit record. Source connectors and tenant administrators receive separate authorities.

The agent independently validates customer-local policy. Immediate logical denial is automatic. Physical mutation requires either a pre-authorized bounded policy or an immutable approval over the exact action-plan hash. A volume threshold alone never grants authority.

### 5.2 Data minimization

By default, SaaS receives only:

- opaque tenant, environment, agent, event, run, object, and connector identifiers;
- object kinds and lifecycle states;
- counts, timestamps, versions, and coarse health status;
- policy and evidence hashes;
- signed receipts and checkpoints;
- exclusions and machine-readable reason codes.

SaaS does not receive:

- source or derivative content;
- chunks or embeddings;
- prompts or generated answers;
- raw source paths or URLs;
- connector responses;
- source-system credentials;
- ACL member identities;
- unsalted content hashes.

Human-readable labels are opt-in. Customer-side exports use opaque random identifiers or tenant-keyed HMAC fingerprints to reduce dictionary attacks against known content.

Receipt action, verification, exclusion, and residual-risk records use closed schemas containing opaque references, enums, counts, timestamps, and reason codes. Arbitrary free text, paths, principals, and connector responses remain local unless a customer explicitly opts in. An outbound allowlist validator runs before signing or upload.

Production policy defines separate retention, residency, deletion, backup, and legal-hold periods for operational telemetry, receipts, checkpoints, key history, tombstones, and support bundles. Tenant deletion uses crypto-erasure where possible and names any retained audit exception.

### 5.3 Production tenancy

Production SaaS uses shared logical tenancy with:

- tenant context derived from authenticated identity, never payload alone;
- database row-level security;
- tenant-scoped queues, object paths, cache keys, and rate limits;
- per-tenant envelope encryption;
- no cross-tenant content deduplication;
- agent enrollment bound to certificate, tenant, environment, and connector scope;
- just-in-time, approved, time-limited, audited support access.

Every object, edge, policy, cache, receipt, and query uses a composite scope key: tenant_id, workspace_id, environment_id, and local identifier. Parent and root edges must remain in the same composite scope; cross-scope edges are rejected before commit. Every query is scope-qualified.

v0.1 runs one active workspace, but every contract contains mandatory scope context and the acceptance suite uses colliding identifiers in a second inert scope to test edge, cache, and receipt isolation.

### 5.4 Components

#### Source connectors

Observe authenticated delete, correction, expiry, permission, and later consent/license events. A revocation endpoint is a registered connector reference, not an arbitrary fetchable URL.

#### Lineage capture

Instruments derivation calls and writes immutable edges before publishing a derivative. A derivative is not queryable until its envelope and graph edge commit.

#### Scanner

Inventories registered stores, compares recorded graph to live state, finds missing metadata, and emits exact or semantic candidates. It reports scanner confidence and source method.

#### Propagation engine

Builds an action plan from policy, graph snapshot, connector capability, and approval policy. It prioritizes deny-fence creation, bounds traversal, applies backpressure, and records every attempt. Mutation execution verifies that the approved plan hash and policy/legal-hold snapshots still match immediately before action.

#### Runtime gate

Performs local, synchronous policy checks using cached policy and tombstone state. No SaaS round trip is allowed on the query path.

#### Receipt signer and local ledger

Canonicalizes receipt bodies, signs them with the customer agent key, and advances a tenant-environment-agent hash chain. Signing privilege is isolated from mutation credentials where practical.

#### SaaS policy service and ledger

Stores metadata-only policy state and signed receipts. It signs periodic inclusion checkpoints using a separate role-specific key. Production append-only claims require immutable/WORM controls; otherwise the product says tamper-evident.

#### TypeScript dashboard

Shows graph coverage, events, propagation status, failures, exclusions, evidence level, receipt verification, and residual risk. Destructive actions require explicit approval context.

### 5.5 Interoperability posture

Lethe maps its vocabulary to existing standards rather than inventing a universal policy language:

- Knowledge objects and versions map to W3C PROV Entities.
- Derivation activity maps to PROV Activities.
- Actors, connectors, and agents map to PROV Agents.
- Parent edges map to wasDerivedFrom and generation/usage relationships.
- Purpose, permission, prohibition, constraint, and party concepts align with the W3C ODRL Information Model.

W3C PROV is designed for interoperable provenance across heterogeneous contexts, and ODRL provides a model for permissions, prohibitions, duties, parties, assets, and constraints. v0.1 uses vocabulary mapping only; it does not serialize RDF/OWL or implement a general ODRL reasoner. See [PROV-O](https://www.w3.org/TR/prov-o/) and [ODRL Information Model 2.2](https://www.w3.org/TR/odrl-model/).

### 5.6 Incumbent coexistence

Lethe is designed to complement, not replace, source platforms, catalogs, DSPM/privacy systems, and workflow tools. Integration contracts can:

- ingest authenticated lifecycle events, impacted-asset sets, or policy findings from an incumbent;
- resolve them to customer-local derivative scope;
- execute local deny and repair through Lethe adapters; and
- return status, scoped receipts, and failures to the incumbent workflow or audit surface.

BigID, OneTrust, Collibra, or Glean integration is at least equal roadmap priority to adding another raw source connector. Product validation must determine whether customers want an independent control plane, an execution plug-in, or both.

---

## 6. Data contracts

All timestamps are RFC 3339 UTC strings. All identifiers are opaque strings. Every mutable policy or source concept has an immutable version identifier. Bare identifiers are never globally authoritative; persistence keys and lookups include tenant, workspace, and environment.

### 6.1 KnowledgeEnvelope

Required fields:

    {
      "schema_version": "1",
      "tenant_id": "ten_demo",
      "workspace_id": "ws_demo",
      "environment_id": "env_local",
      "object_id": "obj_policy_handbook",
      "version_id": "ver_01",
      "kind": "source|chunk|embedding|cache|summary|memory",
      "parent_version_ids": [],
      "root_version_ids": ["ver_01"],
      "lifecycle_state": "active|denied|tombstoned",
      "created_at": "2026-07-14T09:00:00Z",
      "valid_from": "2026-07-14T09:00:00Z",
      "valid_until": null,
      "policy_ref": "policy://demo/default",
      "policy_version": 1,
      "acl_ref": "acl://demo/policy-handbook/1",
      "supersedes_version_id": null,
      "content_ref": "local://objects/ver_01",
      "local_content_fingerprint": "hmac-sha256-key-version-and-base64url",
      "provenance": {
        "activity_type": "ingest",
        "connector_ref": "connector://local-files",
        "run_id": "run_ingest_01"
      },
      "governance": {
        "owner_ref": null,
        "purpose_refs": [],
        "jurisdictions": [],
        "consent_ref": null,
        "license_ref": null,
        "retention_ref": null,
        "revocation_connector_ref": null,
        "residual_risk_class": "standard"
      }
    }

Rules:

- parent version identifiers never change;
- root version identifiers contain every known source root;
- object, parent, and root keys are scoped by tenant, workspace, and environment, with same-scope foreign-key enforcement;
- local content references never leave the data plane by default;
- fingerprints are HMAC-SHA-256 over LETHE-FINGERPRINT-V1, a zero byte, object kind, a zero byte, then canonical payload using a versioned, purpose-separated tenant key; comparison requires matching kind and fingerprint-key version;
- canonical text payload is the exact post-ingestion UTF-8 byte sequence with LF line endings and no additional Unicode normalization; structured cache payload uses RFC 8785 JSON; embedding payload uses model/version, dimensions, and canonical little-endian float32 bytes;
- old fingerprint keys remain locally available only for configured tombstone matching during rotation;
- missing lineage or policy version causes enforcement-mode denial;
- Chroma rows carry workspace, version, roots, policy version, lifecycle state, and ACL reference as metadata.

### 6.2 LifecycleEvent

    {
      "schema_version": "1",
      "event_id": "evt_01",
      "idempotency_key": "customer-stable-key",
      "tenant_id": "ten_demo",
      "workspace_id": "ws_demo",
      "environment_id": "env_local",
      "issuer_key_id": "issuer_demo_01",
      "audience": "agent_demo_01",
      "authority_ref": "authority://demo/source-admin",
      "nonce": "unique-command-nonce",
      "target_version_id": "ver_01",
      "event_type": "delete|correct|expire|permission_change",
      "source_sequence": 42,
      "policy_version": 2,
      "occurred_at": "2026-07-14T10:00:00Z",
      "effective_at": "2026-07-14T10:00:00Z",
      "command_expires_at": "2026-07-14T10:05:00Z",
      "actor_ref": "actor://demo/admin",
      "reason_code": "source_deleted",
      "correction": null,
      "permission_change": null
    }

Correction payload:

    {
      "replacement_version_id": "ver_02",
      "replacement_content_ref": "local-object://registered/ver_02",
      "replacement_fingerprint": "hmac-sha256-key-version-and-base64url"
    }

Permission payload:

    {
      "change": "narrow|widen",
      "new_acl_ref": "acl://demo/policy-handbook/2",
      "new_policy_version": 2
    }

Raw corrected content and ACL member identities remain local. SaaS receives opaque references and counts only.

Event rules:

- authenticated channel enrollment supplies tenant, workspace, environment, and accepted issuer binding;
- local authority policy determines permitted event types, connector namespaces, target scopes, and whether the actor may request permission widening;
- replacement_content_ref is an opaque pre-registered local object reference, never a caller-controlled path or URL;
- replacement version, scope, source authority, registered namespace, and fingerprint must match before derivation;
- rejected, expired, replayed, unauthorized, and stale events produce payload-free audit records;
- source_sequence belongs to the issuing-authority/connector/target stream; policy_version belongs to the referenced policy and is not a substitute for source ordering.

### 6.3 ExecutionReceipt

    {
      "receipt_version": "1",
      "tenant_id": "ten_demo",
      "workspace_id": "ws_demo",
      "environment_id": "env_local",
      "agent_id": "agent_demo_01",
      "key_id": "key_demo_01",
      "chain_sequence": 7,
      "previous_receipt_hash": "sha256-base64url",
      "event_id": "evt_01",
      "run_id": "run_01",
      "source_version_ids": ["ver_01"],
      "policy_version": 2,
      "scope": {
        "scope_manifest_hash": "sha256-base64url",
        "scope_selected_by": "authority://demo/data-owner",
        "requested_evidence_level": "L3",
        "scan_cutoff": "2026-07-14T10:00:00Z",
        "registered_stores": 5,
        "registered_connectors": 5,
        "reachable_connectors": 5,
        "connector_capability_versions": [],
        "unsupported_connectors": [],
        "graph_snapshot_hash": "sha256-base64url"
      },
      "coverage_level": "L3",
      "counts": {
        "expected_tracked": 12,
        "found_tracked": 12,
        "scanner_candidates": 2,
        "actions_attempted": 14,
        "actions_succeeded": 14,
        "actions_failed": 0,
        "targets_verified": 14
      },
      "actions": [],
      "verification_results": [],
      "exclusions": [],
      "unsupported_sinks": [],
      "residual_risks": [],
      "outcome": "succeeded|partial|failed|unknown",
      "started_at": "2026-07-14T10:00:00Z",
      "completed_at": "2026-07-14T10:00:03Z",
      "evidence_manifest_hash": "sha256-base64url",
      "signature_algorithm": "Ed25519",
      "signature": "base64url"
    }

Receipt rules:

- canonicalization follows [RFC 8785 JSON Canonicalization Scheme](https://www.rfc-editor.org/rfc/rfc8785.html);
- signing follows Ed25519 as specified by [RFC 8032](https://www.rfc-editor.org/rfc/rfc8032.html);
- signature input is domain-separated as LETHE-EXECUTION-RECEIPT-V1, a zero byte, then canonical receipt JSON without the signature field;
- entry hash is SHA-256 over a separate domain prefix and the canonical signed receipt;
- chain is per tenant, environment, and agent, avoiding one global concurrent chain;
- key enrollment binds public key, validity interval, tenant, workspace/environment scope, agent, and allowed signing role;
- verifier requires a trusted enrollment record, public key/status history, expected sequence, and previously retained trusted chain head or signed checkpoint;
- a caller-supplied head alone cannot detect suffix truncation;
- checkpoint records contain tenant, environment, agent, sequence, head hash, key ID, signing time, prior checkpoint hash, and signature; resets/forks are explicit signed records rather than silent rewrites;
- scope manifest is selected by an authorized customer role, hashed before execution, and records every store, connector instance/capability version, derivative class, freshness cursor, denominator source, exclusion, and scan cutoff;
- zero or unknown expected denominator cannot produce succeeded;
- actions, verification results, exclusions, unsupported sinks, and residual risks follow closed child schemas with opaque target/connector references, enum action/result/reason codes, counts, and timestamps;
- an outbound allowlist validator rejects unapproved fields before upload;
- succeeded requires every declared in-scope target to meet requested evidence level;
- known adapter or verification failures produce partial;
- inability to establish declared scope produces unknown;
- failure to install deny state or execute the core run produces failed;
- key loss, rotation, compromise, or chain reset creates an explicit discontinuity record; production key lifecycle is post-v0.1.

### 6.4 MutationApproval

Physical mutation approval is immutable and signed:

    {
      "approval_id": "approval_01",
      "tenant_id": "ten_demo",
      "workspace_id": "ws_demo",
      "environment_id": "env_local",
      "action_plan_hash": "sha256-base64url",
      "connector_refs": ["connector://chroma"],
      "target_version_refs": ["ver_01"],
      "maximum_action_count": 100,
      "requester_ref": "actor://demo/operator",
      "approver_ref": "actor://demo/data-owner",
      "approver_authority": "mutation_approve",
      "policy_snapshot_hash": "sha256-base64url",
      "legal_hold_snapshot_hash": "sha256-base64url",
      "nonce": "unique-approval-nonce",
      "expires_at": "2026-07-14T11:00:00Z",
      "signature": "base64url"
    }

Requester, approver, and executor are separate roles in production. Approval authorizes only the named physical plan; it cannot widen policy, clear a deny fence, or survive a changed plan/policy snapshot.

### 6.5 ACL policy record

An ACL reference resolves locally to an immutable policy version containing allowed and denied principals/groups, permitted purposes/actions, validity, source authority, and group-resolution snapshot rules. Evaluation accepts only when every contributing parent policy independently allows the same authenticated request context. Dynamic membership failure denies. v0.1 uses signed fixture policy records for alice and bob; production maps validated identity-provider claims.

---

## 7. Security and failure model

### 7.1 Trust boundaries

| Boundary | Allowed | Forbidden by default |
|---|---|---|
| Source → agent | Authenticated and locally authorized lifecycle events and connector reads | Payload-asserted identity, unregistered authority, or out-of-scope target |
| SaaS → agent | Signed declarative events for registered scope | Commands, code, SQL, arbitrary URLs |
| Agent → SaaS | Metadata-only status, signed receipts | Content, embeddings, credentials, ACL identities |
| App → gate | Validated OIDC/workload identity and query | Caller-supplied trusted identity headers |
| Gate → stores | Policy-filtered retrieval through registered adapters | Direct application credentials bypassing gate |
| Auditor → ledger | Read-only receipt and checkpoint export | Connector secrets or source content |

### 7.2 Principal threats

| Threat | Primary controls | Residual risk |
|---|---|---|
| Compromised SaaS requests broad purge | Signed declarative schema, agent-local event authorization, connector allowlist, command TTL, immutable mutation approval | A legitimately authorized event can still install denial until local intervention |
| Cross-tenant access | Authenticated tenant context, RLS, tenant-scoped queues/caches/keys, no cross-tenant dedupe | Misconfiguration or application bug |
| Direct vector-store bypass | Network/credential separation and mandatory gate path | Customer-created bypass outside Lethe |
| Replayed stale event resurrects data | Idempotency, monotonic versions, deny-wins ordering, durable tombstones | Broken source sequencing |
| Compromised receipt key fabricates evidence | Agent-scoped keys, non-exportable storage where available, rotation/revocation records, external checkpoints | Compromise before detection |
| Incomplete graph yields false success | Denominators, scope manifest, scanner, partial/unknown outcomes, unsupported-store list | Unknown systems remain unknown |
| Receipt leaks sensitive metadata | Opaque IDs, keyed fingerprints, no paths/content/ACL members, retention controls | Counts and timing may remain sensitive |
| Graph cycle or fanout causes denial of service | Cycle detection, traversal bounds, quotas, backpressure, deny-fence priority | Large valid incidents may complete slowly |
| Backup or re-ingestion restores stale data | Replay tombstones before publish, exact-fingerprint quarantine, resurrection tests | Unregistered restore path |
| Revocation connector enables SSRF | Registered connector references only; no arbitrary callback URL | Vulnerable connector implementation |
| Similarity false positive deletes valid data | Similarity is report-only in v0.1 | Human review error |
| Customer agent or host is compromised | Hardened host, separate read/mutate/approve/sign roles, protected keys, state rollback detection, signed-update recovery | Agent can deny data, mutate stores, or fabricate assertions inside its granted scope before detection |

### 7.3 Secrets and updates

- Connector, identity-provider, vector-store, and model credentials remain customer-side.
- Credentials are least-privilege and short-lived where supported.
- Signing keys are generated per tenant-environment-agent and preferably non-exportable.
- Read, mutation, approval, and receipt-signing privileges use separate credentials or processes where the connector permits it.
- Enrollment tokens are one-use and short-lived.
- Logs use allowlisted fields and error codes; no token, content, prompt, path, or connector response logging.
- Production agent updates require signed artifacts, pinned update roots, rollback protection, SBOM publication, and staged rollout.
- Agent host and local database are trusted computing-base components. Receipts state this residual trust; they are not independent attestations of agent behavior.

### 7.4 Outage behavior

| Failure | Required behavior |
|---|---|
| SaaS unavailable | Agent continues local gate enforcement, queues receipts durably, marks control state stale |
| Source connector unavailable | Existing deny remains; run cannot claim completion |
| Sink adapter unavailable | Gate remains closed for affected lineage; receipt is partial or unknown |
| Local policy too stale | Enforcement mode fails closed for affected object |
| Gate process unavailable | Protected application has no direct store credential; retrieval fails closed and health alarm fires |
| SQLite/tombstone corruption or rollback | Stop gated retrieval, reject mutations, compare rollback marker to protected checkpoint, require recovery |
| Identity/group resolver unavailable | Use signed last-known-good snapshot only within policy TTL; otherwise deny |
| Invalid event or policy signature | Reject and audit assertion; preserve current state |
| Receipt ledger unavailable | Run finishes locally, signed receipt queues for upload |
| Duplicate event | Existing run/receipt returned; no duplicate derivatives |
| Out-of-order allow | Recorded and rejected as stale |
| Clock rollback or excessive skew | Monotonic/rollback check fails; time-dependent objects deny and critical health alarm fires |
| Disk full or deny-fence commit failure | Do not acknowledge event or begin mutation; hold affected route in emergency deny if event was authenticated in memory |
| Dead letter | Alert, visible queue state, manual retry with same action identifiers |

Policy TTL is per object/risk class. Known denials and tombstones do not expire merely because SaaS is unavailable. Both pre-prompt and pre-release checks expose reason-coded health. Any break-glass mechanism is customer-controlled, time-limited, signed, audited, and cannot override a known delete, expiry, permission denial, or consent withdrawal; it may only route unaffected traffic to a customer-approved fallback.

### 7.5 Operational limits

Production configuration must bound:

- graph traversal depth, fanout, total objects, and evidence-manifest size;
- connector concurrency and rate limits;
- per-tenant queue and storage quotas;
- event and policy staleness;
- maximum automatic mutation size;
- retry count and dead-letter age;
- acceptable propagation lag and gate error rate.

Alerts cover propagation lag, repeated partial receipts, gate failures, chain forks, key revocation, dead letters, and connector freshness.

---

## 8. v0.1 — Ghost Knowledge Scanner

### 8.1 Goal

Prove, deterministically and locally, that a lifecycle event can:

1. block invalid context before cleanup;
2. enumerate a complete demo-owned derivative chain;
3. apply correct action per derivative type;
4. detect seeded untracked copies within declared stores;
5. resist defined resurrection attempts; and
6. emit a verifiable, tamper-evident receipt.

### 8.2 Closed-world boundary

v0.1 owns every component in its demo. “Full coverage” means all demo-owned derivative classes, not all possible enterprise copies.

In scope:

- local Markdown/text source documents;
- deterministic chunker;
- deterministic local embedding adapter;
- Chroma persistent collection;
- SQLite lineage and control state;
- SQLite retrieval/answer cache;
- deterministic summary generator;
- simulated agent-memory rows;
- deterministic answer generator;
- fixed alice and bob principals;
- loopback-only demo API, fixed local bearer token, and Ed25519-signed fixture event issuer;
- four lifecycle event types;
- ACL metadata synchronization and authoritative gate;
- scanner, propagation, bounded probes, signing, and receipt verifier;
- thin TypeScript dashboard.

Out of scope:

- hosted SaaS deployment;
- multi-tenant production operations;
- Google Drive, Notion, Qdrant, or Pinecone connectors;
- OAuth/OIDC implementation;
- graph database or event broker;
- general policy DSL;
- KMS/HSM integration and production key rotation;
- ODRL serialization or reasoning;
- arbitrary framework middleware;
- real-LLM correctness as an acceptance dependency;
- model unlearning, adapter retraining, backup erasure, or media sanitization.

v0.1 authentication and identity are controlled simulations, not production security evidence. APIs bind to loopback by default. The unsafe endpoint exists only when an explicit demo profile is enabled, requires a separate demo token, and is excluded from production packaging.

### 8.3 Technology shape

**Python monolith**

- ingestion;
- chunking and deterministic embedding;
- Chroma adapter;
- lineage graph;
- lifecycle event processor;
- scanner;
- propagation engine;
- runtime gate;
- demo RAG endpoint;
- receipt signer/verifier.

**SQLite**

- knowledge_objects;
- lineage_edges;
- lifecycle_events;
- policy_versions;
- tombstones;
- summaries;
- memories;
- cache_entries;
- scanner_findings;
- propagation_runs;
- propagation_actions;
- action_outbox;
- scope_manifests;
- mutation_approvals;
- acl_policies;
- key_enrollments;
- receipt_chain.

**TypeScript dashboard**

- object/lineage graph;
- event form;
- gate state;
- scan findings;
- run progress;
- action failures and exclusions;
- receipt and verification view.

v0.1 is single-process, single-writer, one active workspace. One launcher starts the Python API/worker and TypeScript development UI; the UI is never part of transaction correctness.

SQLite is the control-state source of truth. Event, deny fence, propagation run, stable action IDs, and outbox rows commit in one SQLite transaction before the event is acknowledged. The worker claims persisted actions, mutates Chroma or a local store, records read-back, and marks completion. On restart, reconciliation compares every nonterminal action to live store state and resumes idempotently. A derivative is published only after SQLite commits its envelope and lineage edge; immediately before publish, the engine rechecks every root lifecycle and policy version.

### 8.4 Deterministic generation

- Chunker uses pinned size/overlap settings and stable IDs derived from source version and ordinal.
- Default embedding is a deterministic local test adapter supplied directly to Chroma. No hosted model API is required.
- Summary generator applies fixed extraction rules to fixture facts.
- Memory generator writes fixed fact/preferences rows from declared inputs.
- Answer generator returns a templated response from gated retrieval results.
- Optional local semantic or real-LLM adapters may exist later, but their output is excluded from v0.1 pass/fail.
- Fixture manifest pins corpus bytes, chunk settings, embedding implementation/version, HMAC key version, scanner thresholds, labels, random seed, probe suite, reference hardware, and expected denominators. It is hashed before evaluation; buyer-provided holdout probes are reported separately.

### 8.5 Chroma metadata

Every vector record contains:

- tenant_id;
- workspace_id;
- environment_id;
- object_id;
- version_id;
- root_version_ids in a deterministic serial form;
- policy_ref and policy_version;
- lifecycle_state;
- acl_ref;
- valid_until where present;
- derivative_kind.

Chroma supports metadata-filtered queries and deletion by identifiers or filters, making it appropriate for the baseline ACL-sync and purge demo. The gate still rechecks current SQLite state because store metadata can converge after the deny fence. See [Chroma metadata filtering](https://docs.trychroma.com/docs/querying-collections/metadata-filtering) and [Chroma deletion](https://docs.trychroma.com/docs/collections/delete-data).

### 8.6 Demo API

| Method and path | Behavior |
|---|---|
| POST /api/v1/events | Authenticate fixture issuer, authorize event, persist it, install deny fence, return event and run identifiers |
| GET /api/v1/runs/{run_id} | Return phase, counts, failures, exclusions, and outcome |
| GET /api/v1/graph/{version_id} | Return declared roots and descendants |
| POST /api/v1/query | Authoritative gated retrieval and deterministic answer |
| POST /api/v1/demo/unsafe-query | Demo-only baseline that bypasses Lethe; disabled outside demo profile |
| POST /api/v1/scans | Run declared-store inventory and candidate detection |
| GET /api/v1/receipts/{run_id} | Return signed receipt |
| POST /api/v1/receipts/verify | Verify signature, enrollment, entry hash, ordered bundle, and trusted expected head |

POST /events returns only after the deny fence and durable action outbox commit. Cleanup continues asynchronously. Repeating an idempotency key in the same authority/target stream returns the original event/run.

### 8.7 Processing order

1. Authenticate fixture issuer and locally authorize event scope/type.
2. Reject stale sequence, nonce, command TTL, source version, or policy version.
3. Persist event, scope manifest, deny fence, run, stable action identifiers, and outbox atomically.
4. Make gate observe new denial.
5. Execute persisted store actions.
6. Read back each declared store and reconcile source plus derivative state.
7. Run fixed resurrection and recovery probes.
8. Sign receipt and advance local chain.

If any action fails, denial remains active. Retry resumes failed actions without recreating successful derivatives.

At valid-until, query-time evaluation denies immediately. A durable scheduler writes the same outbox-backed cleanup run; no external expire message is required, though an authorized source may submit one early.

### 8.8 Scanner design

The scanner uses three evidence classes:

1. **Tracked:** immutable graph edge and version metadata.
2. **Exact untracked:** same-scope, same-kind, matching key-version HMAC without graph edge.
3. **Semantic candidate:** similarity over fixture corpus above pinned threshold.

Only tracked and exact untracked objects may be automatically quarantined in v0.1. Semantic candidates are surfaced with confidence and ground-truth evaluation.

The fixture evaluator seeds hidden positive and negative copies unknown to the graph. Scanner metrics are:

- recall = true-positive findings / all seeded positive copies;
- precision = true-positive findings / all positive findings.

### 8.9 Eight-step killer demo

1. **Ingest:** Load a document containing a distinctive canary fact, policy version, validity, and alice/bob ACL.
2. **Derive and answer:** Create chunks, embeddings, cache, summary, and simulated memory; display immutable lineage and a permitted pre-event answer.
3. **Prove ghost knowledge:** Delete the source through the untreated control path, prove the source payload is absent, then show the demo-only unsafe query still discloses the canary from a derivative.
4. **Register Lethe event:** Deliver the authenticated source deletion to Lethe. Gated endpoint blocks immediately while physical descendants still exist.
5. **Scan:** Inventory all declared stores, find tracked descendants and seeded untracked copies, and show denominator plus confidence.
6. **Propagate:** Delete, tombstone, evict, suppress, or rebuild per derivative recipe; read back stores.
7. **Attack resurrection:** Re-ingest old source, replay stale event, restore stale cache, and run fixed 100-query recovery/paraphrase suite. Report measured results.
8. **Issue receipt:** Sign scoped receipt and verify it against a fixture-trusted enrollment/head. Modified receipt or missing/reordered entry in the expected ordered bundle fails verification.

Separate deterministic scripts exercise correction, expiry, and permission change.

---

## 9. v0.1 acceptance contract

### 9.1 Four-event × five-derivative matrix

| Event | Chunks | Embeddings | Caches | Summaries | Agent memories |
|---|---|---|---|---|---|
| Delete | Deny, delete payload, retain tombstone | Deny, delete Chroma record | Evict all lineage-bound entries | Tombstone/delete or rebuild from valid parents | Delete/tombstone affected fact |
| Correct | Tombstone old; build new immutable chunks | Delete old; embed new branch | Evict old policy/version keys | Rebuild from replacement and valid parents | Replace through new version; old denied |
| Expire | Deny at clock boundary; cleanup later | Filter/deny immediately; delete per retention | Evict at boundary | Deny then retention action | Deny then retention action |
| Permission change | Recompute effective ACL reference | Update Chroma policy metadata | Evict removed-principal/policy keys | Recompute intersection or rebuild | Recompute intersection; preserve valid principals |

Every cell must assert:

- immediate gate behavior;
- physical action;
- read-back result;
- lineage and tombstone state;
- receipt action and verification record.

Source payload is not a sixth derivative column, but it is part of every run scope. Delete removes or quarantines it, correction supersedes it, expiry denies then applies retention, and permission change updates its policy. A run cannot succeed while an in-scope source payload remains active contrary to policy.

### 9.2 Functional scenarios

1. Ingest one source and verify every derivative has immutable versioned lineage.
2. Untreated source-only deletion proves source absence while unsafe derivative retrieval still returns seeded fact.
3. Authorized Lethe delete makes gated query fail immediately while pre-propagation scan still sees derivative payloads.
4. Propagation clears active source/derivative payloads in declared stores, retains payload-free tombstones, and emits valid L3 receipt.
5. Correction blocks old fact, creates replacement branch, returns corrected fact, and never retrieves old branch.
6. Injectable clock proves expiry before and after valid-until without waiting in real time.
7. ACL narrowing keeps alice allowed, blocks bob, updates Chroma metadata, and evicts bob's cache.
8. Injected Chroma delete failure keeps gate closed, produces partial receipt, names residual, and converges on idempotent retry.
9. Duplicate and out-of-order events cannot duplicate derivatives or restore access.
10. Restart preserves graph, tombstones, policies, deny state, and receipt chain.
11. Re-ingesting revoked source after restart is quarantined before publication.
12. Modified receipt, wrong enrollment/public key, or removed/reordered entry fails against a previously trusted expected head and sequence.
13. Colliding object/version IDs in a second scope cannot create edges, leak cache entries, alter policy, or retrieve receipts across scope.
14. Unauthorized issuer, target, event type, permission widening, replayed nonce, and expired command are denied and audited.
15. Crash after store mutation but before action completion reconciles on restart without duplicate derivative or false success.
16. One command starts stack and one command executes deterministic eight-stage demo.

### 9.3 Performance and quality hypotheses

The fixture manifest is frozen and hashed before measurement. These are v0.1 engineering targets, not production claims:

| Metric | Target | Measurement |
|---|---:|---|
| Seeded scanner recall | ≥95% | Fixed hidden-positive corpus |
| Seeded scanner precision | ≥90% | Fixed positive and negative corpus |
| Prohibited gated disclosures | 0/100 | Fixed recovery/paraphrase suite after propagation |
| Unrelated-query false blocking | ≤1% | Fixed unaffected control suite |
| Runtime-gate overhead | p95 ≤20 ms | 10,000 warmed local checks excluding generation |
| Propagation throughput | 10,000 derivatives <60 seconds | 8-core, 16 GB RAM, local SSD, no network |
| Receipt verification | 100% valid; all tampered fixtures rejected | Fixed cryptographic fixtures |

Functional safety gates—zero prohibited gated disclosures, correct unaffected access, idempotent recovery, and receipt integrity—are mandatory. Scanner precision/recall, p95 overhead, and 10,000-derivative throughput are target hypotheses: misses block the related external claim or later enforcement release, but do not erase a valid functional-demo result. If scanner thresholds cannot meet both precision and recall, report the measured tradeoff rather than tuning against hidden labels after evaluation.

### 9.4 Evidence success rule

A successful L3 run requires:

- all registered demo stores reachable;
- scope manifest is authorized, nonzero, fresh, and hash-matched to receipt;
- source plus all five derivative classes have explicit denominators;
- complete tracked denominator for graph snapshot;
- every required action succeeds;
- every store-specific read-back succeeds;
- gate denies prohibited principals/content;
- no known residual payload remains active;
- receipt and local chain verify.

Any inaccessible declared target, residual prohibited result, or adapter failure prevents succeeded outcome.

---

## 10. Positioning and go-to-market

### 10.1 Category

**Knowledge-lifecycle assurance for heterogeneous RAG and agent stacks.**

Positioning statement:

> For AI platform teams operating customer-built, multi-store systems, Lethe maps source-to-derivative relationships, blocks invalid context, coordinates store-specific lifecycle repair, and reports what was acted on, verified, failed, or remained outside declared coverage.

Lethe does not claim that existing platforms lack deletion, ACL, lineage, or audit features. The hypothesis is that teams need vendor-neutral coordination across products and derivative classes.

### 10.2 Ideal customer profile

Strong fit:

- centralized AI platform team;
- at least three RAG or agent applications;
- at least two derivative stores or memory/cache systems;
- frequent content, identity, or policy changes;
- sensitive, licensed, regulated, or customer-controlled data;
- repeated manual lifecycle incidents or audit-evidence burden;
- ability to deploy a customer-side agent.

The broad ICP may run three or more applications, but the first private-preview deployment is deliberately narrower: one application, one supported source/export path, one supported vector store, and one app-local cache/memory adapter. Additional applications and stores remain declared exclusions until their connectors pass conformance.

Poor fit:

- one application and one store;
- reliable source identifiers on every vector;
- native deletion and permission synchronization already satisfy requirements;
- negligible lifecycle-event volume;
- no willingness to grant even read-only local scanning access.
- inability to instrument derivation paths or provide stable source/version identifiers;
- inability to route protected retrieval through a gate;
- refusal of the metadata-only SaaS boundary;
- requirements centered on model weights, forensic backup erasure, or universal proof.

### 10.3 Buyer and users

- **Champion and initial economic-buyer hypothesis:** Head of AI Platform.
- **Co-buyers:** Security, Privacy, Data Governance.
- **Operator:** Platform or ML infrastructure engineer.
- **Mutation approver:** Data owner or delegated system owner.
- **Evidence consumer:** Security assurance, internal audit, privacy operations, customer trust.

Validation must test whether Head of AI Platform owns budget rather than assuming it.

### 10.4 Land and expand

#### Land: Revocation Posture Assessment

- Available only after v0.2 supports the customer's declared topology; during the first 90 days it is a service-assisted private preview, not a production product.
- 30-day paid engagement for one environment and up to three supported connector instances.
- Read-only local agent.
- No destructive connector permissions.
- Required access: connector inventory, metadata, query/read-back, and policy/version fields. No production write permission.
- Customer supplies the scope manifest and ground-truth examples. Any seeded test is preloaded by the customer or runs in an isolated sandbox namespace; Lethe does not seed production.
- Deliverable: declared denominator, connector freshness, exact stale-copy findings, lineage gaps, unsupported scope, and incident report.
- Severity: critical for active prohibited disclosure; high for exact stale derivative; medium for missing lineage/policy metadata; informational for unsupported or unverified scope.
- Fixture precision/recall remains fixture evidence and is never represented as brownfield recall.
- Report delivered within one business day after successful scan.

#### Expand 1: Continuous monitoring

- persistent lineage capture;
- lifecycle-event monitoring;
- scanner drift alerts;
- recurring evidence reports.

#### Expand 2: Approval-gated propagation

- connector repair recipes;
- local action preview;
- data-owner approvals;
- scoped signed receipts.

#### Expand 3: Runtime enforcement

- observe-only inventory, then shadow decisions;
- canary routing after protected-path coverage and bypass checks;
- mandatory context gate only after entry criteria pass;
- immediate deny fences;
- resurrection controls;
- production evidence checkpoints.

Expansion triggers are concrete: discovered high-risk descendants, repeated lifecycle incidents, slow manual remediation, or audit-evidence burden.

Enforcement entry criteria require a named operational owner, high-availability deployment, credential/network migration that removes direct-store bypass, at least 14 days of shadow evidence, 100% declared protected-route instrumentation, ≤1% unrelated false blocking, acceptable p95 latency, tested rollback, and customer-controlled break-glass. Canary begins at no more than 5% of protected traffic. Break-glass is signed, time-limited, audited, and cannot override known revocation.

### 10.5 Pricing hypotheses

Pricing is a validation instrument, not published commitment. Annual prices below are total tier prices, not cumulative:

| Offer | Test price and included scope |
|---|---|
| 30-day posture assessment | $20,000; one environment, up to three supported connector instances, standard report, no custom connector; credited if annual contract signs within 60 days |
| Scanner/monitoring | $60,000/year; one production environment, one non-production sandbox, up to five supported connectors, standard support |
| Approval-gated propagation | $100,000/year total; scanner scope plus supported mutation recipes and approval workflow |
| Runtime enforcement | $150,000/year total; propagation scope plus gate for up to three protected applications and standard evidence checkpoints |

Customer infrastructure, bespoke connectors, migration labor, and premium support are excluded and quoted separately. Additional environment/connector bands require willingness-to-pay testing. Per-event pricing discourages correct use; object-count pricing becomes unpredictable.

### 10.6 ROI model

Annual hard savings:

    lifecycle events per year
    × (manual remediation hours per event − Lethe remediation hours per event)
    × loaded hourly labor rate

First-year total cost:

    subscription or assessment fee
    + customer and vendor integration labor
    + customer-hosted infrastructure
    + ongoing operations/on-call labor
    + approval and false-positive review labor
    + audit cycles per year
    × evidence-preparation hours saved
    × loaded hourly labor rate

Risk reduction is reported separately unless the customer supplies defensible incident-loss assumptions.

Pilot business targets:

- ≥75% remediation-time reduction;
- ≥90% evidence-preparation reduction;
- ≤6-month payback;
- hard-dollar benefit / first-year total cost ≥2×.

Discovery captures events/year, systems/event, people/event, hours/event, audit cycles/year, evidence hours/cycle, and loaded labor rate.

Value is tier-specific. Assessment/scanner value comes from discovery and evidence preparation; propagation adds remediation labor savings; runtime enforcement adds faster exposure containment. Avoided-incident value is excluded from hard savings unless customer supplies its own loss model. An account qualifies for a tier only when its measured baseline supports that tier's payback and 2× benefit/cost threshold.

### 10.7 Competitive and substitute landscape

This selected, non-exhaustive landscape reports vendor-stated capabilities, not independent verification:

| Substitute | Vendor-stated or documented strength | Enough when | Lethe hypothesis to test, not presumed absence |
|---|---|---|---|
| Glean | Managed knowledge layer, custom ingestion, deletion/permission synchronization, permission-aware search and agents | Customer centralizes retrieval and agents on Glean | Repair derivatives that remain outside Glean-controlled index/query paths |
| BigID | Direct incumbent overlap across AI discovery, vector data, lineage, access, policy, remediation, and evidence | Customer's BigID deployment already covers target objects and actions | Test immutable source-version lineage across chunks/caches/summaries/memories, postcondition recipes, bounded resurrection, and scoped receipts in live RFP/demo |
| Collibra | Technical lineage and downstream impact analysis | Catalog/lineage workflow covers required outcome | Consume impacted assets, execute configured local repair, return connector read-back and receipt |
| Amazon Bedrock Knowledge Bases | Managed data-source deletion policy, vector-data propagation, failure states, and ACL-aware retrieval | Application stays inside managed knowledge-base boundary | Coordinate external caches, summaries, memories, and other stores around Bedrock events |
| OneTrust/privacy lifecycle platforms | Broad discovery, retention/deletion workflow, and governance integration claims | Existing privacy platform reaches target AI derivatives | Act as customer-local AI derivative execution/evidence plug-in |
| Pinecone and Chroma | ID/filter deletion, metadata filtering, and documented consistency behavior | Few stores, stable source IDs, simple worker | Coordinate multi-parent/multi-store repair, ordering, and bounded verification |
| Mem0 and memory APIs | Memory update, batch/filter deletion, verification guidance, and expiry workflows | One memory system owns relevant memories | Link memory actions to upstream versions and other derivative classes |
| Internal orchestration | Tailored to known stack with no new vendor | Event volume and stack change do not justify product TCO | Standard recipes, conformance, coverage accounting, and evidence must beat internal TCO |

AI gateways are enough when immediate runtime suppression is the entire job; Lethe's hypothesis adds durable downstream repair and recovery testing. Model-unlearning systems are complementary when invalid information resides in weights, which remains outside v0.1.

Primary-source references:

- [Glean: tracking content deletions and permissions](https://docs.glean.com/administration/search/about)
- [Glean custom data-source deletion](https://docs.glean.com/connectors/custom/faq)
- [BigID AI security and governance](https://bigid.com/ai-security-governance/)
- [Collibra Data Lineage and impact analysis](https://productresources.collibra.com/docs/collibra/latest/Content/CollibraDataLineage/co_collibra-data-lineage.htm)
- [Amazon Bedrock knowledge-base data-source deletion](https://docs.aws.amazon.com/bedrock/latest/userguide/kb-ds-delete.html)
- [OneTrust Data Discovery and Security](https://www.onetrust.com/platform/data-discovery-and-security/)
- [Pinecone record deletion](https://docs.pinecone.io/guides/manage-data/delete-data)
- [Mem0 memory deletion](https://docs.mem0.ai/core-concepts/memory-operations/delete)

Competitive claims must be refreshed before external use. A cited page proves only what that vendor states. Lethe must not assert that an incumbent lacks a control until current documentation plus live RFP/demo verification supports it.

### 10.8 Potential execution advantages

These are possible execution advantages, not established moat:

- breadth and correctness of connector-specific repair recipes;
- normalized derivative ontology across stores;
- connector conformance suite and resurrection-test corpus;
- historical reliability data for actions and postconditions;
- installed lineage graph and customer workflow integration;
- evidence formats accepted by customer security and audit teams.

Not independently defensible:

- Ed25519;
- hash linking;
- hybrid deployment;
- a dashboard;
- generic connectors;
- “GC for meaning” phrasing.

“Auditor-accepted evidence” remains a validation milestone. Until then, call it auditor-oriented evidence.

Compounding-defensibility gates:

- repair recipes work unchanged across multiple customers;
- median connector setup effort declines across first five installations;
- connector conformance coverage grows faster than supported connector count;
- customers permit aggregation of non-content action reliability metrics;
- independent teams adopt the receipt format/verifier;
- incumbent integrations or displacement appear repeatedly in qualified opportunities.

Installed lineage alone is switching cost, not proof of moat. Receipt schema and an independently runnable offline verifier ship before paid pilot even though the commercial platform remains closed.

---

## 11. Ninety-day validation plan

### 11.1 Hypotheses

1. Costly cross-system lifecycle incidents occur often enough to fund a product.
2. Head of AI Platform owns urgency and budget, not only technical interest.
3. Scanner-first assessment produces value without destructive access.
4. Customer-owned stacks can be connected without consulting-heavy setup.
5. Scoped receipts reduce evidence-preparation work.
6. Runtime-gate latency and fail-closed behavior are operationally acceptable.
7. Buyers value Lethe beyond source-platform and store-native controls.

### 11.2 Days 1–30: problem and buyer validation

- Select a customer-facing working name before interviews; never use Lethe externally.
- Interview 15 ICP participants across at least 10 companies.
- Pass broad problem threshold when at least 8 describe a concrete case where one authenticated source lifecycle event failed across at least two derivative systems outside source control.
- Narrow to a sharper incident/event wedge when 5–7 qualify; kill broad thesis below 5.
- Require at least 5 to quantify people, systems, time, or audit cost.
- Record budget owner, deployment owner, mutation approver, security objections, and current workaround.
- Require at least 3 budget owners to accept the relevant test-price band as plausible without more than 25% discount, and at least 2 to commit to assessment procurement steps.

### 11.3 Days 31–60: solution validation

- Run five buyer-evaluated deterministic demos.
- At least four must pass a pre-agreed acceptance rubric.
- At least three prospects provide sanitized lineage maps, configuration exports, or representative fixtures.
- Build one service-assisted v0.2 connector path suitable for a read-only private preview; do not present it as generally available product.
- Demonstrate untreated control path, immediate gate denial, propagation, bounded recovery tests, and offline receipt verification.
- Measure buyer understanding of scope limitations; a buyer who interprets receipt as universal proof counts as a messaging failure.

### 11.4 Days 61–90: brownfield and commercial validation

- Run two customer-topology read-only scans. At least one must run in a live customer-controlled environment; one may use a sanitized export and counts only as technical learning.
- For each, record elapsed business days and Lethe/customer engineer-days separately. Target ≤10 elapsed business days and ≤5 Lethe engineer-days per supported topology.
- Obtain at least one paid v0.2 private-preview statement of work. It sells defined assessment scope, not production enforcement or v1 availability.
- Obtain at least two budget-owner statements naming budget source, deployment owner, and target production date.
- Quantify ROI in at least three accounts.
- Have at least two security, privacy, or audit stakeholders use the offline verifier and measure evidence-preparation baseline/after hours.

### 11.5 Kill and narrow criteria

Apply one defined two-week repair cycle before a technical kill decision.

| Criterion | Decision |
|---|---|
| 8+ of 15 qualifying cross-system incidents | Continue broad thesis |
| 5–7 of 15 qualifying incidents | Narrow event, buyer, or topology wedge before build |
| Fewer than 5 of 15 qualifying incidents | Kill broad problem thesis |
| Fewer than 3 of 15 participants own budget or urgent roadmap | Kill Head-of-AI-Platform buyer thesis; test security/privacy buyer |
| Fewer than 3 budget owners accept test-price band, or fewer than 2 enter procurement steps | Rework price/offer before commercial build |
| One supported scan exceeds 10 elapsed days or 5 Lethe engineer-days | Narrow or repair that connector before another sale |
| Both supported scans exceed either setup threshold | Kill scanner-first product shape or reset architecture |
| No paid pilot by day 90 | Stop commercial build and reassess wedge |
| Three quantified accounts show hard savings below 2× proposed annual price | Reduce price/cost or kill economics |
| Seeded scanner recall remains below 95% or precision below 90% | Narrow scanner claims and automation scope |
| Any unauthorized gated retrieval remains in acceptance suite | Block runtime-gate release |
| Unrelated-query false blocking exceeds 1% | Block enforcement mode |
| Gate p95 overhead exceeds 20 ms on reference test | Rework or limit deployment path |
| Measured receipt workflow does not reduce evidence effort by at least 90% for both evaluator workflows | Remove 90% claim; retain only measured result |

The localhost demo is technical proof only. It cannot satisfy market-validation criteria.

---

## 12. Roadmap

### v0.1 — Deterministic localhost proof

- Local documents, SQLite, Chroma.
- Four lifecycle events.
- Five derivative classes.
- ACL sync plus authoritative gate.
- Scanner, propagation, local resurrection probes.
- Ed25519 receipts and local hash-linked chain.
- Frozen benchmark/scope manifest and independently runnable receipt verifier.
- TypeScript dashboard.

### v0.2 — First real connectors

- Google Drive and Notion source connectors.
- Qdrant and Pinecone sink adapters.
- App-local cache/memory adapter contract and one reference adapter.
- Incumbent event/finding ingest plus receipt/status callback for one governance workflow.
- Connector capability manifests and conformance tests.
- Real identity-policy references.
- Control-plane alpha using metadata-only contract.
- Read-only posture assessment workflow.
- Service-assisted private preview in one supported application topology.

### v1 — Paid pilots

- Production self-hosted agent and SaaS control plane.
- Shared logical tenancy with enforced isolation.
- Approval-gated mutations.
- Signed control-plane events and mTLS enrollment.
- Key rotation/revocation and externally retained checkpoints.
- Continuous lineage and connector freshness monitoring.
- Pilot-grade SLOs, alerting, support workflow, SBOM, and signed updates.
- Shadow/canary/enforcement rollout gates and bypass attestation.

### Later

- Framework middleware for common RAG and agent runtimes.
- More source, vector, cache, and memory connectors.
- Consent/license withdrawal enforcement.
- Legal-hold and retention integrations.
- Adapter retraining and unlearning-job orchestration.
- Independent verifier tooling and auditor export standards.
- Optional W3C PROV/ODRL interchange.

Model-weight changes remain a separate adapter/orchestration capability, not a core erasure claim.

---

## 13. Risks and open questions

### 13.1 Product risks

| Risk | Response |
|---|---|
| Unknown copies remain outside graph | Scope manifest, scanner, denominators, unknown outcome, no universal claim |
| Customers believe vector-row deletion is enough | Disqualify simple stacks; demonstrate multi-parent summaries, caches, and memories |
| Connector work becomes consulting-heavy | Capability manifests, conformance suite, setup-time kill criterion |
| Runtime gate adds latency or outage risk | Local cached decisions, no SaaS round trip, explicit SLO, fail-closed enforcement |
| Gate can be bypassed | Credential/network separation; document customer-controlled bypass as residual risk |
| Shared derivative still has valid parents | Rebuild from surviving parents; do not blindly cascade delete |
| Legal hold conflicts with delete | Deny retrieval, quarantine payload, report blocked physical action |
| Backups or re-ingestion resurrect content | Tombstone replay before publish, exact fingerprint quarantine, bounded restoration tests |
| Semantic scanner deletes unrelated content | Semantic findings remain report-only until separately validated |
| Closed model weights retain knowledge | Explicit exclusion; later unlearning orchestration only |
| Receipts overstate certainty | Scoped language, denominators, exclusions, partial/unknown states |
| SaaS metadata reveals sensitive relationships | Opaque IDs, keyed fingerprints, minimization, tenant encryption |

### 13.2 Open validation questions

- Is Head of AI Platform economic buyer, technical champion, or both?
- Which incident type creates fastest urgency: deletion, correction, expiry, or permission drift?
- Will customers grant read-only inventory access, then approval-gated mutation access?
- Which two brownfield connectors produce repeatable value with least setup?
- Do security and audit teams accept scoped receipts as useful evidence?
- What chain-checkpoint custody model is simplest for customers?
- Which semantic probe corpus generalizes beyond controlled fixtures?
- Does metadata-only SaaS retain enough usability for operators?
- What retention rules apply to tombstones and receipts in each customer context?
- Which pricing metric best tracks value without discouraging lifecycle events?

### 13.3 Naming risk

Lethe is an internal-only codename with a confirmed near-direct collision. The current PyPI project [pylethe](https://pypi.org/project/pylethe/) describes local-first AI memory, forgetting/supersession, and signed purge receipts, while the PyPI lethe package name is already occupied. Other AI products also use Lethe branding. This creates package, import, SEO, interview, and product-concept confusion.

Before any external interview:

- choose a neutral customer-facing working name;
- prohibit Lethe in external decks, domains, packages, demos, and interviews;
- run trademark clearance in intended markets;
- check domain, company, package, and social-handle availability;
- assess search/SEO discoverability and adjacent product confusion.

The local folder and this document may retain Lethe solely as historical internal codename.

---

## 14. Product decision log

| Decision | Locked for this spec |
|---|---|
| Product | Derived-data revocation control plane / Ghost Knowledge Scanner |
| Codename | Lethe, internal-only due confirmed collision; external replacement required |
| Business model | Closed commercial |
| Primary buyer | Head of AI Platform hypothesis |
| Topology | Customer-side data plane plus metadata-only SaaS control plane |
| v0.1 | Fully local, deterministic, closed-world demo |
| Data plane | Python monolith |
| Dashboard | TypeScript |
| State | SQLite plus Chroma |
| v0.1 events | Delete, correct, expire, permission change |
| Roadmap event | Consent/license withdrawal |
| Derivatives | Chunks, embeddings, caches, summaries, agent memories |
| Permission controls | Store metadata ACL sync plus authoritative runtime gate |
| Enforcement order | Immediate deny fence, asynchronous repair |
| Evidence | Scoped Ed25519 receipt plus tenant-environment-agent SHA-256 hash link |
| v0.1 evidence target | L3; local resurrection probes do not independently qualify as L4 |
| Production tenancy | Shared logical tenancy with hard tenant context and per-tenant encryption |
| Source content in SaaS | Prohibited by default |
| Build status | Blocked pending review of this specification |

---

## 15. Review and approval gate

This document is ready for product review when:

- all locked decisions appear without contradiction;
- five product triggers and four v0.1 events remain distinct;
- every completion claim names declared scope and evidence level;
- target commercial architecture is not confused with localhost v0.1;
- competitor descriptions use current primary sources;
- pricing, buyer, market, moat, and performance statements are labeled hypotheses where unvalidated;
- four independent critiques—enterprise buyer, security architecture, competitive differentiation, and feasibility—have dispositions;
- no implementation, repository initialization, or commit occurs before user approval.

Approval of this specification permits a separate implementation plan. It does not itself authorize production deployment, customer data access, external publication, or commercial claims.

---

## 16. References

Standards and cryptographic formats:

- [W3C PROV-O](https://www.w3.org/TR/prov-o/)
- [W3C ODRL Information Model 2.2](https://www.w3.org/TR/odrl-model/)
- [RFC 8032: EdDSA, including Ed25519](https://www.rfc-editor.org/rfc/rfc8032.html)
- [RFC 8785: JSON Canonicalization Scheme](https://www.rfc-editor.org/rfc/rfc8785.html)

Current product documentation used for positioning:

- [Glean Search](https://docs.glean.com/administration/search/about)
- [Glean custom API deletion](https://docs.glean.com/connectors/custom/faq)
- [BigID AI security and governance](https://bigid.com/ai-security-governance/)
- [Collibra Data Lineage](https://productresources.collibra.com/docs/collibra/latest/Content/CollibraDataLineage/co_collibra-data-lineage.htm)
- [Amazon Bedrock knowledge-base data-source deletion](https://docs.aws.amazon.com/bedrock/latest/userguide/kb-ds-delete.html)
- [OneTrust Data Discovery and Security](https://www.onetrust.com/platform/data-discovery-and-security/)
- [Pinecone delete records](https://docs.pinecone.io/guides/manage-data/delete-data)
- [Mem0 delete memory](https://docs.mem0.ai/core-concepts/memory-operations/delete)
- [Chroma metadata filtering](https://docs.trychroma.com/docs/querying-collections/metadata-filtering)
- [Chroma delete data](https://docs.trychroma.com/docs/collections/delete-data)
- [pylethe project collision](https://pypi.org/project/pylethe/)

Sources checked 2026-07-14. Market and vendor capabilities must be refreshed before external publication.
