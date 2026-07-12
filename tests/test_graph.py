"""Context-graph (decisions.graph.jsonl) operations against a temp bank."""

from __future__ import annotations

import json

from agent_memory.features.graph.command import (
    graph_add,
    graph_join,
    graph_query,
    graph_show,
    graph_stale,
    graph_supersede,
)
from agent_memory.shared.graph import parse_graph_lines
from agent_memory.shared.text import split_csv


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


def test_graph_rewrite_is_atomic_no_tmp_litter(tmp_path) -> None:
    """Supersede rewrites the whole file; it must go through atomic_write_text
    (no staging tmp left behind, content intact)."""
    root = tmp_path
    graph_add(root, "A", "DECIDED", "useX")
    graph_add(root, "A", "DECIDED", "useY")
    assert graph_supersede(root, "g_002", "g_001") == 0
    bank = root / ".memory-bank"
    assert not list(bank.glob(".*tmp*")), "atomic write left staging litter"
    lines = (bank / "decisions.graph.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2


def test_parse_graph_lines_skips_invalid_core_and_normalizes_metadata() -> None:
    rows, issues = parse_graph_lines(
        [
            '{"id":"g_001","s":"A","p":"OWNS","o":"B","extra":7}',
            "[]",
            '{"id":"g_002","s":"A","p":"OWNS"}',
            (
                '{"id":"g_003","s":"A","p":"OWNS","o":"C",'
                '"aliases":"Alias","supersedes":["g_001",null,""]}'
            ),
            '{"id":"g_004","s":"A","p":"OWNS","o":"D","aliases":null}',
            "not-json",
        ]
    )

    assert [row["id"] for row in rows] == ["g_001", "g_003", "g_004"]
    assert rows[0]["extra"] == 7
    assert rows[0]["aliases"] == []
    assert rows[1]["aliases"] == []
    assert rows[1]["supersedes"] == ["g_001"]
    assert rows[2]["aliases"] == []
    assert {(issue.kind, issue.action) for issue in issues} == {
        ("json", "skipped"),
        ("schema", "skipped"),
        ("schema", "normalized"),
    }


def test_graph_mixed_corrupt_rows_remains_usable_and_allocates_next_id(capsys, tmp_path) -> None:
    graph = tmp_path / ".memory-bank" / "decisions.graph.jsonl"
    graph.parent.mkdir()
    graph.write_text(
        json.dumps({"id": "g_007", "s": "A", "p": "OWNS", "o": "B", "aliases": "bad"}) + "\nnull\n",
        encoding="utf-8",
    )

    assert graph_query(tmp_path, "A") == 0
    assert graph_add(tmp_path, "B", "OWNS", "C") == 0

    captured = capsys.readouterr()
    assert "g_007" in captured.out
    assert "added g_008" in captured.out
    assert "normalizing graph line 1" in captured.err
    assert "skipping graph line 2" in captured.err
