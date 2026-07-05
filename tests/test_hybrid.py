"""Hybrid-retrieval pure logic: tokenize, BM25 ranking, and RRF fusion.
No Ollama required — operates on in-memory manifest/vectors only."""

from __future__ import annotations

from agent_memory.features.semantic.hybrid import bm25_scores, rrf_fuse, tokenize


def test_tokenize_strips_stopwords_and_short() -> None:
    toks = tokenize("The auth flow and tokens de db")
    lower = [t.lower() for t in toks]
    assert "the" not in lower and "and" not in lower and "de" not in lower
    assert "db" not in lower  # len<=2 dropped
    assert "auth" in lower and "flow" in lower and "tokens" in lower


def test_bm25_exact_term_ranks_first() -> None:
    manifest = [
        {"text": "auth login token session"},
        {"text": "database migration script"},
        {"text": "auth token refresh flow"},
    ]
    scored = bm25_scores(manifest, "auth token")
    assert scored, "expected non-empty ranking"
    top_idx = scored[0][0]
    assert top_idx in (0, 2)  # one of the auth-token docs wins


def test_bm25_empty_when_no_query_term_matches() -> None:
    manifest = [{"text": "alpha beta"}, {"text": "gamma delta"}]
    assert bm25_scores(manifest, "zzz") == []


def test_rrf_fuse_surfaces_overlap_above_singleton() -> None:
    dense = [(0, 0.9), (1, 0.5), (2, 0.3)]
    bm25 = [(1, 5.0), (2, 4.0), (3, 1.0)]
    fused = rrf_fuse(dense, bm25, limit=4)
    # index 1 appears in BOTH retrievers -> must rank above indices that are in only one
    assert fused[0] == 1
    assert set(fused) == {0, 1, 2, 3}


def test_rrf_fuse_respects_limit() -> None:
    dense = [(i, float(i)) for i in range(10)]
    bm25 = [(i, float(i)) for i in range(10)]
    assert len(rrf_fuse(dense, bm25, limit=3)) == 3
