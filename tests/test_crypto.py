from __future__ import annotations

import json

import pytest

from lethe_control.crypto import (
    DEMO_EVENT_PRIVATE_SEED,
    DEMO_RECEIPT_PRIVATE_SEED,
    b64url_decode,
    b64url_encode,
    canonical_content_bytes,
    canonical_embedding_bytes,
    canonical_json_bytes,
    canonical_text_bytes,
    content_fingerprint,
    derive_purpose_key,
    fixture_root,
    load_demo_key_fixture,
    load_key_enrollment,
    private_key_from_seed,
    public_key_bytes,
    receipt_entry_hash,
    sign_acl_policy,
    sign_event,
    sign_receipt_document,
    verify_acl_policy_signature,
    verify_content_fingerprint,
    verify_event_signature,
    verify_receipt_chain,
    verify_receipt_signature,
)


def test_base64url_is_unpadded_and_canonical() -> None:
    encoded = b64url_encode(b"\xfb\xef\xff\x00")
    assert encoded == "--__AA"
    assert b64url_decode(encoded) == b"\xfb\xef\xff\x00"

    for invalid in ("--__AA==", "not+url", "a"):
        with pytest.raises(ValueError, match="base64url"):
            b64url_decode(invalid)


def test_rfc8785_json_canonicalization_is_order_independent() -> None:
    left = {"z": [3, 2, 1], "a": {"b": True, "a": "value"}}
    right = {"a": {"a": "value", "b": True}, "z": [3, 2, 1]}

    assert canonical_json_bytes(left) == canonical_json_bytes(right)
    assert canonical_json_bytes({"b": 1, "a": "x"}) == b'{"a":"x","b":1}'


def test_text_canonicalization_changes_only_line_endings() -> None:
    assert canonical_text_bytes("one\r\ntwo\rthree") == b"one\ntwo\nthree"
    assert canonical_text_bytes("caf\u00e9") != canonical_text_bytes("cafe\u0301")
    with pytest.raises(UnicodeDecodeError):
        canonical_text_bytes(b"\xff")


def test_embedding_canonicalization_binds_metadata_and_float32_values() -> None:
    encoded = canonical_embedding_bytes([1.0, -2.5], model="feature-hash", model_version="1")

    assert encoded == canonical_embedding_bytes(
        [1.0, -2.5], model="feature-hash", model_version="1"
    )
    assert encoded != canonical_embedding_bytes(
        [1.0, -2.5], model="feature-hash", model_version="2"
    )
    with pytest.raises(ValueError, match="finite"):
        canonical_embedding_bytes([float("nan")], model="feature-hash", model_version="1")


def test_fingerprints_bind_tenant_purpose_version_kind_and_payload() -> None:
    master = b"m" * 32
    key_v1 = derive_purpose_key(
        master,
        tenant_id="ten_demo",
        key_version="v1",
        purpose="content-fingerprint",
    )
    key_v2 = derive_purpose_key(
        master,
        tenant_id="ten_demo",
        key_version="v2",
        purpose="content-fingerprint",
    )
    source = content_fingerprint(
        kind="source", key_version="v1", tenant_key=key_v1, payload="line 1\r\nline 2"
    )

    assert source.startswith("hmac-sha256:v1:")
    assert source == content_fingerprint(
        kind="source", key_version="v1", tenant_key=key_v1, payload="line 1\nline 2"
    )
    assert source != content_fingerprint(
        kind="chunk", key_version="v1", tenant_key=key_v1, payload="line 1\nline 2"
    )
    assert source != content_fingerprint(
        kind="source", key_version="v2", tenant_key=key_v2, payload="line 1\nline 2"
    )
    assert verify_content_fingerprint(
        source,
        kind="source",
        key_version="v1",
        tenant_key=key_v1,
        payload="line 1\nline 2",
    )
    assert not verify_content_fingerprint(
        source,
        kind="source",
        key_version="v1",
        tenant_key=key_v1,
        payload="changed",
    )


def test_structured_cache_fingerprint_uses_jcs() -> None:
    key = b"k" * 32
    first = content_fingerprint(
        kind="cache", key_version="v1", tenant_key=key, payload={"answer": "ok", "n": 1}
    )
    second = content_fingerprint(
        kind="cache", key_version="v1", tenant_key=key, payload={"n": 1, "answer": "ok"}
    )
    assert first == second
    assert canonical_content_bytes("cache", {"b": 2, "a": 1}) == b'{"a":1,"b":2}'


def test_event_signature_detects_mutation_and_domain_confusion() -> None:
    event = {"event_id": "evt_1", "event_type": "delete", "source_sequence": 1}
    private_key = private_key_from_seed(DEMO_EVENT_PRIVATE_SEED)
    public_key = public_key_bytes(private_key)
    signature = sign_event(event, private_key)

    assert verify_event_signature(event, signature, public_key)
    assert not verify_event_signature({**event, "source_sequence": 2}, signature, public_key)
    fake_receipt = {**event, "signature_algorithm": "Ed25519", "signature": signature}
    assert not verify_receipt_signature(fake_receipt, public_key)


def test_acl_policy_signature_excludes_signature_and_binds_domain() -> None:
    private_key = private_key_from_seed(DEMO_EVENT_PRIVATE_SEED)
    public_key = public_key_bytes(private_key)
    policy = {
        "acl_ref": "acl://demo/nightjar/1",
        "policy_version": 1,
        "allowed_principal_refs": ["alice", "bob"],
    }
    signature = sign_acl_policy(policy, private_key)
    signed = {**policy, "signature": signature}

    assert verify_acl_policy_signature(signed, public_key)
    assert verify_acl_policy_signature({**signed, "signature": "ignored"}, public_key, signature)
    assert not verify_acl_policy_signature(
        {**signed, "allowed_principal_refs": ["alice"]}, public_key
    )
    assert not verify_event_signature(policy, signature, public_key)


def test_receipt_signature_and_entry_hash_detect_tampering() -> None:
    private_key = private_key_from_seed(DEMO_RECEIPT_PRIVATE_SEED)
    public_key = public_key_bytes(private_key)
    unsigned = {
        "receipt_version": "1",
        "chain_sequence": 1,
        "previous_receipt_hash": None,
        "event_id": "evt_1",
        "counts": {"actions_succeeded": 4},
    }
    signed = sign_receipt_document(unsigned, private_key)

    assert verify_receipt_signature(signed, public_key)
    assert receipt_entry_hash(signed) == receipt_entry_hash(dict(reversed(signed.items())))
    assert not verify_receipt_signature({**signed, "counts": {"actions_succeeded": 3}}, public_key)
    assert receipt_entry_hash({**signed, "signature": "A" * 86}) != receipt_entry_hash(signed)


def _signed_receipt(sequence: int, previous_hash: str | None) -> dict[str, object]:
    return sign_receipt_document(
        {
            "receipt_version": "1",
            "chain_sequence": sequence,
            "previous_receipt_hash": previous_hash,
            "event_id": f"evt_{sequence}",
        },
        DEMO_RECEIPT_PRIVATE_SEED,
    )


def test_receipt_chain_requires_order_sequence_and_trusted_final_head() -> None:
    public_key = public_key_bytes(private_key_from_seed(DEMO_RECEIPT_PRIVATE_SEED))
    first = _signed_receipt(1, None)
    second = _signed_receipt(2, receipt_entry_hash(first))
    expected_head = receipt_entry_hash(second)

    assert verify_receipt_chain(
        [first, second],
        public_key,
        expected_start_sequence=1,
        trusted_previous_hash=None,
        expected_final_hash=expected_head,
    ).valid
    assert not verify_receipt_chain(
        [second, first],
        public_key,
        expected_start_sequence=1,
        trusted_previous_hash=None,
        expected_final_hash=expected_head,
    ).valid
    assert not verify_receipt_chain(
        [first],
        public_key,
        expected_start_sequence=1,
        trusted_previous_hash=None,
        expected_final_hash=expected_head,
    ).valid
    assert not verify_receipt_chain(
        [second],
        public_key,
        expected_start_sequence=1,
        trusted_previous_hash=None,
        expected_final_hash=expected_head,
    ).valid


def test_demo_keys_enrollments_and_static_events_are_self_consistent() -> None:
    event_key = load_demo_key_fixture("event_issuer")
    enrollment = load_key_enrollment(event_key.key_id)

    assert event_key.private_seed == DEMO_EVENT_PRIVATE_SEED
    assert event_key.public_key == enrollment.public_key
    assert enrollment.allowed_roles == ("event_issuer",)

    data = json.loads((fixture_root() / "demo-events.json").read_text(encoding="utf-8"))
    assert len(data["events"]) == 4
    for fixture in data["events"]:
        assert verify_event_signature(fixture["event"], fixture["signature"], event_key.public_key)


def test_key_loaders_reject_unknown_roles() -> None:
    with pytest.raises(KeyError, match="unknown demo key role"):
        load_demo_key_fixture("production")
    with pytest.raises(KeyError, match="unknown enrollment key id"):
        load_key_enrollment("missing")
