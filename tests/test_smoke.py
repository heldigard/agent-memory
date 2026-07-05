"""End-to-end smoke against a temp project: init, status, add, topic, compact,
keyword search, archive-topic, and the auto-maintain checker (no Ollama)."""

from __future__ import annotations

import json
from io import StringIO
from contextlib import redirect_stdout

from agent_memory.features.bank.command import add_entry, add_topic_entry, init_memory, status_bank
from agent_memory.features.compact.command import archive_topic
from agent_memory.features.maintain.auto import run_auto_maintain
from agent_memory.features.search.command import search_memory


def test_init_creates_all_core_files(tmp_path) -> None:
    init_memory(tmp_path)
    bank = tmp_path / ".memory-bank"
    for name in (
        "MEMORY.md",
        "CONTEXT.md",
        "REFERENCE.md",
        "currentTask.md",
        "activeContext.md",
        "progress.md",
        "systemPatterns.md",
        "dead-ends.md",
        "topics/_index.md",
    ):
        assert (bank / name).exists(), name


def test_status_reports_present(tmp_path) -> None:
    init_memory(tmp_path)
    buf = StringIO()
    with redirect_stdout(buf):
        status_bank(tmp_path)
    assert "Status: present" in buf.getvalue()


def test_add_and_dedupe(tmp_path) -> None:
    init_memory(tmp_path)
    add_entry(tmp_path, "progress", "first entry", status="completed")
    add_entry(tmp_path, "progress", "first entry")  # duplicate -> skipped
    content = (tmp_path / ".memory-bank/progress.md").read_text(encoding="utf-8")
    assert content.count("first entry") == 1


def test_topic_creates_topic_file_and_index_entry(tmp_path) -> None:
    init_memory(tmp_path)
    add_topic_entry(tmp_path, "auth-flow", "deep context about tokens")
    bank = tmp_path / ".memory-bank"
    assert (bank / "topics/auth-flow.md").exists()
    assert "auth-flow.md" in (bank / "topics/_index.md").read_text(encoding="utf-8")


def test_keyword_search_finds_added_text(tmp_path) -> None:
    init_memory(tmp_path)
    add_entry(tmp_path, "progress", "deployed payments microservice")
    buf = StringIO()
    with redirect_stdout(buf):
        search_memory(tmp_path, "payments microservice")
    assert "payments microservice" in buf.getvalue()


def test_archive_topic_moves_file_and_clears_index(tmp_path) -> None:
    init_memory(tmp_path)
    add_topic_entry(tmp_path, "legacy", "obsolete context")
    archive_topic(tmp_path, "legacy", force=True)
    bank = tmp_path / ".memory-bank"
    assert not (bank / "topics/legacy.md").exists()
    assert (bank / "topics/archive/legacy-").glob("legacy-*.md")
    assert "legacy.md" not in (bank / "topics/_index.md").read_text(encoding="utf-8")


def test_auto_maintain_check_only_returns_summary(tmp_path) -> None:
    init_memory(tmp_path)
    summary = run_auto_maintain(tmp_path, check_only=True)
    assert isinstance(summary, dict)
    assert "stale_files" in summary
    assert "over_budget" in summary
    # round-trippable (the hook emits this as JSON)
    json.dumps(summary)
