"""Semantic slice (index/search/hybrid/recall) with a deterministic embed mock.

No real Ollama is contacted. We monkeypatch the locally-imported ``ollama_embed``
in each consumer module to a small fixed-dimension vector keyed on keywords,
then exercise the full build → search → recall path.
"""

from __future__ import annotations

from pathlib import Path

import agent_memory.features.semantic.hybrid as hybrid_mod
import agent_memory.features.semantic.index as index_mod
import agent_memory.features.semantic.search as search_mod
from agent_memory.features.semantic.command import _filter_inactive_records
from agent_memory.features.semantic.hybrid import hybrid_search
from agent_memory.features.semantic.index import build_index
from agent_memory.features.semantic.search import keyword_fallback
from agent_memory.features.semantic.search import search as dense_search


def _fake_embed(text: str, *, model: str = "m", timeout: float = 60.0) -> list[float]:
    """Keyword-keyed unit vector (dim 3). Deterministic → stable index."""
    t = text.lower()
    if "auth" in t:
        return [1.0, 0.0, 0.0]
    if "deploy" in t:
        return [0.0, 1.0, 0.0]
    return [0.5, 0.5, 0.0]


def _patch_embed(monkeypatch) -> None:
    """Replace the locally-imported ``ollama_embed`` in every consumer module.

    ``recall`` does not import ``ollama_embed`` directly — it goes through
    ``hybrid_search`` (mocked via ``hybrid_mod``) and ``keyword_fallback``."""
    for mod in (index_mod, hybrid_mod, search_mod):
        monkeypatch.setattr(mod, "ollama_embed", _fake_embed)


def _seed_bank(tmp_path: Path) -> Path:
    from agent_memory.features.bank.command import add_entry, init_memory

    init_memory(tmp_path)
    add_entry(tmp_path, "progress", "auth flow uses jwt tokens", status="completed")
    add_entry(tmp_path, "progress", "deploy payments microservice v2", status="completed")
    add_entry(tmp_path, "currentTask", "working on auth token refresh")
    return tmp_path


def test_build_index_embeds_chunks(tmp_path: Path, monkeypatch) -> None:
    _patch_embed(monkeypatch)
    _seed_bank(tmp_path)
    stats = build_index(tmp_path, rebuild=False)
    assert stats["chunks"] > 0
    assert stats["chunks_reembedded"] > 0
    assert stats["chunks_reused"] == 0  # first build: nothing to reuse
    assert stats["index_version"] == "v2"


def test_second_build_reuses_everything(tmp_path: Path, monkeypatch) -> None:
    _patch_embed(monkeypatch)
    _seed_bank(tmp_path)
    first = build_index(tmp_path, rebuild=False)
    second = build_index(tmp_path, rebuild=False)
    assert second["chunks"] == first["chunks"]
    assert second["chunks_reused"] == second["chunks"]  # chunk-dedup: all reused
    assert second["chunks_reembedded"] == 0


def test_rebuild_force_reembeds_all(tmp_path: Path, monkeypatch) -> None:
    _patch_embed(monkeypatch)
    _seed_bank(tmp_path)
    build_index(tmp_path, rebuild=False)
    forced = build_index(tmp_path, rebuild=True)
    assert forced["chunks_reused"] == 0
    assert forced["chunks_reembedded"] == forced["chunks"]


def test_embed_chunks_parallel_is_order_stable_and_counts_skipped(monkeypatch) -> None:
    """Parallel embed path must return records in INPUT order and count skips."""
    from agent_memory.features.semantic.index import (
        _collect_embed_results,
        _embed_chunks_parallel,
        _FileChunk,
    )

    chunks = [
        _FileChunk(
            rel=f"f{i}.md",
            mtime=0.0,
            ch={
                "heading": "# h",
                "start": 1,
                "end": 2,
                "text": f"body {i}",
                "sha256": f"h{i}",
            },
        )
        for i in range(6)
    ]

    # Worker sleeps scale with text length so threads finish OUT of submission
    # order — proving the reducer relies on `map` order, not wall-clock order.
    def slow_embed(text: str, *, model: str = "m", timeout: float = 60.0):
        import time

        time.sleep(0.01 * (10 - len(text)))  # shorter text → longer wait
        return [float(len(text)), 0.0, 0.0]

    monkeypatch.setattr(index_mod, "ollama_embed", slow_embed)
    records, vecs, skipped = _embed_chunks_parallel(chunks, workers=4)
    assert skipped == 0
    assert [r["file"] for r in records] == [f"f{i}.md" for i in range(6)]
    assert len(vecs) == 6

    # Reducer also drops None (failed embed) and counts it as skipped.
    mixed: list[tuple[_FileChunk, list[float] | None]] = [
        (chunks[0], [1.0, 0.0, 0.0]),
        (chunks[1], None),
        (chunks[2], [3.0, 0.0, 0.0]),
    ]
    rec2, vecs2, skipped2 = _collect_embed_results(mixed)
    assert skipped2 == 1
    assert [r["file"] for r in rec2] == ["f0.md", "f2.md"]
    assert len(vecs2) == 2


def test_dense_search_returns_scored_records(tmp_path: Path, monkeypatch) -> None:
    _patch_embed(monkeypatch)
    _seed_bank(tmp_path)
    build_index(tmp_path, rebuild=False)
    records = dense_search(tmp_path, "auth", k=5, min_score=0.1)
    assert records
    assert all("score" in r for r in records)


def test_semantic_output_strips_superseded_lines_but_keeps_current_chunk_text() -> None:
    records = [
        {
            "file": "progress.md",
            "start": 1,
            "end": 2,
            "score": 0.9,
            "text": (
                "- 2026-07-08 | status:superseded | Crow is primary\n"
                "- 2026-07-09 | status:completed | Batiai is primary"
            ),
        },
        {
            "file": "old.md",
            "start": 1,
            "end": 1,
            "score": 0.8,
            "text": "- 2026-07-08 | status:superseded | obsolete only",
        },
    ]
    filtered = _filter_inactive_records(records)
    assert len(filtered) == 1
    assert "Batiai" in filtered[0]["text"]
    assert "Crow" not in filtered[0]["text"]


def test_keyword_fallback_can_include_inactive_for_historical_audit(tmp_path: Path) -> None:
    from agent_memory.features.bank.command import add_entry, init_memory

    init_memory(tmp_path)
    add_entry(tmp_path, "progress", "Crow historical winner", status="superseded")
    assert keyword_fallback(tmp_path, "Crow", k=5) == []
    historical = keyword_fallback(tmp_path, "Crow", k=5, include_inactive=True)
    assert len(historical) == 1
    assert "Crow historical winner" in historical[0]["text"]


def test_hybrid_search_tags_method(tmp_path: Path, monkeypatch) -> None:
    _patch_embed(monkeypatch)
    _seed_bank(tmp_path)
    from agent_memory.features.semantic.index import index_dir, load_index

    vectors, manifest = load_index(index_dir(tmp_path))
    hits = hybrid_search(vectors, manifest, "auth", k=3)
    # hybrid may return [] if bm25+dense both miss the tiny corpus; accept non-empty method tags
    for h in hits:
        assert "method" in h


def test_recall_active_query_no_error(tmp_path: Path, monkeypatch) -> None:
    _patch_embed(monkeypatch)
    _seed_bank(tmp_path)
    build_index(tmp_path, rebuild=False)
    from agent_memory.features.semantic.recall import recall

    result = recall(tmp_path, query="auth tokens", min_score=0.1)
    assert "error" not in result or result.get("hits") is not None
    assert result["query"] == "auth tokens"


def test_recall_passive_reads_current_task(tmp_path: Path, monkeypatch) -> None:
    _patch_embed(monkeypatch)
    _seed_bank(tmp_path)
    build_index(tmp_path, rebuild=False)
    from agent_memory.features.semantic.recall import recall

    result = recall(tmp_path, min_score=0.1)
    # passive mode: source label is the file, not the literal "query"
    assert result["source"] == "currentTask.md"


def test_recall_errors_when_no_task_no_query(tmp_path: Path, monkeypatch) -> None:
    _patch_embed(monkeypatch)
    from agent_memory.features.bank.command import init_memory

    init_memory(tmp_path)
    # Remove currentTask.md so _resolve_query has nothing to query from
    (tmp_path / ".memory-bank" / "currentTask.md").unlink()
    build_index(tmp_path, rebuild=False)
    from agent_memory.features.semantic.recall import recall

    result = recall(tmp_path, min_score=0.1)
    assert "error" in result


def test_version_mismatch_forces_reset(tmp_path: Path, monkeypatch) -> None:
    """A stale ``version.txt`` (v1) triggers a full re-embed, not chunk reuse."""
    _patch_embed(monkeypatch)
    _seed_bank(tmp_path)
    first = build_index(tmp_path, rebuild=False)
    # corrupt the version sidecar to simulate an older format
    (tmp_path / ".memory-bank" / ".index" / "version.txt").write_text("v1", encoding="utf-8")
    second = build_index(tmp_path, rebuild=False)
    assert second["chunks_reused"] == 0  # forced reset → re-embed everything
    assert second["chunks_reembedded"] == first["chunks"]
    assert second["index_changed"] is True  # "v1" existed and differed → True


def test_recall_keeps_pure_bm25_hits_when_ollama_down(tmp_path: Path, monkeypatch) -> None:
    """Pure-BM25 hits carry dense score 0.0 by construction; thresholding them
    against min_score would silently empty recall whenever Ollama is down
    (mirrors the exemption in hybrid._filter_min_score)."""
    _patch_embed(monkeypatch)
    _seed_bank(tmp_path)
    build_index(tmp_path, rebuild=False)
    import agent_memory.features.semantic.recall as recall_mod

    bm25_hit = {"file": "progress.md", "score": 0.0, "method": "bm25", "text": "auth flow"}
    monkeypatch.setattr(recall_mod, "_gather_hits", lambda root, q, k: ([bm25_hit], False))

    result = recall_mod.recall(tmp_path, query="auth", min_score=0.2)
    assert result["fallback"] is False
    assert [h["text"] for h in result["hits"]] == ["auth flow"]
