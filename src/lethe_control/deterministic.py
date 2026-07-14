"""Deterministic content derivation and frozen fixture utilities."""

from __future__ import annotations

import base64
import hashlib
import json
import math
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from lethe_control.crypto import canonical_json_bytes, canonical_text_bytes, fixture_root

STABLE_ID_DOMAIN = b"LETHE-STABLE-ID-V1"
FEATURE_HASH_DOMAIN = b"LETHE-SIGNED-FEATURE-HASH-V1"
FEATURE_HASH_VERSION = "signed-feature-hash-v1"
DEFAULT_EMBEDDING_DIMENSIONS = 64
DEFAULT_CHUNK_SIZE = 240
DEFAULT_CHUNK_OVERLAP = 40

_ID_PREFIX_RE = re.compile(r"^[a-z][a-z0-9_]{0,15}$")
_WORD_RE = re.compile(r"[^\W_]+(?:['-][^\W_]+)*", flags=re.UNICODE)
_SENTENCE_BOUNDARY_RE = re.compile(r"(?<=[.!?])\s+")
_FACT_PREFIXES = ("FACT:", "PREFERENCE:")
_PROBE_NAMES = frozenset({"prohibited", "unrelated"})


def _stable_part_bytes(part: str | bytes | int) -> bytes:
    if isinstance(part, bytes):
        return part
    if isinstance(part, int):
        return str(part).encode("ascii")
    return part.encode("utf-8")


def stable_id(prefix: str, *parts: str | bytes | int, length: int = 26) -> str:
    """Create opaque stable ID from length-prefixed components."""

    if not _ID_PREFIX_RE.fullmatch(prefix):
        raise ValueError("stable ID prefix must be lowercase ASCII identifier")
    if not parts:
        raise ValueError("stable ID requires at least one component")
    if not 8 <= length <= 52:
        raise ValueError("stable ID digest length must be between 8 and 52")

    material = bytearray(STABLE_ID_DOMAIN + b"\x00")
    for part in parts:
        encoded = _stable_part_bytes(part)
        if len(encoded) > 2**32 - 1:
            raise ValueError("stable ID component exceeds uint32 length")
        material.extend(len(encoded).to_bytes(4, "big"))
        material.extend(encoded)
    digest = hashlib.sha256(material).digest()
    token = base64.b32encode(digest).rstrip(b"=").decode("ascii").lower()
    return f"{prefix}_{token[:length]}"


@dataclass(frozen=True, slots=True)
class Chunk:
    source_version_id: str
    ordinal: int
    start: int
    end: int
    text: str
    object_id: str
    version_id: str


def chunk_text(
    source_version_id: str,
    text: str | bytes,
    *,
    size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> tuple[Chunk, ...]:
    """Split LF-canonical text by Unicode code points with stable ordinal IDs."""

    if not source_version_id:
        raise ValueError("source version ID must be non-empty")
    if size <= 0:
        raise ValueError("chunk size must be positive")
    if overlap < 0 or overlap >= size:
        raise ValueError("chunk overlap must be non-negative and smaller than size")

    canonical = canonical_text_bytes(text).decode("utf-8")
    if not canonical:
        return ()

    step = size - overlap
    chunks: list[Chunk] = []
    start = 0
    ordinal = 0
    while start < len(canonical):
        end = min(start + size, len(canonical))
        chunk = canonical[start:end]
        chunks.append(
            Chunk(
                source_version_id=source_version_id,
                ordinal=ordinal,
                start=start,
                end=end,
                text=chunk,
                object_id=stable_id("obj", source_version_id, "chunk", ordinal),
                version_id=stable_id("ver", source_version_id, "chunk", ordinal),
            )
        )
        if end == len(canonical):
            break
        start += step
        ordinal += 1
    return tuple(chunks)


def tokenize(text: str | bytes) -> tuple[str, ...]:
    canonical = canonical_text_bytes(text).decode("utf-8")
    return tuple(match.group(0).casefold() for match in _WORD_RE.finditer(canonical))


def feature_hash_embedding(
    text: str | bytes, *, dimensions: int = DEFAULT_EMBEDDING_DIMENSIONS
) -> tuple[float, ...]:
    """Create L2-normalized signed feature-hash vector from unigrams and bigrams."""

    if dimensions <= 0 or dimensions > 4096:
        raise ValueError("embedding dimensions must be between 1 and 4096")
    tokens = tokenize(text)
    features = list(tokens)
    features.extend(f"{left}\x1f{right}" for left, right in zip(tokens, tokens[1:], strict=False))
    vector = [0.0] * dimensions
    for feature in features:
        digest = hashlib.sha256(FEATURE_HASH_DOMAIN + b"\x00" + feature.encode("utf-8")).digest()
        index = int.from_bytes(digest[:8], "big") % dimensions
        vector[index] += -1.0 if digest[8] & 1 else 1.0
    magnitude = math.sqrt(sum(value * value for value in vector))
    if magnitude == 0.0:
        return tuple(vector)
    return tuple(value / magnitude for value in vector)


def exact_score(left: str | bytes, right: str | bytes) -> float:
    """Return 1 only for identical canonical UTF-8 payload bytes."""

    return float(canonical_text_bytes(left) == canonical_text_bytes(right))


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right):
        raise ValueError("vectors must have equal dimensions")
    if not left:
        raise ValueError("vectors must be non-empty")
    left_norm = math.sqrt(sum(float(value) ** 2 for value in left))
    right_norm = math.sqrt(sum(float(value) ** 2 for value in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    score = sum(float(a) * float(b) for a, b in zip(left, right, strict=True)) / (
        left_norm * right_norm
    )
    return max(-1.0, min(1.0, score))


def semantic_score(
    left: str | bytes,
    right: str | bytes,
    *,
    dimensions: int = DEFAULT_EMBEDDING_DIMENSIONS,
) -> float:
    return cosine_similarity(
        feature_hash_embedding(left, dimensions=dimensions),
        feature_hash_embedding(right, dimensions=dimensions),
    )


def _clean_lines(text: str | bytes) -> tuple[str, ...]:
    canonical = canonical_text_bytes(text).decode("utf-8")
    lines: list[str] = []
    for raw_line in canonical.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("<!--"):
            continue
        line = line.lstrip("#").strip()
        if line:
            lines.append(line)
    return tuple(lines)


def _sentences(text: str | bytes) -> tuple[str, ...]:
    sentences: list[str] = []
    for line in _clean_lines(text):
        if line.startswith(_FACT_PREFIXES):
            line = line.split(":", 1)[1].strip()
        sentences.extend(part.strip() for part in _SENTENCE_BOUNDARY_RE.split(line) if part.strip())
    return tuple(sentences)


def summarize_text(text: str | bytes, *, max_sentences: int = 3) -> str:
    """Extract declared facts first, then earliest unique sentences."""

    if max_sentences <= 0:
        raise ValueError("max sentences must be positive")
    lines = _clean_lines(text)
    candidates = [
        line.split(":", 1)[1].strip() for line in lines if line.startswith(_FACT_PREFIXES)
    ]
    candidates.extend(_sentences(text))
    selected: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = candidate.casefold()
        if candidate and key not in seen:
            selected.append(candidate)
            seen.add(key)
        if len(selected) == max_sentences:
            break
    return " ".join(selected)


def memory_from_text(text: str | bytes, *, max_records: int = 8) -> tuple[str, ...]:
    """Extract explicit fixture FACT and PREFERENCE rows in source order."""

    if max_records <= 0:
        raise ValueError("max records must be positive")
    records: list[str] = []
    seen: set[str] = set()
    for line in _clean_lines(text):
        if not line.startswith(_FACT_PREFIXES):
            continue
        value = line.split(":", 1)[1].strip()
        key = value.casefold()
        if value and key not in seen:
            records.append(value)
            seen.add(key)
        if len(records) == max_records:
            break
    return tuple(records)


def answer_from_contexts(query: str | bytes, contexts: Sequence[str | bytes]) -> str:
    """Return highest-overlap context sentence; empty retrieval discloses nothing."""

    if not contexts:
        return "No authorized context available."
    query_tokens = set(tokenize(query))
    candidates: list[tuple[float, int, int, str]] = []
    for context_index, context in enumerate(contexts):
        for sentence_index, sentence in enumerate(_sentences(context)):
            sentence_tokens = set(tokenize(sentence))
            overlap = len(query_tokens & sentence_tokens)
            denominator = max(1, len(query_tokens | sentence_tokens))
            score = overlap / denominator
            candidates.append((score, -context_index, -sentence_index, sentence))
    if not candidates:
        return "No matching authorized context."
    score, _, _, sentence = max(candidates)
    if score == 0.0:
        return "No matching authorized context."
    return f"Based on authorized context: {sentence}"


def _safe_asset_path(root: Path, relative_path: str) -> Path:
    if not relative_path or Path(relative_path).is_absolute():
        raise ValueError("fixture asset path must be relative")
    resolved_root = root.resolve()
    resolved = (root / relative_path).resolve()
    if not resolved.is_relative_to(resolved_root):
        raise ValueError("fixture asset escapes fixture root")
    return resolved


def asset_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_fixture_manifest(
    path: Path | None = None, *, verify_assets: bool = True
) -> dict[str, Any]:
    """Load manifest and optionally enforce every frozen asset digest."""

    manifest_path = path or fixture_root() / "manifest.json"
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema_version") != "1":
        raise ValueError("unsupported fixture manifest")
    if data.get("frozen") is not True:
        raise ValueError("fixture manifest must be marked frozen")
    assets = data.get("assets")
    if not isinstance(assets, list) or not assets:
        raise ValueError("fixture manifest must declare assets")
    if verify_assets:
        for asset in assets:
            if not isinstance(asset, dict):
                raise ValueError("malformed fixture asset entry")
            relative_path = asset.get("path")
            expected_hash = asset.get("sha256")
            if not isinstance(relative_path, str) or not isinstance(expected_hash, str):
                raise ValueError("malformed fixture asset entry")
            actual_hash = asset_sha256(_safe_asset_path(manifest_path.parent, relative_path))
            if actual_hash != expected_hash:
                raise ValueError(f"fixture asset hash mismatch: {relative_path}")
    return cast(dict[str, Any], data)


def fixture_manifest_hash(manifest: Mapping[str, Any] | None = None) -> str:
    loaded = dict(manifest) if manifest is not None else load_fixture_manifest()
    return hashlib.sha256(canonical_json_bytes(loaded)).hexdigest()


def load_probe_suite(name: str, root: Path | None = None) -> tuple[str, ...]:
    if name not in _PROBE_NAMES:
        raise ValueError(f"unknown probe suite: {name}")
    base = root or fixture_root()
    data = json.loads((base / "probes" / f"{name}.json").read_text(encoding="utf-8"))
    queries = data.get("queries") if isinstance(data, dict) else None
    if not isinstance(queries, list) or not all(isinstance(query, str) for query in queries):
        raise ValueError(f"malformed {name} probe suite")
    typed_queries = cast(list[str], queries)
    if len(typed_queries) != 100 or len(set(typed_queries)) != 100:
        raise ValueError(f"{name} probe suite must contain 100 unique queries")
    return tuple(typed_queries)


def rank_contexts(query: str | bytes, contexts: Iterable[str | bytes]) -> tuple[int, ...]:
    """Return input indexes ordered by deterministic semantic score, then input order."""

    scored = [
        (semantic_score(query, context), -index, index) for index, context in enumerate(contexts)
    ]
    scored.sort(reverse=True)
    return tuple(index for _, _, index in scored)
