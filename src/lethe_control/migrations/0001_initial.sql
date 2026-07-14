CREATE TABLE knowledge_objects (
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    environment_id TEXT NOT NULL,
    version_id TEXT NOT NULL,
    object_id TEXT NOT NULL,
    schema_version TEXT NOT NULL CHECK (schema_version = '1'),
    kind TEXT NOT NULL CHECK (kind IN ('source', 'chunk', 'embedding', 'cache', 'summary', 'memory')),
    lifecycle_state TEXT NOT NULL CHECK (lifecycle_state IN ('active', 'denied', 'tombstoned')),
    parent_version_ids_json TEXT NOT NULL,
    root_version_ids_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    valid_from TEXT NOT NULL,
    valid_until TEXT,
    policy_ref TEXT NOT NULL,
    policy_version INTEGER NOT NULL CHECK (policy_version >= 1),
    acl_ref TEXT NOT NULL,
    supersedes_version_id TEXT,
    content_ref TEXT,
    local_content_fingerprint TEXT,
    provenance_json TEXT NOT NULL,
    governance_json TEXT NOT NULL,
    PRIMARY KEY (tenant_id, workspace_id, environment_id, version_id),
    FOREIGN KEY (
        tenant_id, workspace_id, environment_id, supersedes_version_id
    ) REFERENCES knowledge_objects (
        tenant_id, workspace_id, environment_id, version_id
    ) DEFERRABLE INITIALLY DEFERRED
);

CREATE INDEX knowledge_objects_by_object
    ON knowledge_objects (tenant_id, workspace_id, environment_id, object_id, created_at);
CREATE INDEX knowledge_objects_by_gate
    ON knowledge_objects (
        tenant_id, workspace_id, environment_id,
        lifecycle_state, policy_version, valid_until
    );
CREATE INDEX knowledge_objects_by_fingerprint
    ON knowledge_objects (
        tenant_id, workspace_id, environment_id, kind, local_content_fingerprint
    );

CREATE TABLE lineage_edges (
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    environment_id TEXT NOT NULL,
    parent_version_id TEXT NOT NULL,
    child_version_id TEXT NOT NULL,
    activity_type TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (
        tenant_id, workspace_id, environment_id, parent_version_id, child_version_id
    ),
    CHECK (parent_version_id <> child_version_id),
    FOREIGN KEY (
        tenant_id, workspace_id, environment_id, parent_version_id
    ) REFERENCES knowledge_objects (
        tenant_id, workspace_id, environment_id, version_id
    ) ON DELETE RESTRICT,
    FOREIGN KEY (
        tenant_id, workspace_id, environment_id, child_version_id
    ) REFERENCES knowledge_objects (
        tenant_id, workspace_id, environment_id, version_id
    ) ON DELETE RESTRICT
);

CREATE INDEX lineage_edges_by_child
    ON lineage_edges (tenant_id, workspace_id, environment_id, child_version_id);

CREATE TRIGGER lineage_edges_prevent_cycle
BEFORE INSERT ON lineage_edges
WHEN EXISTS (
    WITH RECURSIVE descendants(version_id) AS (
        SELECT child_version_id
        FROM lineage_edges
        WHERE tenant_id = NEW.tenant_id
          AND workspace_id = NEW.workspace_id
          AND environment_id = NEW.environment_id
          AND parent_version_id = NEW.child_version_id
        UNION
        SELECT edge.child_version_id
        FROM lineage_edges AS edge
        JOIN descendants AS prior ON edge.parent_version_id = prior.version_id
        WHERE edge.tenant_id = NEW.tenant_id
          AND edge.workspace_id = NEW.workspace_id
          AND edge.environment_id = NEW.environment_id
    )
    SELECT 1 FROM descendants WHERE version_id = NEW.parent_version_id
)
BEGIN
    SELECT RAISE(ABORT, 'lineage cycle');
END;

CREATE TABLE knowledge_roots (
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    environment_id TEXT NOT NULL,
    version_id TEXT NOT NULL,
    root_version_id TEXT NOT NULL,
    PRIMARY KEY (
        tenant_id, workspace_id, environment_id, version_id, root_version_id
    ),
    FOREIGN KEY (
        tenant_id, workspace_id, environment_id, version_id
    ) REFERENCES knowledge_objects (
        tenant_id, workspace_id, environment_id, version_id
    ) ON DELETE CASCADE,
    FOREIGN KEY (
        tenant_id, workspace_id, environment_id, root_version_id
    ) REFERENCES knowledge_objects (
        tenant_id, workspace_id, environment_id, version_id
    ) ON DELETE RESTRICT
);

CREATE INDEX knowledge_roots_by_root
    ON knowledge_roots (tenant_id, workspace_id, environment_id, root_version_id);

CREATE TABLE lifecycle_events (
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    environment_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    schema_version TEXT NOT NULL CHECK (schema_version = '1'),
    idempotency_key TEXT NOT NULL,
    issuer_key_id TEXT NOT NULL,
    audience TEXT NOT NULL,
    authority_ref TEXT NOT NULL,
    nonce TEXT NOT NULL,
    target_version_id TEXT NOT NULL,
    event_type TEXT NOT NULL CHECK (
        event_type IN ('delete', 'correct', 'expire', 'permission_change')
    ),
    source_sequence INTEGER NOT NULL CHECK (source_sequence >= 1),
    policy_version INTEGER NOT NULL CHECK (policy_version >= 1),
    occurred_at TEXT NOT NULL,
    effective_at TEXT NOT NULL,
    command_expires_at TEXT NOT NULL,
    actor_ref TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    correction_json TEXT,
    permission_change_json TEXT,
    assertion_status TEXT NOT NULL DEFAULT 'accepted' CHECK (
        assertion_status IN ('accepted', 'rejected')
    ),
    rejection_reason_code TEXT,
    received_at TEXT NOT NULL,
    run_id TEXT,
    PRIMARY KEY (tenant_id, workspace_id, environment_id, event_id),
    UNIQUE (
        tenant_id, workspace_id, environment_id,
        authority_ref, target_version_id, idempotency_key
    ),
    UNIQUE (
        tenant_id, workspace_id, environment_id, issuer_key_id, nonce
    ),
    UNIQUE (
        tenant_id, workspace_id, environment_id,
        authority_ref, target_version_id, source_sequence
    )
);

CREATE INDEX lifecycle_events_by_target
    ON lifecycle_events (
        tenant_id, workspace_id, environment_id,
        target_version_id, source_sequence DESC
    );

CREATE TABLE policy_versions (
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    environment_id TEXT NOT NULL,
    policy_ref TEXT NOT NULL,
    policy_version INTEGER NOT NULL CHECK (policy_version >= 1),
    snapshot_hash TEXT NOT NULL,
    effective_at TEXT NOT NULL,
    expires_at TEXT,
    source_authority_ref TEXT NOT NULL,
    policy_json TEXT NOT NULL,
    signature TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (
        tenant_id, workspace_id, environment_id, policy_ref, policy_version
    )
);

CREATE TABLE tombstones (
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    environment_id TEXT NOT NULL,
    target_version_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    object_kind TEXT NOT NULL CHECK (
        object_kind IN ('source', 'chunk', 'embedding', 'cache', 'summary', 'memory')
    ),
    fingerprint_key_version TEXT,
    local_content_fingerprint TEXT,
    reason_code TEXT NOT NULL,
    policy_version INTEGER NOT NULL CHECK (policy_version >= 1),
    created_at TEXT NOT NULL,
    PRIMARY KEY (tenant_id, workspace_id, environment_id, target_version_id),
    FOREIGN KEY (
        tenant_id, workspace_id, environment_id, target_version_id
    ) REFERENCES knowledge_objects (
        tenant_id, workspace_id, environment_id, version_id
    ) ON DELETE RESTRICT,
    FOREIGN KEY (
        tenant_id, workspace_id, environment_id, event_id
    ) REFERENCES lifecycle_events (
        tenant_id, workspace_id, environment_id, event_id
    ) ON DELETE RESTRICT
);

CREATE INDEX tombstones_by_fingerprint
    ON tombstones (
        tenant_id, workspace_id, environment_id,
        object_kind, fingerprint_key_version, local_content_fingerprint
    );

CREATE TABLE summaries (
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    environment_id TEXT NOT NULL,
    version_id TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (tenant_id, workspace_id, environment_id, version_id),
    FOREIGN KEY (
        tenant_id, workspace_id, environment_id, version_id
    ) REFERENCES knowledge_objects (
        tenant_id, workspace_id, environment_id, version_id
    ) ON DELETE CASCADE
);

CREATE TABLE memories (
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    environment_id TEXT NOT NULL,
    version_id TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (tenant_id, workspace_id, environment_id, version_id),
    FOREIGN KEY (
        tenant_id, workspace_id, environment_id, version_id
    ) REFERENCES knowledge_objects (
        tenant_id, workspace_id, environment_id, version_id
    ) ON DELETE CASCADE
);

CREATE TABLE cache_entries (
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    environment_id TEXT NOT NULL,
    cache_key TEXT NOT NULL,
    version_id TEXT NOT NULL,
    principal_ref TEXT NOT NULL,
    policy_version INTEGER NOT NULL CHECK (policy_version >= 1),
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT,
    PRIMARY KEY (tenant_id, workspace_id, environment_id, cache_key),
    FOREIGN KEY (
        tenant_id, workspace_id, environment_id, version_id
    ) REFERENCES knowledge_objects (
        tenant_id, workspace_id, environment_id, version_id
    ) ON DELETE CASCADE
);

CREATE INDEX cache_entries_by_version
    ON cache_entries (tenant_id, workspace_id, environment_id, version_id);
CREATE INDEX cache_entries_by_principal_policy
    ON cache_entries (
        tenant_id, workspace_id, environment_id, principal_ref, policy_version
    );

CREATE TABLE scanner_findings (
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    environment_id TEXT NOT NULL,
    finding_id TEXT NOT NULL,
    scan_id TEXT NOT NULL,
    finding_class TEXT NOT NULL CHECK (
        finding_class IN ('tracked', 'exact_untracked', 'semantic_candidate')
    ),
    derivative_kind TEXT NOT NULL CHECK (
        derivative_kind IN ('source', 'chunk', 'embedding', 'cache', 'summary', 'memory')
    ),
    connector_ref TEXT NOT NULL,
    target_version_id TEXT,
    fingerprint_key_version TEXT,
    local_content_fingerprint TEXT,
    confidence REAL CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
    disposition TEXT NOT NULL DEFAULT 'reported' CHECK (
        disposition IN ('reported', 'approved', 'dismissed', 'acted')
    ),
    found_at TEXT NOT NULL,
    PRIMARY KEY (tenant_id, workspace_id, environment_id, finding_id)
);

CREATE INDEX scanner_findings_by_scan
    ON scanner_findings (
        tenant_id, workspace_id, environment_id, scan_id, finding_class
    );

CREATE TABLE scope_manifests (
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    environment_id TEXT NOT NULL,
    manifest_id TEXT NOT NULL,
    schema_version TEXT NOT NULL CHECK (schema_version = '1'),
    manifest_hash TEXT NOT NULL,
    selected_by TEXT NOT NULL,
    requested_evidence_level TEXT NOT NULL CHECK (
        requested_evidence_level IN ('L0', 'L1', 'L2', 'L3', 'L4')
    ),
    scan_cutoff TEXT NOT NULL,
    registered_store_refs_json TEXT NOT NULL,
    registered_connector_refs_json TEXT NOT NULL,
    connector_capability_versions_json TEXT NOT NULL,
    derivative_classes_json TEXT NOT NULL,
    freshness_cursors_json TEXT NOT NULL,
    denominators_json TEXT NOT NULL,
    exclusions_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (tenant_id, workspace_id, environment_id, manifest_id),
    UNIQUE (tenant_id, workspace_id, environment_id, manifest_hash)
);

CREATE TABLE mutation_approvals (
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    environment_id TEXT NOT NULL,
    approval_id TEXT NOT NULL,
    schema_version TEXT NOT NULL CHECK (schema_version = '1'),
    action_plan_hash TEXT NOT NULL,
    connector_refs_json TEXT NOT NULL,
    target_version_refs_json TEXT NOT NULL,
    maximum_action_count INTEGER NOT NULL CHECK (maximum_action_count >= 1),
    requester_ref TEXT NOT NULL,
    approver_ref TEXT NOT NULL,
    approver_authority TEXT NOT NULL CHECK (approver_authority = 'mutation_approve'),
    policy_snapshot_hash TEXT NOT NULL,
    legal_hold_snapshot_hash TEXT NOT NULL,
    nonce TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    signature TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (tenant_id, workspace_id, environment_id, approval_id),
    UNIQUE (tenant_id, workspace_id, environment_id, approver_ref, nonce)
);

CREATE TABLE acl_policies (
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    environment_id TEXT NOT NULL,
    acl_ref TEXT NOT NULL,
    schema_version TEXT NOT NULL CHECK (schema_version = '1'),
    policy_version INTEGER NOT NULL CHECK (policy_version >= 1),
    allowed_principal_refs_json TEXT NOT NULL,
    denied_principal_refs_json TEXT NOT NULL,
    allowed_group_refs_json TEXT NOT NULL,
    denied_group_refs_json TEXT NOT NULL,
    permitted_purpose_refs_json TEXT NOT NULL,
    permitted_actions_json TEXT NOT NULL,
    valid_from TEXT NOT NULL,
    valid_until TEXT,
    source_authority_ref TEXT NOT NULL,
    group_snapshot_version TEXT NOT NULL,
    group_snapshot_expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    signature TEXT NOT NULL,
    PRIMARY KEY (
        tenant_id, workspace_id, environment_id, acl_ref, policy_version
    )
);

CREATE TABLE key_enrollments (
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    environment_id TEXT NOT NULL,
    key_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    public_key TEXT NOT NULL,
    signing_role TEXT NOT NULL CHECK (signing_role IN ('event_issuer', 'receipt_signer')),
    status TEXT NOT NULL CHECK (status IN ('active', 'revoked', 'expired')),
    valid_from TEXT NOT NULL,
    valid_until TEXT,
    enrolled_at TEXT NOT NULL,
    PRIMARY KEY (tenant_id, workspace_id, environment_id, key_id)
);

CREATE INDEX key_enrollments_by_agent
    ON key_enrollments (
        tenant_id, workspace_id, environment_id, agent_id, signing_role, status
    );

CREATE TABLE propagation_runs (
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    environment_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    scope_manifest_id TEXT NOT NULL,
    approval_id TEXT,
    phase TEXT NOT NULL,
    outcome TEXT CHECK (outcome IS NULL OR outcome IN ('succeeded', 'partial', 'failed', 'unknown')),
    graph_snapshot_hash TEXT NOT NULL,
    action_plan_hash TEXT NOT NULL,
    counts_json TEXT NOT NULL DEFAULT '{}',
    failures_json TEXT NOT NULL DEFAULT '[]',
    exclusions_json TEXT NOT NULL DEFAULT '[]',
    started_at TEXT NOT NULL,
    completed_at TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (tenant_id, workspace_id, environment_id, run_id),
    UNIQUE (tenant_id, workspace_id, environment_id, event_id),
    FOREIGN KEY (
        tenant_id, workspace_id, environment_id, event_id
    ) REFERENCES lifecycle_events (
        tenant_id, workspace_id, environment_id, event_id
    ) ON DELETE RESTRICT,
    FOREIGN KEY (
        tenant_id, workspace_id, environment_id, scope_manifest_id
    ) REFERENCES scope_manifests (
        tenant_id, workspace_id, environment_id, manifest_id
    ) ON DELETE RESTRICT,
    FOREIGN KEY (
        tenant_id, workspace_id, environment_id, approval_id
    ) REFERENCES mutation_approvals (
        tenant_id, workspace_id, environment_id, approval_id
    ) ON DELETE RESTRICT
);

CREATE INDEX propagation_runs_by_phase
    ON propagation_runs (tenant_id, workspace_id, environment_id, phase, updated_at);

CREATE TABLE propagation_actions (
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    environment_id TEXT NOT NULL,
    action_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    target_version_id TEXT,
    target_kind TEXT NOT NULL CHECK (
        target_kind IN ('source', 'chunk', 'embedding', 'cache', 'summary', 'memory')
    ),
    connector_ref TEXT NOT NULL,
    action_code TEXT NOT NULL CHECK (
        action_code IN (
            'deny', 'delete', 'quarantine', 'tombstone',
            'evict', 'suppress', 'rebuild', 'update_acl'
        )
    ),
    state TEXT NOT NULL DEFAULT 'pending' CHECK (
        state IN ('pending', 'leased', 'retryable', 'succeeded', 'failed', 'dead_letter')
    ),
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    lease_owner TEXT,
    lease_expires_at TEXT,
    last_error_code TEXT,
    read_back_json TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (tenant_id, workspace_id, environment_id, action_id),
    UNIQUE (
        tenant_id, workspace_id, environment_id,
        run_id, target_version_id, target_kind, connector_ref, action_code
    ),
    FOREIGN KEY (
        tenant_id, workspace_id, environment_id, run_id
    ) REFERENCES propagation_runs (
        tenant_id, workspace_id, environment_id, run_id
    ) ON DELETE CASCADE
);

CREATE INDEX propagation_actions_by_state
    ON propagation_actions (
        tenant_id, workspace_id, environment_id, state, updated_at
    );

CREATE TABLE action_outbox (
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    environment_id TEXT NOT NULL,
    action_id TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'pending' CHECK (
        state IN ('pending', 'leased', 'retryable', 'succeeded', 'failed', 'dead_letter')
    ),
    available_at TEXT NOT NULL,
    lease_owner TEXT,
    lease_expires_at TEXT,
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    last_error_code TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (tenant_id, workspace_id, environment_id, action_id),
    FOREIGN KEY (
        tenant_id, workspace_id, environment_id, action_id
    ) REFERENCES propagation_actions (
        tenant_id, workspace_id, environment_id, action_id
    ) ON DELETE CASCADE
);

CREATE INDEX action_outbox_claim
    ON action_outbox (
        tenant_id, workspace_id, environment_id, state, available_at, lease_expires_at
    );

CREATE TABLE receipt_chain (
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    environment_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    chain_sequence INTEGER NOT NULL CHECK (chain_sequence >= 1),
    run_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    key_id TEXT NOT NULL,
    previous_receipt_hash TEXT,
    entry_hash TEXT NOT NULL,
    receipt_json TEXT NOT NULL,
    signed_at TEXT NOT NULL,
    PRIMARY KEY (
        tenant_id, workspace_id, environment_id, agent_id, chain_sequence
    ),
    UNIQUE (tenant_id, environment_id, agent_id, chain_sequence),
    UNIQUE (tenant_id, environment_id, agent_id, entry_hash),
    UNIQUE (tenant_id, workspace_id, environment_id, agent_id, run_id),
    FOREIGN KEY (
        tenant_id, workspace_id, environment_id, run_id
    ) REFERENCES propagation_runs (
        tenant_id, workspace_id, environment_id, run_id
    ) ON DELETE RESTRICT,
    FOREIGN KEY (
        tenant_id, workspace_id, environment_id, event_id
    ) REFERENCES lifecycle_events (
        tenant_id, workspace_id, environment_id, event_id
    ) ON DELETE RESTRICT,
    FOREIGN KEY (
        tenant_id, workspace_id, environment_id, key_id
    ) REFERENCES key_enrollments (
        tenant_id, workspace_id, environment_id, key_id
    ) ON DELETE RESTRICT
);

CREATE TRIGGER receipt_chain_genesis_link
BEFORE INSERT ON receipt_chain
WHEN NEW.chain_sequence = 1 AND NEW.previous_receipt_hash IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'genesis receipt cannot have previous hash');
END;

CREATE TRIGGER receipt_chain_continuity
BEFORE INSERT ON receipt_chain
WHEN NEW.chain_sequence > 1 AND NOT EXISTS (
    SELECT 1
    FROM receipt_chain AS previous
    WHERE previous.tenant_id = NEW.tenant_id
      AND previous.environment_id = NEW.environment_id
      AND previous.agent_id = NEW.agent_id
      AND previous.chain_sequence = NEW.chain_sequence - 1
      AND previous.entry_hash = NEW.previous_receipt_hash
)
BEGIN
    SELECT RAISE(ABORT, 'receipt chain discontinuity');
END;
