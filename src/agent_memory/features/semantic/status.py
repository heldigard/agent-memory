"""Semantic index health snapshot."""

from __future__ import annotations

from pathlib import Path

from agent_memory.features.semantic.index import index_dir, load_index
from agent_memory.shared.config import EMBED_MODEL_FILE, VERSION_FILE
from agent_memory.shared.ollama import embed_ready as ollama_embed_ready
from agent_memory.shared.ollama import is_alive as ollama_is_alive
from agent_memory.shared.paths import bank_dir, iter_memory_files


def status(root: Path) -> dict[str, object]:
    """Return index health: file/chunk counts, dimension, stale/orphan counts."""
    memory = bank_dir(root)
    idx = index_dir(root)
    vectors, manifest = load_index(idx)
    files = iter_memory_files(memory) if memory.exists() else []
    current = {str(p.relative_to(memory)): p.stat().st_mtime for p in files}
    stale = sum(
        1 for r in manifest if r.get("file") in current and r.get("mtime") != current[r["file"]]
    )
    orphans = sum(1 for r in manifest if r.get("file") not in current)
    vector_dim = int(vectors.shape[1]) if vectors.ndim == 2 and vectors.shape[0] > 0 else 0
    tags_up = ollama_is_alive()
    # Only probe embed when tags respond — avoid a pointless timeout on dead hosts.
    embed_ok = ollama_embed_ready() if tags_up else False
    return {
        "memory_dir": str(memory),
        "exists": memory.exists(),
        "files": len(files),
        "indexed_chunks": len(manifest),
        "vector_dim": vector_dim,
        "embed_model": _read_sidecar(idx / EMBED_MODEL_FILE),
        "index_version": _read_sidecar(idx / VERSION_FILE),
        "stale_files": stale,
        "orphan_chunks": orphans,
        "ollama_alive": tags_up,
        "ollama_embed_ready": embed_ok,
    }


def _read_sidecar(path: Path) -> str:
    """Return stripped sidecar text, or empty string on missing/unreadable file."""
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
