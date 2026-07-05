"""Tests for bank.command.read_memory — bounded output, topic read, missing bank."""

from __future__ import annotations

from pathlib import Path

from agent_memory.features.bank.command import read_memory


def _seed_lines(path: Path, n: int) -> None:
    """Write ``n`` numbered lines to ``path``."""
    path.write_text("\n".join(f"line {i}" for i in range(n)) + "\n")


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
