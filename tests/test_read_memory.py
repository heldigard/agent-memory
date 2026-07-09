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
        """Emission stops after ``total_lines``."""
        for name in ("progress.md", "CONTEXT.md", "activeContext.md"):
            _seed_lines(bank / ".memory-bank" / name, 100)
        read_memory(bank, per_file_lines=20, total_lines=25)
        out = capsys.readouterr().out
        lines = [ln for ln in out.splitlines() if ln.startswith("line ")]
        assert len(lines) <= 25

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
