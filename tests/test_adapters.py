from __future__ import annotations

from lethe_control.adapters import AdapterAction, AdapterCapabilities, AdapterResult
from lethe_control.models import ActionCode, ObjectKind


def test_adapter_contract_records_are_content_free() -> None:
    capability = AdapterCapabilities(
        connector_ref="connector://chroma",
        version="1",
        kinds=(ObjectKind.EMBEDDING,),
    )
    action = AdapterAction(
        action_id="action-1",
        action_code=ActionCode.DELETE,
        target_version_id="version-1",
        kind=ObjectKind.EMBEDDING,
        content_ref="chroma://version-1",
        metadata={"policy_version": 2},
    )
    result = AdapterResult(
        applied=True,
        verified=True,
        result_code="payload_absent",
        metadata={},
    )

    assert capability.supports_read_back is True
    assert action.metadata == {"policy_version": 2}
    assert result.verified is True
