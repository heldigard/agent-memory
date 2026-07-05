"""CLI dispatcher (``agent_memory.cli``) — argparse building + main() routing.

Covers the previously-0% ``cli.py``: the parser builds, subcommands dispatch,
and exit codes are right. Ollama-dependent paths (semindex/semsearch) are left
to ``test_ollama``/``test_hybrid``; here we exercise the deterministic commands
(init/status/read/add/topic/search/compact/handoff/maintain --no-llm).
"""

from __future__ import annotations

import sys
from pathlib import Path

from agent_memory import cli


def _run(argv: list[str]) -> int:
    """Invoke ``cli.main()`` with a synthetic argv and return its exit code."""
    old = sys.argv
    sys.argv = ["agent-memory", *argv]
    try:
        return cli.main()
    finally:
        sys.argv = old


def test_parse_args_builds_all_subcommands() -> None:
    """The parser accepts every registered subcommand without error."""
    for cmd in (
        ["init"],
        ["status"],
        ["read"],
        ["add", "--file", "memory", "--text", "x"],
        ["topic", "--name", "x", "--text", "y"],
        ["search", "q"],
        ["semsearch", "q"],
        ["semindex"],
        ["semstatus"],
        ["semclean"],
        ["semrecall"],
        ["compact"],
        ["handoff"],
        ["maintain"],
        ["archive-topic", "slug"],
        ["coord"],
        ["graph", "show"],
        ["auto-maintain"],
        ["auto-maintain-check"],
    ):
        ns = cli.parse_args(["--root", "/tmp", *cmd])
        assert ns.command is not None


def test_cli_init_then_status(tmp_path: Path, capsys) -> None:
    assert _run(["--root", str(tmp_path), "init"]) == 0
    capsys.readouterr()
    assert _run(["--root", str(tmp_path), "status"]) == 0
    out = capsys.readouterr().out
    assert "Status: present" in out


def test_cli_add_then_keyword_search(tmp_path: Path, capsys) -> None:
    _run(["--root", str(tmp_path), "init"])
    capsys.readouterr()
    assert (
        _run(["--root", str(tmp_path), "add", "--file", "progress", "--text", "deployed api v2"])
        == 0
    )
    capsys.readouterr()
    assert _run(["--root", str(tmp_path), "search", "deployed"]) == 0
    out = capsys.readouterr().out
    assert "deployed api v2" in out


def test_cli_add_rejects_invalid_status(tmp_path: Path, capsys) -> None:
    _run(["--root", str(tmp_path), "init"])
    capsys.readouterr()
    try:
        _run(
            ["--root", str(tmp_path), "add", "--file", "memory", "--text", "x", "--status", "bogus"]
        )
    except SystemExit:
        return
    raise AssertionError("invalid status should have exited")


def test_cli_handoff_emits_section(tmp_path: Path, capsys) -> None:
    _run(["--root", str(tmp_path), "init"])
    capsys.readouterr()
    assert _run(["--root", str(tmp_path), "handoff"]) == 0
    out = capsys.readouterr().out
    assert "Session Handoff" in out


def test_cli_maintain_no_llm_writes_audit(tmp_path: Path, capsys) -> None:
    _run(["--root", str(tmp_path), "init"])
    capsys.readouterr()
    report = tmp_path / "audit.md"
    assert _run(["--root", str(tmp_path), "maintain", "--no-llm", "-o", str(report)]) == 0
    assert report.exists()
    body = report.read_text(encoding="utf-8")
    assert "Audit" in body
    assert "PROPOSE-ONLY" in body


def test_cli_graph_add_show_query(tmp_path: Path, capsys) -> None:
    _run(["--root", str(tmp_path), "init"])
    capsys.readouterr()
    assert (
        _run(
            [
                "--root",
                str(tmp_path),
                "graph",
                "add",
                "--s",
                "Auth",
                "--p",
                "OWNS",
                "--o",
                "TokenService",
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert _run(["--root", str(tmp_path), "graph", "query", "Auth"]) == 0
    out = capsys.readouterr().out
    assert "TokenService" in out


def test_cli_auto_maintain_check_json(tmp_path: Path, capsys) -> None:
    _run(["--root", str(tmp_path), "init"])
    capsys.readouterr()
    assert _run(["--root", str(tmp_path), "auto-maintain-check", "--json"]) == 0
    out = capsys.readouterr().out
    assert '"stale_files"' in out


def test_main_module_entry() -> None:
    """``__main__.py`` is wired to ``cli.main()`` (smoke import, no exec)."""
    from agent_memory import __main__

    assert callable(__main__.main)
