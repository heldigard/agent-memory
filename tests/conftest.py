"""Shared fixtures for agent-memory tests."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture()
def bank(tmp_path: Path) -> Path:
    """Create a minimal ``.memory-bank/`` with core files and topics dir."""
    mb = tmp_path / ".memory-bank"
    mb.mkdir()
    (mb / "topics").mkdir()
    (mb / "MEMORY.md").write_text("# Project Memory\n\n## Topics\n")
    (mb / "CONTEXT.md").write_text("# Context\n")
    (mb / "activeContext.md").write_text("# Active Context\n")
    (mb / "progress.md").write_text("# Progress\n")
    (mb / "currentTask.md").write_text("# Current Task\n")
    (mb / "topics" / "_index.md").write_text("# Topic Index\n## Topics\n")
    return tmp_path
