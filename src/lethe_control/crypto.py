"""Canonicalization, fingerprints, and DEMO-ONLY Ed25519 helpers.

These primitives prove byte and signer integrity inside declared demo scope. They do
not prove lineage completeness, secure key custody, or universal deletion.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import math
import re
import struct
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import rfc8785
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

FINGERPRINT_DOMAIN = b"LETHE-FINGERPRINT-V1"
KEY_DERIVATION_DOMAIN = b"LETHE-PURPOSE-KEY-V1"
EVENT_SIGNATURE_DOMAIN = b"LETHE-LIFECYCLE-EVENT-V1"
ACL_POLICY_SIGNATURE_DOMAIN = b"LETHE-ACL-POLICY-V1"
RECEIPT_SIGNATURE_DOMAIN = b"LETHE-EXECUTION-RECEIPT-V1"
RECEIPT_ENTRY_HASH_DOMAIN = b"LETHE-EXECUTION-RECEIPT-ENTRY-HASH-V1"
EMBEDDING_PAYLOAD_DOMAIN = b"LETHE-EMBEDDING-PAYLOAD-V1"

DEMO_EVENT_PRIVATE_SEED = b"\x11" * 32
DEMO_RECEIPT_PRIVATE_SEED = b"\x22" * 32
DEMO_APPROVAL_PRIVATE_SEED = b"\x33" * 32
DEMO_TENANT_MASTER_KEY = b"\x44" * 32

_B64URL_RE = re.compile(r"^[A-Za-z0-9_-]*$")
_TEXT_KINDS = frozenset({"source", "chunk", "summary", "memory"})


def b64url_encode(value: bytes) -> str:
    """Encode bytes as unpadded RFC 4648 base64url."""

    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def b64url_decode(value: str) -> bytes:
    """Decode canonical, unpadded base64url and reject ambiguous encodings."""

    if "=" in value or not _B64URL_RE.fullmatch(value) or len(value) % 4 == 1:
        raise ValueError("invalid unpadded base64url")
    try:
        decoded = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("invalid unpadded base64url") from exc
    if b64url_encode(decoded) != value:
        raise ValueError("non-canonical base64url")
    return decoded


def canonical_json_bytes(value: Any) -> bytes:
    """Serialize JSON-compatible input with RFC 8785 JCS."""

    return rfc8785.dumps(value)


def canonical_text_bytes(value: str | bytes) -> bytes:
    """Return valid UTF-8 with LF line endings and no Unicode normalization."""

    text = value.decode("utf-8", errors="strict") if isinstance(value, bytes) else value
    return text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")


def canonical_embedding_bytes(values: Sequence[float], *, model: str, model_version: str) -> bytes:
    """Encode model identity, dimensions, and finite little-endian float32 values."""

    model_bytes = model.encode("utf-8")
    version_bytes = model_version.encode("utf-8")
    if not model_bytes or not version_bytes:
        raise ValueError("embedding model and version must be non-empty")
    if len(model_bytes) > 2**32 - 1 or len(version_bytes) > 2**32 - 1:
        raise ValueError("embedding model metadata is too large")
    if len(values) > 2**32 - 1:
        raise ValueError("embedding dimensions exceed uint32")

    encoded_values = bytearray()
    for value in values:
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("embedding values must be finite")
        try:
            encoded_values.extend(struct.pack("<f", number))
        except OverflowError as exc:
            raise ValueError("embedding value is outside float32 range") from exc

    return b"".join(
        (
            EMBEDDING_PAYLOAD_DOMAIN,
            b"\x00",
            struct.pack("<I", len(model_bytes)),
            model_bytes,
            struct.pack("<I", len(version_bytes)),
            version_bytes,
            struct.pack("<I", len(values)),
            bytes(encoded_values),
        )
    )


def canonical_content_bytes(
    kind: str,
    payload: Any,
    *,
    embedding_model: str | None = None,
    embedding_version: str | None = None,
) -> bytes:
    """Canonicalize supported KnowledgeEnvelope payload kinds."""

    if kind in _TEXT_KINDS:
        if not isinstance(payload, (str, bytes)):
            raise TypeError(f"{kind} payload must be str or bytes")
        return canonical_text_bytes(payload)
    if kind == "cache":
        return canonical_json_bytes(payload)
    if kind == "embedding":
        if embedding_model is None or embedding_version is None:
            raise ValueError("embedding fingerprints require model and version")
        if isinstance(payload, (str, bytes)) or not isinstance(payload, Sequence):
            raise TypeError("embedding payload must be a sequence of floats")
        return canonical_embedding_bytes(
            cast(Sequence[float], payload),
            model=embedding_model,
            model_version=embedding_version,
        )
    raise ValueError(f"unsupported derivative kind: {kind}")


def derive_purpose_key(
    tenant_master_key: bytes, *, tenant_id: str, key_version: str, purpose: str
) -> bytes:
    """Derive a tenant/version/purpose-separated 256-bit key with HMAC-SHA-256."""

    if len(tenant_master_key) < 32:
        raise ValueError("tenant master key must be at least 32 bytes")
    parts = (tenant_id, key_version, purpose)
    if any(not part or "\x00" in part for part in parts):
        raise ValueError("key context values must be non-empty and contain no NUL")
    message = KEY_DERIVATION_DOMAIN + b"\x00" + b"\x00".join(part.encode("utf-8") for part in parts)
    return hmac.new(tenant_master_key, message, hashlib.sha256).digest()


def content_fingerprint(
    *,
    kind: str,
    key_version: str,
    tenant_key: bytes,
    payload: Any,
    embedding_model: str | None = None,
    embedding_version: str | None = None,
) -> str:
    """Create version-labelled HMAC-SHA-256 fingerprint for one payload kind.

    ``tenant_key`` must already be tenant/version/purpose separated. Use
    :func:`derive_purpose_key` with purpose ``content-fingerprint``.
    """

    if not key_version or ":" in key_version:
        raise ValueError("key version must be non-empty and contain no colon")
    if not kind or "\x00" in kind:
        raise ValueError("kind must be non-empty and contain no NUL")
    if len(tenant_key) < 32:
        raise ValueError("tenant fingerprint key must be at least 32 bytes")
    canonical_payload = canonical_content_bytes(
        kind,
        payload,
        embedding_model=embedding_model,
        embedding_version=embedding_version,
    )
    message = FINGERPRINT_DOMAIN + b"\x00" + kind.encode("utf-8") + b"\x00" + canonical_payload
    digest = hmac.new(tenant_key, message, hashlib.sha256).digest()
    return f"hmac-sha256:{key_version}:{b64url_encode(digest)}"


def verify_content_fingerprint(
    fingerprint: str,
    *,
    kind: str,
    key_version: str,
    tenant_key: bytes,
    payload: Any,
    embedding_model: str | None = None,
    embedding_version: str | None = None,
) -> bool:
    """Compare a labelled content fingerprint in constant time."""

    expected = content_fingerprint(
        kind=kind,
        key_version=key_version,
        tenant_key=tenant_key,
        payload=payload,
        embedding_model=embedding_model,
        embedding_version=embedding_version,
    )
    return hmac.compare_digest(fingerprint, expected)


def private_key_from_seed(seed: bytes) -> Ed25519PrivateKey:
    if len(seed) != 32:
        raise ValueError("Ed25519 private seed must contain 32 bytes")
    return Ed25519PrivateKey.from_private_bytes(seed)


def public_key_bytes(key: Ed25519PrivateKey | Ed25519PublicKey) -> bytes:
    public_key = key.public_key() if isinstance(key, Ed25519PrivateKey) else key
    return public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )


def _coerce_private_key(key: bytes | Ed25519PrivateKey) -> Ed25519PrivateKey:
    return private_key_from_seed(key) if isinstance(key, bytes) else key


def _coerce_public_key(key: bytes | Ed25519PublicKey) -> Ed25519PublicKey:
    if isinstance(key, bytes):
        if len(key) != 32:
            raise ValueError("Ed25519 public key must contain 32 bytes")
        return Ed25519PublicKey.from_public_bytes(key)
    return key


def _domain_payload(domain: bytes, payload: Mapping[str, Any]) -> bytes:
    return domain + b"\x00" + canonical_json_bytes(dict(payload))


def sign_event(event: Mapping[str, Any], private_key: bytes | Ed25519PrivateKey) -> str:
    """Sign LifecycleEvent JSON for the X-Lethe-Event-Signature header."""

    signature = _coerce_private_key(private_key).sign(
        _domain_payload(EVENT_SIGNATURE_DOMAIN, event)
    )
    return b64url_encode(signature)


def verify_event_signature(
    event: Mapping[str, Any],
    signature: str,
    public_key: bytes | Ed25519PublicKey,
) -> bool:
    """Verify a domain-separated LifecycleEvent signature."""

    return _verify_domain_signature(EVENT_SIGNATURE_DOMAIN, event, signature, public_key)


def _unsigned_document(document: Mapping[str, Any]) -> dict[str, Any]:
    unsigned = dict(document)
    unsigned.pop("signature", None)
    return unsigned


def sign_acl_policy(policy: Mapping[str, Any], private_key: bytes | Ed25519PrivateKey) -> str:
    """Sign canonical ACL policy JSON without its top-level signature field."""

    signature = _coerce_private_key(private_key).sign(
        _domain_payload(ACL_POLICY_SIGNATURE_DOMAIN, _unsigned_document(policy))
    )
    return b64url_encode(signature)


def verify_acl_policy_signature(
    policy: Mapping[str, Any],
    public_key: bytes | Ed25519PublicKey,
    signature: str | None = None,
) -> bool:
    """Verify fixed-domain ACL policy signature."""

    claimed_signature = signature if signature is not None else policy.get("signature")
    if not isinstance(claimed_signature, str):
        return False
    return _verify_domain_signature(
        ACL_POLICY_SIGNATURE_DOMAIN,
        _unsigned_document(policy),
        claimed_signature,
        public_key,
    )


def _unsigned_receipt(receipt: Mapping[str, Any]) -> dict[str, Any]:
    return _unsigned_document(receipt)


def sign_receipt(receipt: Mapping[str, Any], private_key: bytes | Ed25519PrivateKey) -> str:
    """Sign receipt canonical JSON without its top-level signature field."""

    signature = _coerce_private_key(private_key).sign(
        _domain_payload(RECEIPT_SIGNATURE_DOMAIN, _unsigned_receipt(receipt))
    )
    return b64url_encode(signature)


def sign_receipt_document(
    receipt: Mapping[str, Any], private_key: bytes | Ed25519PrivateKey
) -> dict[str, Any]:
    """Return a receipt copy with fixed algorithm and signature fields."""

    signed = _unsigned_receipt(receipt)
    signed["signature_algorithm"] = "Ed25519"
    signed["signature"] = sign_receipt(signed, private_key)
    return signed


def verify_receipt_signature(
    receipt: Mapping[str, Any],
    public_key: bytes | Ed25519PublicKey,
    signature: str | None = None,
) -> bool:
    """Verify receipt signature and its declared algorithm."""

    if receipt.get("signature_algorithm") != "Ed25519":
        return False
    claimed_signature = signature if signature is not None else receipt.get("signature")
    if not isinstance(claimed_signature, str):
        return False
    return _verify_domain_signature(
        RECEIPT_SIGNATURE_DOMAIN,
        _unsigned_receipt(receipt),
        claimed_signature,
        public_key,
    )


def _verify_domain_signature(
    domain: bytes,
    payload: Mapping[str, Any],
    signature: str,
    public_key: bytes | Ed25519PublicKey,
) -> bool:
    try:
        decoded = b64url_decode(signature)
        if len(decoded) != 64:
            return False
        _coerce_public_key(public_key).verify(decoded, _domain_payload(domain, payload))
    except (InvalidSignature, TypeError, ValueError):
        return False
    return True


def receipt_entry_hash(receipt: Mapping[str, Any]) -> str:
    """Hash canonical signed receipt as one chain entry."""

    if not isinstance(receipt.get("signature"), str):
        raise ValueError("receipt entry hash requires a signed receipt")
    digest = hashlib.sha256(
        RECEIPT_ENTRY_HASH_DOMAIN + b"\x00" + canonical_json_bytes(dict(receipt))
    ).digest()
    return b64url_encode(digest)


@dataclass(frozen=True, slots=True)
class ChainVerification:
    valid: bool
    final_hash: str | None
    error: str | None = None


def verify_receipt_chain(
    receipts: Sequence[Mapping[str, Any]],
    public_key: bytes | Ed25519PublicKey,
    *,
    expected_start_sequence: int,
    trusted_previous_hash: str | None,
    expected_final_hash: str | None,
) -> ChainVerification:
    """Verify one ordered single-key receipt bundle against trusted endpoints."""

    current_hash = trusted_previous_hash
    expected_sequence = expected_start_sequence
    for receipt in receipts:
        if receipt.get("chain_sequence") != expected_sequence:
            return ChainVerification(False, current_hash, "unexpected chain sequence")
        if receipt.get("previous_receipt_hash") != current_hash:
            return ChainVerification(False, current_hash, "previous receipt hash mismatch")
        if not verify_receipt_signature(receipt, public_key):
            return ChainVerification(False, current_hash, "invalid receipt signature")
        current_hash = receipt_entry_hash(receipt)
        expected_sequence += 1
    if current_hash is None or expected_final_hash is None:
        final_hash_matches = current_hash is expected_final_hash
    else:
        final_hash_matches = hmac.compare_digest(current_hash, expected_final_hash)
    if not final_hash_matches:
        return ChainVerification(False, current_hash, "final trusted head mismatch")
    return ChainVerification(True, current_hash)


@dataclass(frozen=True, slots=True)
class DemoKeyFixture:
    role: str
    key_id: str
    private_seed: bytes
    public_key: bytes


@dataclass(frozen=True, slots=True)
class KeyEnrollment:
    key_id: str
    public_key: bytes
    status: str
    valid_from: str
    valid_until: str
    tenant_id: str
    workspace_id: str
    environment_id: str
    agent_id: str
    allowed_roles: tuple[str, ...]


def fixture_root() -> Path:
    return Path(__file__).resolve().parents[2] / "fixtures"


def load_demo_key_fixture(role: str, path: Path | None = None) -> DemoKeyFixture:
    """Load a clearly non-production deterministic key seed by fixture role."""

    fixture_path = path or fixture_root() / "keys" / "demo_keys.json"
    data = json.loads(fixture_path.read_text(encoding="utf-8"))
    entries = data.get("keys")
    if not isinstance(entries, list):
        raise ValueError("demo key fixture must contain a keys list")
    for entry in entries:
        if isinstance(entry, dict) and entry.get("role") == role:
            private_seed_value = entry.get("private_seed_base64url")
            public_key_value = entry.get("public_key_base64url")
            key_id = entry.get("key_id")
            if not all(
                isinstance(value, str) for value in (private_seed_value, public_key_value, key_id)
            ):
                raise ValueError("malformed demo key fixture")
            private_seed = b64url_decode(cast(str, private_seed_value))
            public_key = b64url_decode(cast(str, public_key_value))
            derived_public = public_key_bytes(private_key_from_seed(private_seed))
            if not hmac.compare_digest(public_key, derived_public):
                raise ValueError("demo public key does not match private seed")
            return DemoKeyFixture(role, cast(str, key_id), private_seed, public_key)
    raise KeyError(f"unknown demo key role: {role}")


def load_key_enrollment(key_id: str, path: Path | None = None) -> KeyEnrollment:
    fixture_path = path or fixture_root() / "keys" / "enrollments.json"
    data = json.loads(fixture_path.read_text(encoding="utf-8"))
    entries = data.get("enrollments")
    if not isinstance(entries, list):
        raise ValueError("enrollment fixture must contain an enrollments list")
    for entry in entries:
        if isinstance(entry, dict) and entry.get("key_id") == key_id:
            required = {
                name: entry.get(name)
                for name in (
                    "key_id",
                    "public_key_base64url",
                    "status",
                    "valid_from",
                    "valid_until",
                    "tenant_id",
                    "workspace_id",
                    "environment_id",
                    "agent_id",
                )
            }
            if not all(isinstance(value, str) for value in required.values()):
                raise ValueError("malformed key enrollment")
            allowed_roles = entry.get("allowed_roles")
            if not isinstance(allowed_roles, list) or not all(
                isinstance(value, str) for value in allowed_roles
            ):
                raise ValueError("malformed key enrollment roles")
            return KeyEnrollment(
                key_id=cast(str, required["key_id"]),
                public_key=b64url_decode(cast(str, required["public_key_base64url"])),
                status=cast(str, required["status"]),
                valid_from=cast(str, required["valid_from"]),
                valid_until=cast(str, required["valid_until"]),
                tenant_id=cast(str, required["tenant_id"]),
                workspace_id=cast(str, required["workspace_id"]),
                environment_id=cast(str, required["environment_id"]),
                agent_id=cast(str, required["agent_id"]),
                allowed_roles=tuple(cast(list[str], allowed_roles)),
            )
    raise KeyError(f"unknown enrollment key id: {key_id}")
