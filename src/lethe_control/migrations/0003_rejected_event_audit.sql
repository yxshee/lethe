CREATE TABLE rejected_event_audit (
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    environment_id TEXT NOT NULL,
    rejection_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    issuer_key_id TEXT NOT NULL,
    authority_ref TEXT NOT NULL,
    target_version_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    source_sequence INTEGER NOT NULL,
    nonce TEXT NOT NULL,
    rejection_reason_code TEXT NOT NULL,
    event_json TEXT NOT NULL,
    received_at TEXT NOT NULL,
    PRIMARY KEY (tenant_id, workspace_id, environment_id, rejection_id)
);

CREATE INDEX rejected_event_audit_by_event
    ON rejected_event_audit (
        tenant_id, workspace_id, environment_id, event_id, received_at
    );
