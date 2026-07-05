"""Per-project semantic index: load, save, and incremental build.

The index lives at ``<project-root>/.memory-bank/.index/`` and ONLY there —
per-project isolation, nothing global. Two reuse layers keep builds cheap:

  * **Chunk-level dedup (sha256)** — re-chunk every file on each build (cheap),
    then reuse the vector of any chunk whose ``sha256`` already exists in the
    manifest. A one-line edit in a 50-chunk file re-embeds only that chunk, not
    the whole file. Replaces the old coarse file-level ``mtime`` match.
  * **Model + format sidecars** — a different embedding model OR a bumped
    ``INDEX_VERSION`` (vector format) forces one full re-embed, after which the
    cheap chunk-dedup path takes over again.

Vectors are stored L2-normalized so dense cosine is a single ``v @ q`` per
query (no per-query renormalization of the whole matrix).
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from agent_memory.features.semantic.chunking import chunk_file
from agent_memory.shared.config import (
    EMBED_MODEL_FILE,
    INDEX_DIRNAME,
    INDEX_VERSION,
    MANIFEST_FILE,
    VECTORS_FILE,
    VERSION_FILE,
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
    a half-written index. Vectors are L2-normalized on write; also writes the
    embed-model and index-version sidecars (the version sidecar is what lets a
    format bump force one clean re-embed)."""
    idx.mkdir(parents=True, exist_ok=True)
    vtmp, mtmp, emtmp, vertmp = (
        idx / ".vectors.tmp.npz",
        idx / ".manifest.tmp.json",
        idx / ".embed_model.tmp.txt",
        idx / ".version.tmp.txt",
    )
    normed = _l2_normalize(vectors)
    np.savez(vtmp, vectors=normed)
    _write_json(mtmp, manifest)
    emtmp.write_text(DEFAULT_EMBED_MODEL, encoding="utf-8")
    vertmp.write_text(INDEX_VERSION, encoding="utf-8")
    os.replace(vtmp, idx / VECTORS_FILE)
    os.replace(mtmp, idx / MANIFEST_FILE)
    os.replace(emtmp, idx / EMBED_MODEL_FILE)
    os.replace(vertmp, idx / VERSION_FILE)


def _l2_normalize(vectors: np.ndarray) -> np.ndarray:
    """Return an L2-normalized copy (rows unit-length). Empty passthrough."""
    if vectors.ndim != 2 or vectors.shape[0] == 0:
        return vectors.astype(np.float32)
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    return (vectors / (norms + 1e-9)).astype(np.float32)


def build_index(root: Path, rebuild: bool = False) -> dict:
    """Build or incrementally update the per-project index. Returns stats.

    Re-chunks every current file (cheap) and reuses the stored vector for any
    chunk whose ``sha256`` is already in the manifest — so only genuinely new or
    changed chunks get embedded, even when a whole file was touched.

    Serialized by an exclusive ``fcntl.flock`` on ``.index/.build.lock`` so two
    concurrent ``semindex`` runs (e.g. CLI + auto-maintain) can't interleave:
    the second waits, then finds everything already indexed (cheap reuse path).
    """
    memory = bank_dir(root)
    idx = index_dir(root)
    if not memory.exists():
        return {"error": f"no memory bank at {memory}"}
    with _build_lock(idx):
        return _build_index_locked(memory, idx, rebuild)


@contextlib.contextmanager
def _build_lock(idx: Path):
    """Exclusive lock held for the whole build. ``flock`` auto-releases on exit."""
    idx.mkdir(parents=True, exist_ok=True)
    lock_path = idx / ".build.lock"
    # Try non-blocking first so we can print a "waiting" notice if contended.
    with open(lock_path, "w", encoding="utf-8") as fd:
        try:
            fcntl.flock(fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("agent-memory: index build in progress; waiting for lock…", file=sys.stderr)
            fcntl.flock(fd.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fd.fileno(), fcntl.LOCK_UN)


def _build_index_locked(memory: Path, idx: Path, rebuild: bool) -> dict:
    """Build the index assuming the build lock is already held."""
    existing_vectors, existing_manifest, model_changed, version_changed = _load_or_reset(
        idx, rebuild
    )
    files = iter_memory_files(memory)
    current = {str(p.relative_to(memory)) for p in files}

    vec_by_hash = _vector_by_sha256(existing_manifest, existing_vectors)
    all_chunks = _gather_chunks(files, memory)
    kept_records, kept_vecs, to_embed = _partition_by_hash(all_chunks, vec_by_hash)

    new_records, new_vecs, skipped = _embed_chunks(to_embed)
    embedded_rels = {fc.rel for fc in to_embed}
    orphan_count = sum(1 for r in existing_manifest if r.get("file") not in current)
    all_records = kept_records + new_records
    all_vectors = _stack_vectors(kept_vecs, new_vecs)
    _persist_or_clear(idx, all_records, all_vectors)
    return _stats(
        BuildInputs(
            files=files,
            all_chunks=all_chunks,
            kept=len(kept_records),
            embedded=len(new_records),
            embedded_rels=embedded_rels,
            skipped=skipped,
            orphan_count=orphan_count,
            rebuild=rebuild,
            model_changed=model_changed,
            version_changed=version_changed,
            idx=idx,
        )
    )


def _load_or_reset(idx: Path, rebuild: bool) -> tuple[np.ndarray, list[dict], bool, bool]:
    """Return ``(vectors, manifest, model_changed, version_changed)``.

    A missing or mismatched sidecar (embed model OR index version) forces a full
    reset so the next save writes a consistent, normalized, version-tagged index.
    The ``*_changed`` flags are informational (True only when a PREVIOUS version
    existed and differed); the reset itself is driven by the mismatch checks."""
    if rebuild:
        return np.empty((0, 0), dtype=np.float32), [], False, False
    stored_model = _read_sidecar(idx / EMBED_MODEL_FILE)
    stored_version = _read_sidecar(idx / VERSION_FILE)
    needs_model_reset = stored_model != DEFAULT_EMBED_MODEL
    needs_version_reset = stored_version != INDEX_VERSION
    if needs_model_reset or needs_version_reset:
        model_changed = bool(stored_model) and needs_model_reset
        version_changed = bool(stored_version) and needs_version_reset
        return np.empty((0, 0), dtype=np.float32), [], model_changed, version_changed
    vectors, manifest = load_index(idx)
    return vectors, manifest, False, False


def _read_sidecar(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _vector_by_sha256(manifest: list[dict], vectors: np.ndarray) -> dict[str, np.ndarray]:
    """First stored vector per chunk sha256 (chunk-level reuse key)."""
    out: dict[str, np.ndarray] = {}
    for i, rec in enumerate(manifest):
        h = rec.get("sha256")
        if h and h not in out and i < vectors.shape[0]:
            out[h] = vectors[i]
    return out


@dataclass
class _FileChunk:
    """One chunk of one current file with its (rel, mtime) provenance."""

    rel: str
    mtime: float
    ch: dict


def _gather_chunks(files: list[Path], memory: Path) -> list[_FileChunk]:
    """Re-chunk every current file (no embedding happens here)."""
    out: list[_FileChunk] = []
    for path in files:
        rel = str(path.relative_to(memory))
        mtime = path.stat().st_mtime
        for ch in chunk_file(path):
            out.append(_FileChunk(rel, mtime, ch))
    return out


def _partition_by_hash(
    chunks: list[_FileChunk], vec_by_hash: dict[str, np.ndarray]
) -> tuple[list[dict], list[np.ndarray], list[_FileChunk]]:
    """Split chunks into (reused-records, reused-vectors, to-embed)."""
    kept_records: list[dict] = []
    kept_vecs: list[np.ndarray] = []
    to_embed: list[_FileChunk] = []
    for fc in chunks:
        vec = vec_by_hash.get(fc.ch["sha256"])
        if vec is not None:
            kept_records.append(_record(fc.rel, fc.mtime, fc.ch))
            kept_vecs.append(vec)
        else:
            to_embed.append(fc)
    return kept_records, kept_vecs, to_embed


def _embed_chunks(to_embed: list[_FileChunk]) -> tuple[list[dict], list[np.ndarray], int]:
    """Embed every chunk in ``to_embed``; return ``(records, vectors, skipped)``.

    Embeddings are independent HTTP calls to local Ollama, so a thread pool
    parallelizes them. Results stay order-stable (``map`` preserves input order)
    and the worker count is bounded (default 4, env-tunable) so we don't
    saturate the daemon on a large first-time build. Falls back to serial when
    only one chunk is needed or the pool is disabled."""
    if len(to_embed) <= 1:
        return _embed_chunks_serial(to_embed)
    workers = _embed_workers()
    if workers <= 1:
        return _embed_chunks_serial(to_embed)
    return _embed_chunks_parallel(to_embed, workers)


def _embed_chunks_serial(to_embed: list[_FileChunk]) -> tuple[list[dict], list[np.ndarray], int]:
    """Serial embedding path (also the single-chunk fast path)."""
    records: list[dict] = []
    vecs: list[np.ndarray] = []
    skipped = 0
    for fc in to_embed:
        vec = ollama_embed(fc.ch["text"])
        if vec is None:
            skipped += 1
            continue
        records.append(_record(fc.rel, fc.mtime, fc.ch))
        vecs.append(np.asarray(vec, dtype=np.float32))
    return records, vecs, skipped


def _embed_chunks_parallel(
    to_embed: list[_FileChunk], workers: int
) -> tuple[list[dict], list[np.ndarray], int]:
    """Order-stable parallel embedding via ``ThreadPoolExecutor.map``.

    ``map`` yields results in input order, so records/vectors align with the
    manifest ordering the serial path produced (tests and dedup-by-sha256 don't
    care about order, but stable order keeps diffs readable)."""
    from concurrent.futures import ThreadPoolExecutor

    def _one(fc: _FileChunk) -> list[float] | None:
        return ollama_embed(fc.ch["text"])

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(zip(to_embed, pool.map(_one, to_embed), strict=True))
    return _collect_embed_results(results)


def _collect_embed_results(
    results: Iterable[tuple[_FileChunk, list[float] | None]],
) -> tuple[list[dict], list[np.ndarray], int]:
    """Reduce ordered ``(chunk, vector|None)`` pairs to records + vectors + skipped."""
    records: list[dict] = []
    vecs: list[np.ndarray] = []
    skipped = 0
    for fc, vec in results:
        if vec is None:
            skipped += 1
            continue
        records.append(_record(fc.rel, fc.mtime, fc.ch))
        vecs.append(np.asarray(vec, dtype=np.float32))
    return records, vecs, skipped


def _embed_workers() -> int:
    """Thread count for parallel embed (env ``AGENT_MEMORY_EMBED_WORKERS``, default 4).

    Bounded so a one-shot ``semindex`` doesn't fire hundreds of concurrent
    requests at the daemon. Set to 1 to force the serial path."""
    raw = os.environ.get("AGENT_MEMORY_EMBED_WORKERS", "4")
    try:
        n = int(raw)
    except ValueError:
        return 4
    return max(1, n)


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


def _stack_vectors(kept_vecs: list[np.ndarray], new_vecs: list[np.ndarray]) -> np.ndarray:
    """Vertically stack kept + new vectors; empty-aware."""
    blocks = [b for b in (kept_vecs + new_vecs) if b.shape and b.shape[0]]
    if not blocks:
        return np.empty((0, 0), dtype=np.float32)
    return np.vstack(blocks).astype(np.float32)


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
    all_chunks: list[_FileChunk]
    kept: int
    embedded: int
    embedded_rels: set[str]
    skipped: int
    orphan_count: int
    rebuild: bool
    model_changed: bool
    version_changed: bool
    idx: Path


def _stats(inp: BuildInputs) -> dict:
    """Build the serializable stats dict from accumulated build inputs."""
    return {
        "files_total": len(inp.files),
        "files_reused": len(inp.files) - len(inp.embedded_rels),
        "files_reembedded": len(inp.embedded_rels),
        "chunks": len(inp.all_chunks),
        "chunks_reused": inp.kept,
        "chunks_reembedded": inp.embedded,
        "orphans_dropped": inp.orphan_count,
        "chunks_skipped_no_ollama": inp.skipped,
        "rebuild": inp.rebuild,
        "model": DEFAULT_EMBED_MODEL,
        "model_changed": inp.model_changed,
        "index_version": INDEX_VERSION,
        "index_changed": inp.version_changed,
        "index_dir": str(inp.idx),
    }


def _load_json(path: Path) -> list[dict]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def _write_json(path: Path, data: list[dict]) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
