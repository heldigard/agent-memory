"""Dense (cosine) search and keyword fallback over the semantic index."""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np

from agent_memory.shared.config import DEFAULT_K, MIN_SCORE
from agent_memory.shared.ollama import embed as ollama_embed
from agent_memory.shared.paths import iter_memory_files


def search(root: Path, query: str, k: int = DEFAULT_K, min_score: float = MIN_SCORE) -> list[dict]:
    """Semantic search via cosine similarity. Returns up to ``k`` records with
    a ``score`` field, or ``[]`` on failure (caller may fall back to keyword)."""
    from agent_memory.features.semantic.index import index_dir, load_index

    vectors, manifest = load_index(index_dir(root))
    if not manifest or vectors.shape[0] == 0:
        return []
    q = ollama_embed(query)
    if q is None:
        return []
    return _rank_dense(vectors, manifest, np.asarray(q, dtype=np.float32), k, min_score)


def _rank_dense(
    vectors: np.ndarray, manifest: list[dict], q: np.ndarray, k: int, min_score: float
) -> list[dict]:
    """Top-k cosine-ranked records above ``min_score``.

    Assumes ``vectors`` is already L2-normalized (saved that way at index time);
    only the query vector is normalized here."""
    qn = q / (np.linalg.norm(q) + 1e-9)
    scores = vectors @ qn
    out: list[dict] = []
    for i in np.argsort(-scores)[:k]:
        s = float(scores[i])
        if s < min_score:
            continue
        rec = dict(manifest[i])
        rec["score"] = round(s, 4)
        out.append(rec)
    return out


def keyword_fallback(root: Path, query: str, k: int = DEFAULT_K) -> list[dict]:
    """Grep-style fallback when Ollama is unavailable. No calibrated scores."""
    from agent_memory.shared.paths import bank_dir

    memory = bank_dir(root)
    if not memory.exists():
        return []
    terms = [t.lower() for t in re.findall(r"[\w.-]+", query) if len(t) > 2]
    if not terms:
        return []
    cap = k * 4
    hits: list[dict] = []
    for rel, lineno, line in _iter_keyword_lines(memory):
        if not _any_term(line, terms):
            continue
        hits.append(_fallback_hit(rel, lineno, line))
        if len(hits) >= cap:
            return hits[:cap]
    return hits


def _iter_keyword_lines(memory: Path):
    """Yield ``(relpath, lineno, line)`` for every line in core + topic files."""
    for path in iter_memory_files(memory):
        rel = str(path.relative_to(memory))
        for lineno, line in enumerate(
            path.read_text(encoding="utf-8", errors="replace").splitlines(), 1
        ):
            yield rel, lineno, line


def _any_term(line: str, terms: list[str]) -> bool:
    lower = line.lower()
    return any(t in lower for t in terms)


def _fallback_hit(rel: str, lineno: int, line: str) -> dict:
    return {
        "file": rel,
        "start": lineno,
        "end": lineno,
        "heading": "",
        "text": line.strip()[:300],
        "score": 0.0,
        "fallback": True,
    }
