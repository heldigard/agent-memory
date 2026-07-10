"""Tests for bank.command.read_memory — bounded output, topic read, missing bank."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from agent_memory.features.bank.command import read_memory


def _seed_lines(path: Path, n: int) -> None:
    """Write ``n`` numbered lines to ``path``."""
    path.write_text("\n".join(f"line {i}" for i in range(n)) + "\n")


def _ts(delta: timedelta) -> str:
    return (datetime.now(UTC) + delta).strftime("%Y-%m-%dT%H:%M:%SZ")


class TestReadMemoryBounded:
    def test_per_file_limit(self, bank: Path, capsys) -> None:
        """Only ``per_file_lines`` lines emitted per file."""
        _seed_lines(bank / ".memory-bank" / "progress.md", 50)
        read_memory(bank, per_file_lines=5, total_lines=100)
        out = capsys.readouterr().out
        # progress.md header + 5 lines + truncation note
        assert "line 0" in out
        assert "line 4" in out
        assert "line 5" not in out

    def test_total_limit(self, bank: Path, capsys) -> None:
        """Every physical stdout line, including formatting, fits the limit."""
        for name in ("progress.md", "CONTEXT.md", "activeContext.md"):
            _seed_lines(bank / ".memory-bank" / name, 100)
        read_memory(bank, per_file_lines=20, total_lines=25)
        out = capsys.readouterr().out
        assert len(out.splitlines()) <= 25

    def test_physical_total_limit_small_budgets(self, bank: Path, capsys) -> None:
        _seed_lines(bank / ".memory-bank" / "MEMORY.md", 20)
        for total in (0, 1, 2, 3, 80):
            read_memory(bank, per_file_lines=12, total_lines=total)
            out = capsys.readouterr().out
            assert len(out.splitlines()) <= total
            if "### MEMORY.md" in out:
                lines = out.splitlines()
                heading = lines.index("### MEMORY.md")
                assert heading + 1 < len(lines)
                assert lines[heading + 1].startswith("line ")

    def test_zero_per_file_never_emits_orphan_section(self, bank: Path, capsys) -> None:
        _seed_lines(bank / ".memory-bank" / "MEMORY.md", 20)
        read_memory(bank, per_file_lines=0, total_lines=80)
        out = capsys.readouterr().out
        assert "### MEMORY.md" not in out
        assert len(out.splitlines()) <= 80

    def test_long_line_is_bounded_only_in_injected_view(self, bank: Path, capsys) -> None:
        path = bank / ".memory-bank" / "MEMORY.md"
        original = "prefix-" + ("x" * 900) + "-important-tail"
        path.write_text(original + "\n", encoding="utf-8")

        read_memory(bank, per_file_lines=12, total_lines=80)
        out = capsys.readouterr().out

        injected = next(line for line in out.splitlines() if line.startswith("prefix-"))
        assert len(injected) <= 500
        assert "[linea recortada]" in injected
        assert injected.endswith("-important-tail")
        assert path.read_text(encoding="utf-8") == original + "\n"

    def test_startup_output_has_aggregate_character_budget(self, bank: Path, capsys) -> None:
        long_line = "x" * 1000
        for name in (
            "MEMORY.md",
            "CONTEXT.md",
            "REFERENCE.md",
            "currentTask.md",
            "activeContext.md",
            "progress.md",
            "systemPatterns.md",
        ):
            (bank / ".memory-bank" / name).write_text(
                "\n".join(f"{index}-{long_line}" for index in range(20)) + "\n",
                encoding="utf-8",
            )

        read_memory(bank, per_file_lines=12, total_lines=80)
        out = capsys.readouterr().out

        assert len(out.splitlines()) <= 80
        assert len(out) <= 12_000
        assert "startup context recortado por caracteres" in out

    def test_topic_read_preserves_long_on_demand_evidence(self, bank: Path, capsys) -> None:
        topic = bank / ".memory-bank" / "topics" / "deep.md"
        evidence = "prefix-" + ("e" * 900) + "-exact-tail"
        topic.write_text(evidence + "\n", encoding="utf-8")

        read_memory(bank, per_file_lines=12, total_lines=80, topic="deep", topic_lines=5)
        out = capsys.readouterr().out

        assert evidence in out
        assert "linea recortada" not in out

    def test_missing_bank(self, tmp_path: Path, capsys) -> None:
        """No output when bank doesn't exist."""
        read_memory(tmp_path, per_file_lines=10, total_lines=100)
        out = capsys.readouterr().out
        assert out == ""

    def test_topic_read(self, bank: Path, capsys) -> None:
        """Reading a specific topic prints only that topic."""
        topic = bank / ".memory-bank" / "topics" / "auth-flow.md"
        topic.write_text("# Auth Flow\n\nDetailed auth notes here.\n")
        read_memory(bank, per_file_lines=10, total_lines=100, topic="auth-flow")
        out = capsys.readouterr().out
        assert "Auth Flow" in out
        assert "Detailed auth notes" in out

    def test_topic_not_found(self, bank: Path) -> None:
        """SystemExit when topic doesn't exist."""
        import pytest

        with pytest.raises(SystemExit, match="Topic not found"):
            read_memory(bank, per_file_lines=10, total_lines=100, topic="nonexistent")

    def test_agent_sessions_filters_stale_coordination_noise(
        self, bank: Path, capsys
    ) -> None:
        """Old auto-generated coordination rows are hidden from startup context."""
        now = _ts(timedelta())
        old = _ts(timedelta(minutes=-30))
        registry = bank / ".memory-bank" / "agent-sessions.md"
        registry.write_text(
            "# Agent Sessions\n"
            "> Auto-generated coordination registry. Do not edit manually.\n\n"
            "## Active\n"
            f"- {now} | agent:codex | pid:sid-live | branch:main | task:\"current\" | "
            f"heartbeat:{now}\n"
            f"- {now} | agent:codex | pid:pid:999999 | branch:main | task:\"None.\" | "
            f"heartbeat:{now}\n"
            f"- {old} | agent:claude | pid:pid:999999 | branch:main | task:\"old\" | "
            f"heartbeat:{old}\n\n"
            "## Recently Ended\n"
            f"- {old} | agent:gemini | pid:sid-old | branch:main | heartbeat:{old} | "
            f"status:completed | ended:{old}\n",
            encoding="utf-8",
        )

        read_memory(bank, per_file_lines=20, total_lines=100)
        out = capsys.readouterr().out
        assert "current" in out
        assert "None." not in out
        assert "old" not in out
        assert "sid-old" not in out

    def test_topic_index_filters_operational_session_topics(
        self, bank: Path, capsys
    ) -> None:
        """Default reads hide session-log topics but keep domain topics."""
        index = bank / ".memory-bank" / "topics" / "_index.md"
        index.write_text(
            "# Topic Index\n\n"
            "## Topics\n"
            "- [Foreign Sessions](foreign-sessions.md)\n"
            "- [Agent Sessions](agent-sessions.md)\n"
            "- [Auth Flow](auth-flow.md)\n",
            encoding="utf-8",
        )

        read_memory(bank, per_file_lines=20, total_lines=100)
        out = capsys.readouterr().out
        assert "auth-flow.md" in out
        assert "foreign-sessions.md" not in out
        assert "[Agent Sessions](agent-sessions.md)" not in out
