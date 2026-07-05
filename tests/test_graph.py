"""Context-graph (decisions.graph.jsonl) operations against a temp bank."""

from __future__ import annotations

from agent_memory.features.graph.command import (
    graph_add,
    graph_join,
    graph_query,
    graph_show,
    graph_stale,
    graph_supersede,
    split_csv,
)


def test_split_csv_parses_and_trims() -> None:
    assert split_csv(None) is None
    assert split_csv("") is None
    assert split_csv("a, b ,c") == ["a", "b", "c"]


def test_graph_add_query_show_roundtrip(capsys, tmp_path) -> None:
    root = tmp_path
    assert graph_add(root, "ModA", "DEPENDS_ON", "ModB", {"src": "systemPatterns.md"}) == 0
    assert graph_query(root, "ModA") == 0
    out = capsys.readouterr().out
    assert "(ModA) -[DEPENDS_ON]-> (ModB)" in out
    assert graph_show(root) == 0
    assert "1 triple" in capsys.readouterr().out


def test_graph_query_alias_aware(capsys, tmp_path) -> None:
    root = tmp_path
    graph_add(root, "AuthService", "OWNS", "TokenBucket", {"aliases": ["AuthModule", "Auth"]})
    assert graph_query(root, "AuthModule") == 0  # alias resolves
    assert "(AuthService) -[OWNS]-> (TokenBucket)" in capsys.readouterr().out


def test_graph_join_two_hop(capsys, tmp_path) -> None:
    root = tmp_path
    graph_add(root, "Svc", "DEPENDS_ON", "DB")
    graph_add(root, "DB", "OWNS", "ConnectionPool")
    assert graph_join(root, "Svc", "DEPENDS_ON", "OWNS") == 0
    assert "ConnectionPool" in capsys.readouterr().out


def test_graph_supersede_and_stale(capsys, tmp_path) -> None:
    root = tmp_path
    graph_add(root, "A", "DECIDED", "useX")
    graph_add(root, "A", "DECIDED", "useY")
    assert graph_supersede(root, "g_002", "g_001") == 0
    assert graph_stale(root) == 0
    out = capsys.readouterr().out
    assert "1 stale fact" in out
    assert "g_001" in out


def test_graph_supersede_unknown_id_returns_2(capsys, tmp_path) -> None:
    root = tmp_path
    graph_add(root, "A", "DECIDED", "useX")
    assert graph_supersede(root, "g_999", "g_001") == 2
