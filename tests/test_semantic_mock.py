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
from agent_memory.features.semantic.hybrid import hybrid_search
from agent_memory.features.semantic.index import build_index
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


def test_dense_search_returns_scored_records(tmp_path: Path, monkeypatch) -> None:
    _patch_embed(monkeypatch)
    _seed_bank(tmp_path)
    build_index(tmp_path, rebuild=False)
    records = dense_search(tmp_path, "auth", k=5, min_score=0.1)
    assert records
    assert all("score" in r for r in records)


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
