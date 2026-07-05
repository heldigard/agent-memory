"""Per-project semantic index: load, save, and incremental build.

The index lives at ``<project-root>/.memory-bank/.index/`` and ONLY there —
per-project isolation, nothing global. Build is incremental (mtime-aware),
orphans are purged, and a model sidecar forces a full re-embed if the
embedding model ever changes.
"""

from __future__ import annotations

import contextlib
import json
import os
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from agent_memory.features.semantic.chunking import chunk_file
from agent_memory.shared.config import (
    EMBED_MODEL_FILE,
    INDEX_DIRNAME,
    MANIFEST_FILE,
    VECTORS_FILE,
)
from agent_memory.shared.ollama import DEFAULT_EMBED_MODEL
from agent_memory.shared.ollama import embed as ollama_embed
from agent_memory.shared.paths import bank_dir, iter_memory_files


def index_dir(root: Path) -> Path:
    return bank_dir(root) / INDEX_DIRNAME


def load_index(idx: Path) -> tuple[np.ndarray, list[dict]]:
    """Return ``(vectors[N,dim] or empty, manifest list)``. Missing/corrupt → empty."""
    vpath, mpath = idx / VECTORS_FILE, idx / MANIFEST_FILE
    if not (vpath.exists() and mpath.exists()):
        return np.empty((0, 0), dtype=np.float32), []
    try:
        vectors = np.load(vpath)["vectors"]
        manifest = _load_json(mpath)
    except (OSError, ValueError, KeyError):
        return np.empty((0, 0), dtype=np.float32), []
    if len(manifest) != vectors.shape[0]:
        return np.empty((0, 0), dtype=np.float32), []
    return vectors.astype(np.float32), manifest


def save_index(idx: Path, vectors: np.ndarray, manifest: list[dict]) -> None:
    """Atomic save (tmp files + ``os.replace``) so a concurrent search never sees
    a half-written index. Also writes an embed-model sidecar."""
    idx.mkdir(parents=True, exist_ok=True)
    vtmp, mtmp, emtmp = (
        idx / ".vectors.tmp.npz",
        idx / ".manifest.tmp.json",
        idx / ".embed_model.tmp.txt",
    )
    np.savez(vtmp, vectors=vectors.astype(np.float32))
    _write_json(mtmp, manifest)
    emtmp.write_text(DEFAULT_EMBED_MODEL, encoding="utf-8")
    os.replace(vtmp, idx / VECTORS_FILE)
    os.replace(mtmp, idx / MANIFEST_FILE)
    os.replace(emtmp, idx / EMBED_MODEL_FILE)


def build_index(root: Path, rebuild: bool = False) -> dict:
    """Build or incrementally update the per-project index. Returns stats."""
    memory = bank_dir(root)
    idx = index_dir(root)
    if not memory.exists():
        return {"error": f"no memory bank at {memory}"}
    vectors, manifest, model_changed = _load_or_reset(idx, rebuild)
    files = iter_memory_files(memory)
    current = {str(p.relative_to(memory)): p.stat().st_mtime for p in files}
    kept_manifest, kept_vectors = _select_kept(manifest, vectors, current)
    to_embed = _files_to_embed(files, kept_manifest, memory)
    new_records, new_vecs, skipped = _embed_files(to_embed, memory)
    all_records, all_vectors = _merge(kept_manifest, kept_vectors, new_records, new_vecs)
    _persist_or_clear(idx, all_records, all_vectors)
    inputs = BuildInputs(
        files=files,
        to_embed=to_embed,
        manifest=manifest,
        all_records=all_records,
        skipped=skipped,
        rebuild=rebuild,
        model_changed=model_changed,
        idx=idx,
    )
    return _stats(inputs)


def _load_or_reset(idx: Path, rebuild: bool) -> tuple[np.ndarray, list[dict], bool]:
    """Return ``(vectors, manifest, model_changed)`` honoring rebuild + model guard."""
    if rebuild:
        return np.empty((0, 0), dtype=np.float32), [], False
    try:
        stored = (idx / EMBED_MODEL_FILE).read_text(encoding="utf-8").strip()
    except OSError:
        stored = ""
    if stored != DEFAULT_EMBED_MODEL:
        return np.empty((0, 0), dtype=np.float32), [], bool(stored)
    vectors, manifest = load_index(idx)
    return vectors, manifest, False


def _select_kept(
    manifest: list[dict], vectors: np.ndarray, current: dict[str, float]
) -> tuple[list[dict], np.ndarray]:
    """Filter manifest+vectors to unchanged, non-orphan entries."""
    kept_manifest: list[dict] = []
    kept_idx: list[int] = []
    for i, rec in enumerate(manifest):
        rel = rec.get("file")
        if rel not in current:
            continue
        if rec.get("mtime") == current[rel]:
            kept_manifest.append(rec)
            kept_idx.append(i)
    if vectors.shape[0] and kept_idx:
        return kept_manifest, vectors[kept_idx]
    if vectors.shape[0]:
        return kept_manifest, np.empty((0, vectors.shape[1]), dtype=np.float32)
    return kept_manifest, np.empty((0, 0), dtype=np.float32)


def _files_to_embed(files: list[Path], kept_manifest: list[dict], memory: Path) -> list[Path]:
    """Files not present (unchanged) in ``kept_manifest`` that need re-embedding."""
    kept = {rec["file"] for rec in kept_manifest}
    return [p for p in files if str(p.relative_to(memory)) not in kept]


def _embed_files(to_embed: list[Path], memory: Path) -> tuple[list[dict], list[np.ndarray], int]:
    """Embed every chunk of every file in ``to_embed``."""
    records: list[dict] = []
    vecs: list[np.ndarray] = []
    skipped = 0
    for path in to_embed:
        recs, vs, sk = _embed_one_file(path, memory)
        records.extend(recs)
        vecs.extend(vs)
        skipped += sk
    return records, vecs, skipped


def _embed_one_file(path: Path, memory: Path) -> tuple[list[dict], list[np.ndarray], int]:
    """Embed all chunks of one file; return (records, vectors, skipped_count)."""
    records: list[dict] = []
    vecs: list[np.ndarray] = []
    skipped = 0
    rel = str(path.relative_to(memory))
    mtime = path.stat().st_mtime
    for ch in chunk_file(path):
        vec = ollama_embed(ch["text"])
        if vec is None:
            skipped += 1
            continue
        records.append(_record(rel, mtime, ch))
        vecs.append(np.asarray(vec, dtype=np.float32))
    return records, vecs, skipped


def _record(rel: str, mtime: float, ch: dict) -> dict:
    return {
        "file": rel,
        "mtime": mtime,
        "heading": ch["heading"],
        "start": ch["start"],
        "end": ch["end"],
        "sha256": ch["sha256"],
        "text": ch["text"],
    }


def _merge(
    kept_manifest: list[dict],
    kept_vectors: np.ndarray,
    new_records: list[dict],
    new_vecs: list[np.ndarray],
) -> tuple[list[dict], np.ndarray]:
    """Merge kept + new into one (records, vectors) pair."""
    all_records = kept_manifest + new_records
    if new_vecs:
        new_block = np.vstack(new_vecs).astype(np.float32)
        return all_records, np.vstack([kept_vectors, new_block]) if kept_vectors.shape[
            0
        ] else new_block
    return all_records, kept_vectors


def _persist_or_clear(idx: Path, records: list[dict], vectors: np.ndarray) -> None:
    """Save the index, or clear stale files when the bank is empty."""
    if records:
        save_index(idx, vectors, records)
        return
    for f in (idx / VECTORS_FILE, idx / MANIFEST_FILE):
        with contextlib.suppress(OSError):
            f.unlink()


@dataclass
class BuildInputs:
    """Inputs to ``_stats``: bundled so the helper takes a single parameter."""

    files: list[Path]
    to_embed: list[Path]
    manifest: list[dict]
    all_records: list[dict]
    skipped: int
    rebuild: bool
    model_changed: bool
    idx: Path


def _stats(inp: BuildInputs) -> dict:
    """Build the serializable stats dict from accumulated build inputs."""
    files_str = {str(p) for p in inp.files}
    return {
        "files_total": len(inp.files),
        "files_reembedded": len(inp.to_embed),
        "files_reused": len(inp.files) - len(inp.to_embed),
        "orphans_dropped": sum(1 for r in inp.manifest if r.get("file") not in files_str),
        "chunks": len(inp.all_records),
        "chunks_skipped_no_ollama": inp.skipped,
        "rebuild": inp.rebuild,
        "model": DEFAULT_EMBED_MODEL,
        "model_changed": inp.model_changed,
        "index_dir": str(inp.idx),
    }


def _load_json(path: Path) -> list[dict]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def _write_json(path: Path, data: list[dict]) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
