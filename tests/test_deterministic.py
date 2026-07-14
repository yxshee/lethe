from __future__ import annotations

import json
import math
import shutil
from pathlib import Path

import pytest

from lethe_control.crypto import fixture_root
from lethe_control.deterministic import (
    DEFAULT_EMBEDDING_DIMENSIONS,
    answer_from_contexts,
    chunk_text,
    cosine_similarity,
    exact_score,
    feature_hash_embedding,
    fixture_manifest_hash,
    load_fixture_manifest,
    load_probe_suite,
    memory_from_text,
    rank_contexts,
    semantic_score,
    stable_id,
    summarize_text,
)


def test_stable_ids_are_deterministic_and_component_safe() -> None:
    assert stable_id("ver", "source", 1) == stable_id("ver", "source", 1)
    assert stable_id("ver", "ab", "c") != stable_id("ver", "a", "bc")
    assert stable_id("obj", "source", 1).startswith("obj_")

    with pytest.raises(ValueError, match="prefix"):
        stable_id("Bad", "source")
    with pytest.raises(ValueError, match="component"):
        stable_id("ver")


def test_chunker_uses_lf_text_fixed_windows_overlap_and_stable_ids() -> None:
    text = "abcdefghij\r\nklmnopqrst"
    chunks = chunk_text("ver_source_1", text, size=10, overlap=2)
    lf_chunks = chunk_text("ver_source_1", text.replace("\r\n", "\n"), size=10, overlap=2)

    assert chunks == lf_chunks
    assert [(chunk.start, chunk.end) for chunk in chunks] == [(0, 10), (8, 18), (16, 21)]
    assert chunks[0].text == "abcdefghij"
    assert chunks[0].text[-2:] == chunks[1].text[:2]
    assert len({chunk.version_id for chunk in chunks}) == len(chunks)


def test_frozen_canary_chunk_count_matches_manifest() -> None:
    manifest = load_fixture_manifest()
    source = (fixture_root() / "documents" / "canary_source.md").read_bytes()
    settings = manifest["chunking"]
    chunks = chunk_text(
        "ver_demo_canary_001",
        source,
        size=settings["size"],
        overlap=settings["overlap"],
    )

    assert len(chunks) == settings["canary_source_chunk_count"] == 4


def test_signed_feature_hash_is_repeatable_normalized_and_64_dimensional() -> None:
    first = feature_hash_embedding("Project Nightjar amber-lantern-731")
    second = feature_hash_embedding("Project Nightjar amber-lantern-731")
    changed = feature_hash_embedding("Completely unrelated ocean current")

    assert first == second
    assert len(first) == DEFAULT_EMBEDDING_DIMENSIONS == 64
    assert math.isclose(math.sqrt(sum(value * value for value in first)), 1.0)
    assert first != changed
    assert feature_hash_embedding("") == (0.0,) * 64


def test_similarity_scores_exact_and_semantic_content() -> None:
    canary = "Project Nightjar canary amber-lantern-731"
    related = "Nightjar used the amber-lantern-731 canary"
    unrelated = "Tides follow lunar gravity"

    assert exact_score("line\r\n", "line\n") == 1.0
    assert exact_score("caf\u00e9", "cafe\u0301") == 0.0
    assert semantic_score(canary, canary) == pytest.approx(1.0)
    assert semantic_score(canary, related) > semantic_score(canary, unrelated)
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == 0.0
    with pytest.raises(ValueError, match="equal dimensions"):
        cosine_similarity([1.0], [1.0, 2.0])


def test_summary_memory_and_answer_helpers_preserve_declared_fixture_facts() -> None:
    source = (fixture_root() / "documents" / "canary_source.md").read_text(encoding="utf-8")
    summary = summarize_text(source)
    memories = memory_from_text(source)
    answer = answer_from_contexts("What is the Nightjar canary phrase?", [source])

    assert "amber-lantern-731" in summary
    assert "vault 19" in summary
    assert memories[:2] == (
        'Project Nightjar\'s revocation canary phrase is "amber-lantern-731".',
        "The approved archive location is vault 19.",
    )
    assert "amber-lantern-731" in answer
    assert answer_from_contexts("anything", []) == "No authorized context available."
    assert answer_from_contexts("quasar topology", [source]) == "No matching authorized context."


def test_rank_contexts_is_semantic_then_input_order() -> None:
    contexts = [
        "Ocean tides and lunar gravity",
        "Project Nightjar used amber-lantern-731",
        "Project Nightjar used amber-lantern-731",
    ]
    assert rank_contexts("Nightjar amber-lantern-731", contexts) == (1, 2, 0)


def test_manifest_is_frozen_hash_verified_and_declares_multi_parent_policy() -> None:
    manifest = load_fixture_manifest()
    multi_parent = next(
        item for item in manifest["corpus"] if item["fixture_ref"] == "summary_multi_parent"
    )

    assert manifest["frozen"] is True
    assert len(manifest["registered_stores"]) == 5
    assert manifest["expected_denominators"]["delete_demo"]["expected_total_targets"] == 15
    assert multi_parent["effective_allowed_principals"] == ["alice"]
    assert multi_parent["effective_valid_until"] == "2026-07-15T12:00:00Z"
    assert len(multi_parent["parent_version_ids"]) == 2
    assert fixture_manifest_hash(manifest) == fixture_manifest_hash(
        dict(reversed(manifest.items()))
    )


def test_manifest_rejects_asset_drift(tmp_path: Path) -> None:
    copied_root = tmp_path / "fixtures"
    shutil.copytree(fixture_root(), copied_root)
    canary = copied_root / "documents" / "canary_source.md"
    canary.write_text(canary.read_text(encoding="utf-8") + "tampered\n", encoding="utf-8")

    with pytest.raises(ValueError, match="asset hash mismatch"):
        load_fixture_manifest(copied_root / "manifest.json")


def test_probe_suites_are_fixed_unique_and_labelled() -> None:
    prohibited = load_probe_suite("prohibited")
    unrelated = load_probe_suite("unrelated")

    assert len(prohibited) == len(set(prohibited)) == 100
    assert len(unrelated) == len(set(unrelated)) == 100
    assert any("amber-lantern-731" in query for query in prohibited)
    assert all("amber-lantern-731" not in query for query in unrelated)
    with pytest.raises(ValueError, match="unknown probe suite"):
        load_probe_suite("buyer-holdout")


def test_probe_json_counts_match_manifest() -> None:
    manifest = load_fixture_manifest()
    for name in ("prohibited", "unrelated"):
        path = fixture_root() / manifest["probes"][name]["path"]
        data = json.loads(path.read_text(encoding="utf-8"))
        assert len(data["queries"]) == manifest["probes"][name]["count"]
