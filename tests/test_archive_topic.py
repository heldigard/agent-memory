"""Tests for archive_topic — broken-link guard and no-force abort."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_memory.features.compact.command import archive_topic


class TestArchiveTopic:
    def test_force_archives_without_check(self, bank: Path) -> None:
        """--force skips broken-link check and archives."""
        topic = bank / ".memory-bank" / "topics" / "my-topic.md"
        topic.write_text("# My Topic\n\nSome content.\n")
        archive_topic(bank, "my-topic", force=True)
        assert not topic.exists()
        archives = list((bank / ".memory-bank" / "topics" / "archive").glob("my-topic-*"))
        assert len(archives) == 1

    def test_no_force_aborts_on_broken_ref(self, bank: Path) -> None:
        """Without --force, refuses if active files reference the topic."""
        topic = bank / ".memory-bank" / "topics" / "auth.md"
        topic.write_text("# Auth\n\nNotes.\n")
        # Add a reference to auth.md in MEMORY.md
        mem = bank / ".memory-bank" / "MEMORY.md"
        mem.write_text("# Memory\n\nSee [auth](auth.md) for details.\n")
        with pytest.raises(SystemExit):
            archive_topic(bank, "auth", force=False)
        # Topic should still exist
        assert topic.exists()

    def test_no_force_succeeds_when_no_refs(self, bank: Path) -> None:
        """Without --force, archives cleanly when no active refs exist."""
        topic = bank / ".memory-bank" / "topics" / "orphan.md"
        topic.write_text("# Orphan\n\nNo references.\n")
        archive_topic(bank, "orphan", force=False)
        assert not topic.exists()

    def test_topic_not_found(self, bank: Path) -> None:
        """SystemExit when topic doesn't exist."""
        with pytest.raises(SystemExit, match="Topic not found"):
            archive_topic(bank, "nonexistent", force=True)

    def test_index_entry_removed(self, bank: Path) -> None:
        """The _index.md entry is removed after archiving."""
        topic = bank / ".memory-bank" / "topics" / "temp.md"
        topic.write_text("# Temp\n\nTemporary.\n")
        index = bank / ".memory-bank" / "topics" / "_index.md"
        index.write_text("# Topic Index\n## Topics\n- [Temp](temp.md)\n")
        archive_topic(bank, "temp", force=True)
        content = index.read_text()
        assert "temp.md" not in content

    def test_rearchive_appends(self, bank: Path) -> None:
        """Archiving same slug twice appends to the existing archive."""
        topic = bank / ".memory-bank" / "topics" / "rounds.md"
        topic.write_text("# Rounds v1\n")
        archive_topic(bank, "rounds", force=True)
        # Create again and archive
        topic.write_text("# Rounds v2\n")
        archive_topic(bank, "rounds", force=True)
        archives = list((bank / ".memory-bank" / "topics" / "archive").glob("rounds-*"))
        assert len(archives) == 1
        content = archives[0].read_text()
        assert "Rounds v1" in content
        assert "Rounds v2" in content
