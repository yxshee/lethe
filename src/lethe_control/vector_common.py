"""Shared scope hygiene for vector store backends.

Both backends must clean metadata identically so payload shapes, policy
bindings, and lifecycle filters stay interchangeable in evidence and
conformance tests.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

UNBOUNDED_EXPIRY_EPOCH = 253_402_300_799.0

# Binding hashes must agree across backends; the domain string predates the
# second backend and stays fixed for compatibility with persisted state.
_POLICY_BINDING_DOMAIN = b"LETHE-CHROMA-POLICY-BINDING-V1\0"


def length_prefixed(value: str) -> bytes:
    encoded = value.encode("utf-8")
    return len(encoded).to_bytes(4, "big") + encoded


def scoped_digest(
    domain: bytes,
    *,
    tenant_id: str,
    workspace_id: str,
    environment_id: str,
    version_id: str,
) -> bytes:
    """Collision-resistant digest binding a version to its full logical scope."""

    digest = hashlib.sha256(domain)
    for value in (tenant_id, workspace_id, environment_id, version_id):
        digest.update(length_prefixed(value))
    return digest.digest()


def policy_binding(acl_ref: str, policy_version: int) -> str:
    digest = hashlib.sha256(_POLICY_BINDING_DOMAIN)
    digest.update(length_prefixed(acl_ref))
    digest.update(policy_version.to_bytes(8, "big", signed=False))
    return f"pol_{digest.hexdigest()}"


def expiry_epoch(value: Any) -> float:
    if value in (None, ""):
        return UNBOUNDED_EXPIRY_EPOCH
    if isinstance(value, bool):
        raise ValueError("valid_until_epoch must be a timestamp")
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        raise ValueError("valid_until must be an RFC 3339 timestamp")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("valid_until must include a timezone")
    return parsed.astimezone(UTC).timestamp()


def clean_metadata(
    *,
    tenant_id: str,
    workspace_id: str,
    environment_id: str,
    version_id: str,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    clean = dict(metadata)
    expected_scope = {
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "environment_id": environment_id,
        "version_id": version_id,
    }
    for key, expected in expected_scope.items():
        supplied = clean.get(key)
        if supplied is not None and supplied != expected:
            raise ValueError(f"metadata {key} does not match the scoped vector key")
        clean[key] = expected

    roots = clean.get("root_version_ids")
    if isinstance(roots, list):
        clean["root_version_ids"] = json.dumps(roots, separators=(",", ":"))

    clean["lifecycle_state"] = str(clean.get("lifecycle_state", "active"))
    clean["valid_until_epoch"] = expiry_epoch(
        clean.get("valid_until_epoch", clean.get("valid_until"))
    )
    acl_ref = clean.get("acl_ref")
    policy_version = clean.get("policy_version")
    if (
        isinstance(acl_ref, str)
        and isinstance(policy_version, int)
        and not isinstance(policy_version, bool)
    ):
        clean["policy_binding"] = policy_binding(acl_ref, policy_version)

    return {key: value for key, value in clean.items() if value is not None}
