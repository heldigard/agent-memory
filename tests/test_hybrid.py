"""Hybrid-retrieval pure logic: tokenize, BM25 ranking, RRF fusion, dense, rerank.
No Ollama required — operates on in-memory manifest/vectors only; ollama-facing
helpers are monkeypatched."""

from __future__ import annotations

from unittest.mock import patch

import numpy as np

import agent_memory.features.semantic.hybrid as hybrid_mod
from agent_memory.features.semantic.hybrid import (
    PickInputs,
    _annotate_hits,
    _pick_candidates,
    bm25_scores,
    dense_scores,
    hybrid_search,
    llm_relevance,
    rerank,
    rrf_fuse,
    tokenize,
)


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


# --- bm25 edge branches ---


def test_bm25_empty_manifest_returns_empty() -> None:
    assert bm25_scores([], "auth token") == []


def test_bm25_query_with_only_stopwords_returns_empty() -> None:
    # query tokenizes to nothing (all stopwords / len<=2) -> []
    assert bm25_scores([{"text": "auth token"}], "the a an of to is") == []


def test_bm25_skips_empty_doc() -> None:
    # an empty-text doc is skipped during scoring but a populated one still ranks
    manifest = [{"text": ""}, {"text": "auth token refresh"}]
    scored = bm25_scores(manifest, "auth token")
    assert scored
    assert all(idx != 0 for idx, _ in scored)


# --- dense_scores branches ---


def _unit_vec(values: list[float]) -> np.ndarray:
    arr = np.asarray(values, dtype=np.float32)
    return arr / (np.linalg.norm(arr) + 1e-9)


def test_dense_scores_empty_or_non2d_vectors_return_empty() -> None:
    assert dense_scores(np.empty((0, 0)), "auth") == []
    assert dense_scores(np.asarray([1.0, 2.0, 3.0], dtype=np.float32), "auth") == []  # 1-D


def test_dense_scores_embed_failure_returns_empty() -> None:
    vectors = _unit_vec([1.0, 0.0, 0.0]).reshape(1, 3)
    with patch.object(hybrid_mod, "ollama_embed", return_value=None):
        assert dense_scores(vectors, "auth") == []


def test_dense_scores_zero_limit_returns_empty() -> None:
    vectors = _unit_vec([1.0, 0.0, 0.0]).reshape(1, 3)
    with patch.object(hybrid_mod, "ollama_embed", return_value=[1.0, 0.0, 0.0]):
        assert dense_scores(vectors, "auth", limit=0) == []


def test_dense_scores_ranks_by_cosine() -> None:
    vectors = np.stack(
        [_unit_vec([1.0, 0.0, 0.0]), _unit_vec([0.0, 1.0, 0.0]), _unit_vec([1.0, 1.0, 0.0])]
    )
    with patch.object(hybrid_mod, "ollama_embed", return_value=[1.0, 0.0, 0.0]):
        scored = dense_scores(vectors, "auth", limit=3)
    assert scored
    assert scored[0][0] == 0  # exact axis match ranks first


# --- llm_relevance + rerank ---


def test_llm_relevance_parses_digit_and_falls_back() -> None:
    with patch.object(hybrid_mod, "ollama_generate", return_value="relevance: 7"):
        assert llm_relevance("q", "doc") == 7.0
    with patch.object(hybrid_mod, "ollama_generate", return_value=""):
        assert llm_relevance("q", "doc") == 0.0
    with patch.object(hybrid_mod, "ollama_generate", return_value="no digit here"):
        assert llm_relevance("q", "doc") == 0.0


def test_rerank_passthrough_on_singleton_or_ollama_down() -> None:
    single = [(0, {"text": "a"})]
    with patch.object(hybrid_mod, "ollama_is_alive", return_value=True):
        assert rerank("q", single) == [(0, 0.0)]
    many = [(0, {"text": "a"}), (1, {"text": "b"})]
    with patch.object(hybrid_mod, "ollama_is_alive", return_value=False):
        assert rerank("q", many) == [(0, 0.0), (1, 0.0)]


def test_rerank_sorts_by_relevance() -> None:
    items = [(0, {"text": "a"}), (1, {"text": "b"}), (2, {"text": "c"})]
    scores = {"a": 1.0, "b": 9.0, "c": 5.0}

    def fake_rel(_query, text):
        return scores[text]

    with (
        patch.object(hybrid_mod, "ollama_is_alive", return_value=True),
        patch.object(hybrid_mod, "llm_relevance", side_effect=fake_rel),
    ):
        ranked = rerank("q", items)
    assert [i for i, _ in ranked] == [1, 2, 0]
    assert ranked[0][1] == 9.0


# --- hybrid_search + _pick_candidates + _annotate_hits ---


def test_hybrid_search_empty_manifest_and_no_matches() -> None:
    assert hybrid_search(np.empty((0, 0)), [], "auth") == []
    # manifest present but neither retriever matches: dense empty (embed None),
    # bm25 misses "zzz" -> both empty -> []
    manifest = [{"text": "alpha beta"}]
    with patch.object(hybrid_mod, "ollama_embed", return_value=None):
        assert hybrid_search(_unit_vec([1, 0, 0]).reshape(1, 3), manifest, "zzz") == []


def test_hybrid_search_tags_method_and_runs_rerank() -> None:
    manifest = [
        {"text": "auth token refresh", "file": "a.md"},
        {"text": "deploy pipeline", "file": "b.md"},
    ]
    vectors = np.stack([_unit_vec([1, 0]), _unit_vec([0, 1])])

    # No-rerank path: tags methods, attaches dense score
    with patch.object(hybrid_mod, "ollama_embed", return_value=[1.0, 0.0]):
        hits = hybrid_search(vectors, manifest, "auth token", k=2, do_rerank=False)
    assert hits
    assert all("method" in h for h in hits)
    assert all("score" in h for h in hits)

    # Rerank path: exercises _pick_candidates rerank branch + rerank_score tag
    with (
        patch.object(hybrid_mod, "ollama_embed", return_value=[1.0, 0.0]),
        patch.object(hybrid_mod, "ollama_is_alive", return_value=True),
        patch.object(hybrid_mod, "llm_relevance", return_value=5.0),
    ):
        hits_rr = hybrid_search(vectors, manifest, "auth token", k=2, do_rerank=True)
    assert hits_rr
    assert all("rerank_score" in h for h in hits_rr)


def test_pick_candidates_rerank_and_no_rerank_branches() -> None:
    dense = [(0, 0.9), (1, 0.5)]
    bm25 = [(0, 5.0)]
    manifest = [{"text": "a"}, {"text": "b"}]

    no_rr = PickInputs(dense=dense, bm25=bm25, manifest=manifest, query="q", k=2, do_rerank=False)
    ids, scores = _pick_candidates(no_rr)
    assert scores == {}
    assert ids[0] == 0  # index 0 in both retrievers -> fused first

    rr = PickInputs(dense=dense, bm25=bm25, manifest=manifest, query="q", k=2, do_rerank=True)
    with (
        patch.object(hybrid_mod, "ollama_is_alive", return_value=True),
        patch.object(hybrid_mod, "llm_relevance", return_value=3.0),
    ):
        ids_rr, scores_rr = _pick_candidates(rr)
    assert ids_rr
    assert scores_rr  # rerank produced scores


def test_filter_min_score_keeps_pure_bm25_and_drops_weak_dense() -> None:
    from agent_memory.features.semantic.hybrid import _filter_min_score

    hits = [
        {"text": "both", "score": 0.9, "method": "dense+bm25"},
        {"text": "weak", "score": 0.1, "method": "dense"},
        {"text": "lex", "score": 0.0, "method": "bm25"},
    ]
    filtered = _filter_min_score(hits, 0.5)
    texts = {h["text"] for h in filtered}
    assert "both" in texts
    assert "lex" in texts  # pure BM25 preserved
    assert "weak" not in texts
    assert _filter_min_score(hits, 0.0) == hits


def test_annotate_hits_tags_overlap_dense_and_bm25_only() -> None:
    inp = PickInputs(
        dense=[(0, 0.8), (1, 0.4)],
        bm25=[(0, 5.0), (2, 1.0)],
        manifest=[{"text": "a"}, {"text": "b"}, {"text": "c"}],
        query="q",
        k=3,
        do_rerank=False,
    )
    hits = _annotate_hits([0, 1, 2], inp, {})
    by_idx = {h["text"]: h for h in hits}
    assert by_idx["a"]["method"] == "dense+bm25"  # in both
    assert by_idx["b"]["method"] == "dense"  # dense only
    assert by_idx["c"]["method"] == "bm25"  # bm25 only
    assert by_idx["a"]["score"] == round(0.8, 4)
    # rerank_scores present -> tag attached
    hits_rr = _annotate_hits([0], inp, {0: 6.0})
    assert hits_rr[0]["rerank_score"] == 6.0


def test_is_pure_bm25_hit_shared_contract() -> None:
    """The exemption predicate shared by hybrid._filter_min_score and
    recall.recall: only a hit tagged ``bm25`` whose dense score is 0.0 (or
    absent) is exempt from min-score thresholding. Locking it here prevents the
    two retrieval paths from drifting back into the 2026-07-23 recall bug."""
    from agent_memory.features.semantic.hybrid import is_pure_bm25_hit

    assert is_pure_bm25_hit(0.0, "bm25") is True
    assert is_pure_bm25_hit(None, "bm25") is True  # float(None or 0.0) == 0.0
    assert is_pure_bm25_hit(0.4, "bm25") is False  # bm25 but has a real score
    assert is_pure_bm25_hit(0.0, "dense") is False  # dense is never exempt
    assert is_pure_bm25_hit(0.8, "dense+bm25") is False  # fused has a cosine
    assert is_pure_bm25_hit(0.0, None) is False  # untagged is not bm25
