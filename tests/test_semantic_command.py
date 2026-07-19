"""CLI handler tests for the semantic slice (semantic/command.py).

Handlers print to stdout and return exit codes; Ollama-facing pieces are
monkeypatched (same keyword-keyed embed mock as ``test_semantic_mock.py``) so
no real daemon is contacted.
"""

from __future__ import annotations

import json
from pathlib import Path

import agent_memory.features.semantic.command as cmd_mod
import agent_memory.features.semantic.hybrid as hybrid_mod
import agent_memory.features.semantic.index as index_mod
import agent_memory.features.semantic.search as search_mod
from agent_memory.features.semantic.command import (
    SearchOpts,
    _record_line,
    cmd_clean,
    cmd_index,
    cmd_recall,
    cmd_search,
    cmd_status,
)


def _fake_embed(text: str, *, model: str = "m", timeout: float = 60.0) -> list[float]:
    t = text.lower()
    if "auth" in t:
        return [1.0, 0.0, 0.0]
    if "deploy" in t:
        return [0.0, 1.0, 0.0]
    return [0.5, 0.5, 0.0]


def _patch_embed(monkeypatch) -> None:
    for mod in (index_mod, hybrid_mod, search_mod):
        monkeypatch.setattr(mod, "ollama_embed", _fake_embed)


def _seed_bank(tmp_path: Path) -> Path:
    from agent_memory.features.bank.command import add_entry, init_memory

    init_memory(tmp_path)
    add_entry(tmp_path, "progress", "auth flow uses jwt tokens", status="completed")
    add_entry(tmp_path, "currentTask", "working on auth token refresh")
    return tmp_path


def _stats(**over: object) -> dict:
    base: dict = {
        "chunks": 3,
        "files_total": 2,
        "files_reused": 1,
        "files_reembedded": 1,
        "chunks_reused": 2,
        "chunks_reembedded": 1,
        "orphans_dropped": 0,
        "chunks_skipped_no_ollama": 0,
        "rebuild": False,
        "model": "m",
        "index_dir": "/tmp/idx",
    }
    base.update(over)
    return base


def test_cmd_index_prints_summary(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(cmd_mod, "build_index", lambda root, rebuild: _stats())
    assert cmd_index(tmp_path, rebuild=False) == 0
    out = capsys.readouterr().out
    assert "Indexed 3 chunks from 2 files" in out
    assert "model=m" in out


def test_cmd_index_reports_error(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(cmd_mod, "build_index", lambda root, rebuild: {"error": "boom"})
    assert cmd_index(tmp_path, rebuild=False) == 1
    assert "boom" in capsys.readouterr().err


def test_cmd_index_notes_model_change_and_skips(tmp_path: Path, monkeypatch, capsys) -> None:
    stats = _stats(model_changed=True, chunks_skipped_no_ollama=2)
    monkeypatch.setattr(cmd_mod, "build_index", lambda root, rebuild: stats)
    assert cmd_index(tmp_path, rebuild=False) == 0
    captured = capsys.readouterr()
    assert "embedding model changed" in captured.out
    assert "2 chunks skipped" in captured.err


def test_cmd_search_hybrid_path(tmp_path: Path, monkeypatch, capsys) -> None:
    _patch_embed(monkeypatch)
    _seed_bank(tmp_path)
    index_mod.build_index(tmp_path, rebuild=False)
    monkeypatch.setattr(cmd_mod, "ollama_is_alive", lambda: True)
    rc = cmd_search(tmp_path, "auth", 5, SearchOpts(min_score=0.1))
    assert rc == 0
    assert "## Semantic Memory Search: auth" in capsys.readouterr().out


def test_cmd_search_keyword_fallback_when_ollama_down(tmp_path: Path, monkeypatch, capsys) -> None:
    _patch_embed(monkeypatch)
    _seed_bank(tmp_path)
    index_mod.build_index(tmp_path, rebuild=False)
    monkeypatch.setattr(cmd_mod, "ollama_is_alive", lambda: False)
    monkeypatch.setattr(cmd_mod, "hybrid_search", lambda *a, **k: [])
    rc = cmd_search(tmp_path, "auth", 5, SearchOpts(min_score=0.1))
    assert rc == 0
    captured = capsys.readouterr()
    assert "[Ollama down" in captured.err
    assert "keyword fallback" in captured.out


def test_cmd_search_include_inactive_keeps_superseded(tmp_path: Path, monkeypatch, capsys) -> None:
    _patch_embed(monkeypatch)
    _seed_bank(tmp_path)
    from agent_memory.features.bank.command import add_entry

    add_entry(tmp_path, "progress", "auth v1 replaced", status="superseded")
    index_mod.build_index(tmp_path, rebuild=False)
    monkeypatch.setattr(cmd_mod, "ollama_is_alive", lambda: True)
    cmd_search(tmp_path, "auth", 10, SearchOpts(min_score=0.0, include_inactive=True))
    assert "auth v1 replaced" in capsys.readouterr().out


def test_cmd_search_no_matches(tmp_path: Path, monkeypatch, capsys) -> None:
    _patch_embed(monkeypatch)
    _seed_bank(tmp_path)
    index_mod.build_index(tmp_path, rebuild=False)
    monkeypatch.setattr(cmd_mod, "ollama_is_alive", lambda: True)
    monkeypatch.setattr(cmd_mod, "hybrid_search", lambda *a, **k: [])
    rc = cmd_search(tmp_path, "zzzz", 5, SearchOpts(min_score=0.1))
    assert rc == 0
    assert "- no matches" in capsys.readouterr().out


def test_record_line_tags() -> None:
    base = {"file": "f.md", "start": 1, "end": 2, "score": 0.5, "text": "body"}
    assert "[keyword fallback]" in _record_line({**base, "fallback": True})
    assert "[rerank=0.9]" in _record_line({**base, "rerank_score": 0.9})
    assert "[bm25]" in _record_line({**base, "method": "bm25"})
    assert "(" in _record_line({**base, "heading": "# H"})
    assert _record_line(base).startswith("- f.md:1-2 score=0.5")


def test_cmd_status_text_and_json(tmp_path: Path, monkeypatch, capsys) -> None:
    _patch_embed(monkeypatch)
    _seed_bank(tmp_path)
    index_mod.build_index(tmp_path, rebuild=False)
    monkeypatch.setattr(cmd_mod, "ollama_is_alive", lambda: True)
    assert cmd_status(tmp_path) == 0
    assert "memory_dir" in capsys.readouterr().out
    assert cmd_status(tmp_path, json_out=True) == 0
    snapshot = json.loads(capsys.readouterr().out)
    assert snapshot["exists"] is True
    assert snapshot["indexed_chunks"] > 0


def test_cmd_status_hints_refresh_when_stale(tmp_path: Path, monkeypatch, capsys) -> None:
    _patch_embed(monkeypatch)
    _seed_bank(tmp_path)
    index_mod.build_index(tmp_path, rebuild=False)
    monkeypatch.setattr(cmd_mod, "ollama_is_alive", lambda: True)
    # Touch a file so its mtime diverges from the indexed record.
    progress = tmp_path / ".memory-bank" / "progress.md"
    progress.write_text(progress.read_text() + "\n")
    assert cmd_status(tmp_path) == 0
    assert "Run `index`" in capsys.readouterr().out


def test_cmd_clean_prints_summary(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(cmd_mod, "build_index", lambda root, rebuild: _stats(orphans_dropped=2))
    assert cmd_clean(tmp_path) == 0
    out = capsys.readouterr().out
    assert "Cleaned -> 3 chunks" in out
    assert "2 orphans dropped" in out


def test_cmd_recall_error_propagates(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(cmd_mod.recall_mod, "recall", lambda *a, **k: {"error": "no query"})
    assert cmd_recall(tmp_path, 5, None, 0.2, full=False) == 1
    assert "no query" in capsys.readouterr().err


def test_cmd_recall_prints_hits_full_and_compact(tmp_path: Path, monkeypatch, capsys) -> None:
    hit = {
        "file": "progress.md",
        "start": 1,
        "end": 3,
        "score": 0.9,
        "heading": "# Progress",
        "type": "dense",
        "text": "line one\nline two",
    }
    result = {"query": "auth", "hits": [hit], "source": "query", "min_score": 0.2}
    monkeypatch.setattr(cmd_mod.recall_mod, "recall", lambda *a, **k: result)
    assert cmd_recall(tmp_path, 5, "auth", 0.2, full=False) == 0
    out = capsys.readouterr().out
    assert "## Recall (active re-query)" in out
    assert "min_score>=0.2" in out
    assert "line one line two" in out

    assert cmd_recall(tmp_path, 5, "auth", 0.2, full=True) == 0
    assert "### Matched Memory: progress.md" in capsys.readouterr().out


def test_cmd_recall_no_hits_guidance(tmp_path: Path, monkeypatch, capsys) -> None:
    result = {"query": "q", "hits": [], "source": "currentTask.md"}
    monkeypatch.setattr(cmd_mod.recall_mod, "recall", lambda *a, **k: result)
    assert cmd_recall(tmp_path, 5, None, 0.2, full=False) == 0
    out = capsys.readouterr().out
    assert "## Recall (currentTask.md)" in out
    assert "no relevant memory" in out
