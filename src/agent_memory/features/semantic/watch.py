"""Continuous incremental reindex: poll ``.memory-bank`` and rebuild on change.

Native-Ubuntu companion to ``semindex``: instead of indexing only at
SessionStart, ``semwatch`` keeps the semantic index hot while an agent (or a
human) edits memory files. Stdlib-only polling — no inotify dependency — which
is cheap here: the bank is a few dozen small markdown files and ``stat()`` on
them costs microseconds. A change triggers the normal incremental
:func:`build_index` (sha256 chunk dedup + flock serialization), so a watcher
never fights a concurrent manual ``semindex`` run: the second build waits on
the lock and then finds everything already indexed.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from agent_memory.features.semantic.index import build_index
from agent_memory.shared.paths import bank_dir, iter_memory_files

DEFAULT_INTERVAL = 2.0
DEFAULT_DEBOUNCE = 1.0


def snapshot(memory: Path) -> dict[str, float]:
    """``{relpath: mtime}`` for every memory file; empty when the bank is gone."""
    if not memory.exists():
        return {}
    return {str(p.relative_to(memory)): p.stat().st_mtime for p in iter_memory_files(memory)}


def watch(
    root: Path, interval: float = DEFAULT_INTERVAL, debounce: float = DEFAULT_DEBOUNCE
) -> int:
    """Poll the bank every ``interval``s; rebuild when the snapshot changes.

    A second ``debounce`` sleep after detecting a change lets writers finish
    (hooks append entries in bursts) so one logical edit triggers one build,
    not three. Ctrl-C exits cleanly with code 0.
    """
    memory = bank_dir(root)
    if not memory.exists():
        print(f"no memory bank at {memory}", file=sys.stderr)
        return 1
    _report(build_index(root, rebuild=False))
    prev = snapshot(memory)
    print(
        f"semwatch: watching {memory} (interval={interval}s, debounce={debounce}s) — Ctrl-C to stop"
    )
    try:
        while True:
            time.sleep(interval)
            cur = snapshot(memory)
            if cur == prev:
                continue
            time.sleep(debounce)
            prev = snapshot(memory)
            _report(build_index(root, rebuild=False))
    except KeyboardInterrupt:
        print("semwatch: stopped")
        return 0


def _report(stats: dict) -> None:
    """One-line build summary; errors go to stderr but never kill the watcher."""
    if "error" in stats:
        print(stats["error"], file=sys.stderr)
        return
    skipped = stats.get("chunks_skipped_no_ollama", 0)
    skip_note = f", {skipped} skipped (Ollama down)" if skipped else ""
    print(
        f"semwatch: {stats['chunks']} chunks "
        f"(reused {stats.get('chunks_reused', 0)}, "
        f"re-embedded {stats.get('chunks_reembedded', 0)}{skip_note})"
    )
