CREATE INDEX propagation_actions_by_run_state
    ON propagation_actions (
        tenant_id, workspace_id, environment_id, run_id, state
    );
