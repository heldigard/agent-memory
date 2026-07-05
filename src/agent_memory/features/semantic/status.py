"""Semantic index health snapshot."""

from __future__ import annotations

from pathlib import Path

from agent_memory.features.semantic.index import index_dir, load_index
from agent_memory.shared.ollama import is_alive as ollama_is_alive
from agent_memory.shared.paths import iter_memory_files


def status(root: Path) -> dict[str, object]:
    """Return index health: file/chunk counts, dimension, stale/orphan counts."""
    memory = root / ".memory-bank"
    idx = index_dir(root)
    _, manifest = load_index(idx)
    files = iter_memory_files(memory) if memory.exists() else []
    current = {str(p.relative_to(memory)): p.stat().st_mtime for p in files}
    stale = sum(
        1 for r in manifest if r.get("file") in current and r.get("mtime") != current[r["file"]]
    )
    orphans = sum(1 for r in manifest if r.get("file") not in current)
    return {
        "memory_dir": str(memory),
        "exists": memory.exists(),
        "files": len(files),
        "indexed_chunks": len(manifest),
        "stale_files": stale,
        "orphan_chunks": orphans,
        "ollama_alive": ollama_is_alive(),
    }
