"""Outbound metadata allowlist.

The SaaS control plane receives opaque identifiers, kinds, lifecycle states,
counts, timestamps, versions, policy and evidence hashes, signed receipts,
and machine-readable reason codes — never content, chunks, embeddings,
prompts, answers, raw paths or URLs, credentials, or ACL member identities.
This validator runs on the data-plane agent BEFORE signing or upload, and
again defensively on the control-plane ingest path.

Allowed keys are derived from the contract models' JSON schemas so the
allowlist cannot drift from the models; deny rules then reject sensitive
key names and value shapes even if a schema'd field were misused.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from lethe_control.models import ActionState, ExecutionReceipt, RunStatusResponse

_DENIED_KEY_FRAGMENTS = (
    "payload",
    "content",
    "embedding",
    "prompt",
    "answer",
    "path",
    "url",
    "credential",
    "principal",
    "token",
    "secret",
    "password",
    "member",
)

_ALLOWED_REF_SCHEMES = (
    "connector://",
    "store://",
    "acl://",
    "policy://",
    "authority://",
    "actor://",
    "purpose://",
)

_URL_VALUE = re.compile(r"^https?://", re.IGNORECASE)
_FILESYSTEM_VALUE = re.compile(r"^(/|~|\./|\.\./)")
_RAW_HEX = re.compile(r"^[0-9a-fA-F]{32,}$")

_HASH_KEY_SUFFIXES = ("_hash", "signature", "_head")


def _schema_keys(model: type[Any]) -> frozenset[str]:
    schema = model.model_json_schema(by_alias=True)
    keys: set[str] = set()

    def collect(node: Any) -> None:
        if isinstance(node, dict):
            for name, subschema in node.get("properties", {}).items():
                keys.add(name)
                collect(subschema)
            for value in node.get("$defs", {}).values():
                collect(value)
            for nested in ("items", "additionalProperties"):
                if isinstance(node.get(nested), dict):
                    collect(node[nested])
            for union_key in ("anyOf", "oneOf", "allOf"):
                for member in node.get(union_key, []) or []:
                    collect(member)

    collect(schema)
    return frozenset(keys)


# Dynamic count keys are enum values, not schema property names.
_ACTION_STATE_KEYS = frozenset(state.value for state in ActionState)

DOCUMENT_TYPES: dict[str, frozenset[str]] = {
    "receipt": _schema_keys(ExecutionReceipt),
    "run_status": _schema_keys(RunStatusResponse) | _ACTION_STATE_KEYS,
}


class OutboundViolation(RuntimeError):
    def __init__(self, violations: list[str]) -> None:
        super().__init__("; ".join(violations))
        self.violations = violations


def _is_hash_key(key: str) -> bool:
    return key.endswith(_HASH_KEY_SUFFIXES)


def _check_value(key: str, value: Any, location: str, violations: list[str]) -> None:
    if not isinstance(value, str):
        return
    if _URL_VALUE.match(value):
        violations.append(f"url_value:{location}")
        return
    if _FILESYSTEM_VALUE.match(value):
        violations.append(f"filesystem_value:{location}")
        return
    if "://" in value and not value.startswith(_ALLOWED_REF_SCHEMES):
        violations.append(f"unknown_ref_scheme:{location}")
        return
    if _RAW_HEX.match(value) and not _is_hash_key(key):
        violations.append(f"raw_digest_outside_hash_field:{location}")


def _walk(node: Any, allowed_keys: frozenset[str], location: str, violations: list[str]) -> None:
    if isinstance(node, Mapping):
        for key, value in node.items():
            key_location = f"{location}.{key}" if location else str(key)
            lowered = str(key).lower()
            if any(fragment in lowered for fragment in _DENIED_KEY_FRAGMENTS):
                violations.append(f"denied_key:{key_location}")
                continue
            if key not in allowed_keys:
                violations.append(f"unallowed_key:{key_location}")
                continue
            _check_value(str(key), value, key_location, violations)
            _walk(value, allowed_keys, key_location, violations)
    elif isinstance(node, list):
        for index, item in enumerate(node):
            _walk(item, allowed_keys, f"{location}[{index}]", violations)


def validate_outbound(document_type: str, document: Mapping[str, Any]) -> list[str]:
    allowed = DOCUMENT_TYPES.get(document_type)
    if allowed is None:
        return [f"unknown_document_type:{document_type}"]
    violations: list[str] = []
    _walk(document, allowed, "", violations)
    return violations


def assert_outbound_safe(document_type: str, document: Mapping[str, Any]) -> None:
    violations = validate_outbound(document_type, document)
    if violations:
        raise OutboundViolation(violations)
