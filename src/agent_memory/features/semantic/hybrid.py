"""Hybrid retrieval: BM25 + dense, fused via Reciprocal Rank Fusion (RRF).

Degrades gracefully — when Ollama is down the dense list is empty but BM25
still ranks from the manifest, so recall keeps working. Optional LLM rerank
is opt-in (slow), never on the SessionStart auto path.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

import numpy as np

from agent_memory.shared.config import BM25_B, BM25_K1, DEFAULT_K, HYBRID_POOL, RERANK_TOPN, RRF_K
from agent_memory.shared.ollama import embed as ollama_embed
from agent_memory.shared.ollama import generate as ollama_generate
from agent_memory.shared.ollama import is_alive as ollama_is_alive


@dataclass
class Bm25Ctx:
    """Shared BM25 state so the per-doc helpers take a single context arg."""

    docs: list[list[str]]
    qterms: list[str]
    df: dict[str, int]
    avgdl: float
    n_docs: int


RERANK_MODEL = "qwen3.5:4b"
_TOKEN_RE = re.compile(r"\w+", re.UNICODE)
_STOPWORDS = frozenset(
    [
        "the",
        "a",
        "an",
        "and",
        "or",
        "of",
        "to",
        "in",
        "for",
        "on",
        "with",
        "is",
        "are",
        "was",
        "were",
        "be",
        "this",
        "that",
        "it",
        "as",
        "at",
        "by",
        "from",
        "into",
        "has",
        "have",
        "had",
        "not",
        "but",
        "they",
        "their",
        "there",
        "which",
        "who",
        "will",
        "can",
        "que",
        "de",
        "la",
        "el",
        "en",
        "y",
        "o",
        "un",
        "una",
        "para",
        "con",
        "por",
        "se",
        "del",
        "las",
        "los",
        "como",
        "mas",
        "pero",
        "su",
        "sus",
        "al",
        "esto",
        "eso",
        "esta",
        "estan",
        "es",
        "son",
        "fue",
        "fueron",
        "tiene",
    ]
)


def tokenize(text: str) -> list[str]:
    """Lowercased word tokens (len>2, stopword-stripped). Shared by BM25."""
    return [
        m.group(0).lower()
        for m in _TOKEN_RE.finditer(text)
        if len(m.group(0)) > 2 and m.group(0).lower() not in _STOPWORDS
    ]


def bm25_scores(
    manifest: list[dict], query: str, limit: int = HYBRID_POOL
) -> list[tuple[int, float]]:
    """BM25 ranking over manifest chunk texts. Returns top ``limit`` (index, score)."""
    docs = [tokenize(rec.get("text", "")) for rec in manifest]
    if not docs:
        return []
    qterms = tokenize(query)
    if not qterms:
        return []
    doc_sets = [set(d) for d in docs]
    df = {t: sum(1 for ds in doc_sets if t in ds) for t in set(qterms)}
    if not any(df.values()):
        return []
    avgdl = sum(len(d) for d in docs) / len(docs)
    ctx = Bm25Ctx(docs=docs, qterms=qterms, df=df, avgdl=avgdl, n_docs=len(docs))
    return _bm25_rank(ctx)[:limit]


def _bm25_rank(ctx: Bm25Ctx) -> list[tuple[int, float]]:
    """Score every non-empty doc; return ``(index, score)`` descending."""
    scored: list[tuple[int, float]] = []
    for i, d in enumerate(ctx.docs):
        if not d:
            continue
        denom = BM25_K1 * (1 - BM25_B + BM25_B * len(d) / (ctx.avgdl + 1e-9))
        tf = {w: d.count(w) for w in ctx.df if w in d}
        s = _bm25_doc_score(ctx, tf, denom)
        if s > 0:
            scored.append((i, s))
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored


def _bm25_doc_score(ctx: Bm25Ctx, tf: dict[str, int], denom: float) -> float:
    """Accumulate one doc's BM25 score across query terms."""
    s = 0.0
    for t in ctx.qterms:
        freq = tf.get(t, 0)
        if not freq:
            continue
        idf = _idf(ctx.df[t], ctx.n_docs)
        s += idf * (freq * (BM25_K1 + 1)) / (freq + denom)
    return s


def _idf(df_t: int, n_docs: int) -> float:
    return math.log(1 + (n_docs - df_t + 0.5) / (df_t + 0.5))


def dense_scores(
    vectors: np.ndarray, query: str, limit: int = HYBRID_POOL
) -> list[tuple[int, float]]:
    """Top-``limit`` ``(index, cosine)`` for the query. Empty if Ollama is down.

    Assumes ``vectors`` is already L2-normalized (``save_index`` normalizes on
    write + a ``version=v2`` sidecar guarantees it); only the query is
    normalized here."""
    if vectors.ndim != 2 or vectors.shape[0] == 0:
        return []
    q = ollama_embed(query)
    if q is None:
        return []
    qn = np.asarray(q, dtype=np.float32)
    qn = qn / (np.linalg.norm(qn) + 1e-9)
    scores = vectors @ qn
    top = np.argsort(-scores)[:limit]
    return [(int(i), float(scores[i])) for i in top]


def rrf_fuse(
    dense: list[tuple[int, float]], bm25: list[tuple[int, float]], limit: int
) -> list[int]:
    """Reciprocal Rank Fusion of two ranked lists → fused index order.

    A chunk surfacing in BOTH retrievers ranks above one in only one."""
    dense_rank = {i: r for r, (i, _) in enumerate(dense)}
    bm25_rank = {i: r for r, (i, _) in enumerate(bm25)}
    ids = set(dense_rank) | set(bm25_rank)
    return sorted(ids, key=lambda i: _rrf_score(i, dense_rank, bm25_rank), reverse=True)[:limit]


def _rrf_score(i: int, dense_rank: dict[int, int], bm25_rank: dict[int, int]) -> float:
    d = 1.0 / (RRF_K + dense_rank[i]) if i in dense_rank else 0.0
    b = 1.0 / (RRF_K + bm25_rank[i]) if i in bm25_rank else 0.0
    return d + b


def llm_relevance(query: str, text: str) -> float:
    """LLM-as-reranker relevance 0-9. Deterministic prompt → cached by ollama."""
    prompt = (
        "Rate how relevant the DOCUMENT is to the QUERY, 0 (irrelevant) to 9 "
        "(exact match). Reply with a single digit only.\n\n"
        f"QUERY: {query[:300]}\nDOCUMENT: {text[:800]}"
    )
    out = ollama_generate(prompt, model=RERANK_MODEL, temperature=0.0)
    if not out:
        return 0.0
    m = re.search(r"\d", out)
    return float(m.group()) if m else 0.0


def rerank(query: str, items: list[tuple[int, dict]]) -> list[tuple[int, float]]:
    """LLM rerank of ``(index, record)`` candidates → ``(index, score)`` order.

    On failure or a 1-item list, returns input order (RRF order) with score 0.0."""
    if len(items) <= 1 or not ollama_is_alive():
        return [(i, 0.0) for i, _ in items]
    scored = [(llm_relevance(query, rec.get("text", "")), i) for i, rec in items]
    scored.sort(key=lambda x: x[0], reverse=True)  # stable → RRF order on ties
    return [(i, s) for s, i in scored]


def hybrid_search(
    vectors: np.ndarray,
    manifest: list[dict],
    query: str,
    k: int = DEFAULT_K,
    do_rerank: bool = False,
) -> list[dict]:
    """BM25 + dense fused via RRF. Each hit carries ``method`` (dense+bm25/bm25/dense)
    and a dense ``score``. Empty only if the index is empty or no retriever matches."""
    if not manifest:
        return []
    pool = max(HYBRID_POOL, k * 4)
    dense = dense_scores(vectors, query, pool)
    bm25 = bm25_scores(manifest, query, pool)
    if not dense and not bm25:
        return []
    inp = PickInputs(
        dense=dense, bm25=bm25, manifest=manifest, query=query, k=k, do_rerank=do_rerank
    )
    chosen, rerank_scores = _pick_candidates(inp)
    return _annotate_hits(chosen, inp, rerank_scores)


@dataclass
class PickInputs:
    """Hybrid-search shared inputs: bundled so per-stage helpers take one arg."""

    dense: list
    bm25: list
    manifest: list
    query: str
    k: int
    do_rerank: bool


def _pick_candidates(inp: PickInputs) -> tuple[list[int], dict[int, float]]:
    """Pick the final candidate ids (RRF or LLM-reranked) and their rerank scores."""
    if not inp.do_rerank:
        return rrf_fuse(inp.dense, inp.bm25, inp.k), {}
    candidate_ids = rrf_fuse(inp.dense, inp.bm25, RERANK_TOPN)
    reranked = rerank(inp.query, [(i, inp.manifest[i]) for i in candidate_ids])[: inp.k]
    return [i for i, _ in reranked], dict(reranked)


def _annotate_hits(
    chosen: list[int], inp: PickInputs, rerank_scores: dict[int, float]
) -> list[dict]:
    """Build the output hit dicts with method/score tags from the pick context."""
    dense_score = dict(inp.dense)
    dense_ids = set(dense_score)
    bm25_ids = {i for i, _ in inp.bm25}
    out: list[dict] = []
    for i in chosen:
        rec = dict(inp.manifest[i])
        rec["score"] = round(dense_score.get(i, 0.0), 4)
        if i in rerank_scores:
            rec["rerank_score"] = rerank_scores[i]
        rec["method"] = (
            "dense+bm25"
            if (i in dense_ids and i in bm25_ids)
            else ("bm25" if i in bm25_ids else "dense")
        )
        out.append(rec)
    return out
