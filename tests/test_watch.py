"""Tests for the semwatch polling loop (semantic/watch.py).

No real Ollama: ``build_index`` is monkeypatched; the loop is driven by a fake
``time.sleep`` that mutates a file mid-run and then raises KeyboardInterrupt.
"""

from __future__ import annotations

from pathlib import Path

import agent_memory.features.semantic.watch as watch_mod
from agent_memory.features.semantic.watch import snapshot, watch

_STATS = {"chunks": 2, "chunks_reused": 1, "chunks_reembedded": 1, "chunks_skipped_no_ollama": 0}


def test_snapshot_maps_rel_paths_to_mtimes(bank: Path) -> None:
    snap = snapshot(bank / ".memory-bank")
    assert "MEMORY.md" in snap
    assert "topics/_index.md" in snap
    assert all(isinstance(v, float) for v in snap.values())


def test_snapshot_empty_when_bank_missing(tmp_path: Path) -> None:
    assert snapshot(tmp_path / ".memory-bank") == {}


def test_watch_fails_without_bank(tmp_path: Path, capsys) -> None:
    assert watch(tmp_path, interval=0.01, debounce=0.0) == 1
    assert "no memory bank" in capsys.readouterr().err


def test_watch_reindexes_on_change(bank: Path, monkeypatch, capsys) -> None:
    builds: list[bool] = []
    monkeypatch.setattr(
        watch_mod, "build_index", lambda root, rebuild=False: builds.append(rebuild) or _STATS
    )
    target = bank / ".memory-bank" / "MEMORY.md"

    state = {"sleeps": 0}

    def fake_sleep(seconds: float) -> None:
        state["sleeps"] += 1
        if state["sleeps"] == 2:
            target.write_text(target.read_text() + "\nchanged\n")
        if state["sleeps"] > 3:
            raise KeyboardInterrupt

    monkeypatch.setattr(watch_mod.time, "sleep", fake_sleep)
    rc = watch(bank, interval=0.01, debounce=0.0)
    assert rc == 0
    # initial build + exactly one rebuild for the settled change
    assert len(builds) == 2
    out = capsys.readouterr().out
    assert out.count("semwatch: 2 chunks") == 2
    assert "semwatch: stopped" in out


def test_watch_idle_does_not_rebuild(bank: Path, monkeypatch) -> None:
    builds: list[bool] = []
    monkeypatch.setattr(
        watch_mod, "build_index", lambda root, rebuild=False: builds.append(rebuild) or _STATS
    )

    state = {"sleeps": 0}

    def fake_sleep(seconds: float) -> None:
        state["sleeps"] += 1
        if state["sleeps"] > 3:
            raise KeyboardInterrupt

    monkeypatch.setattr(watch_mod.time, "sleep", fake_sleep)
    assert watch(bank, interval=0.01, debounce=0.0) == 0
    assert len(builds) == 1  # only the initial build


def test_watch_reports_ollama_skips(bank: Path, monkeypatch, capsys) -> None:
    stats = {**_STATS, "chunks_skipped_no_ollama": 3}
    monkeypatch.setattr(watch_mod, "build_index", lambda root, rebuild=False: stats)
    monkeypatch.setattr(
        watch_mod.time,
        "sleep",
        lambda s: (_ for _ in ()).throw(KeyboardInterrupt),
    )
    assert watch(bank, interval=0.01, debounce=0.0) == 0
    assert "3 skipped (Ollama down)" in capsys.readouterr().out


def test_watch_survives_build_errors(bank: Path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        watch_mod, "build_index", lambda root, rebuild=False: {"error": "no memory bank"}
    )
    monkeypatch.setattr(
        watch_mod.time,
        "sleep",
        lambda s: (_ for _ in ()).throw(KeyboardInterrupt),
    )
    assert watch(bank, interval=0.01, debounce=0.0) == 0
    assert "no memory bank" in capsys.readouterr().err
