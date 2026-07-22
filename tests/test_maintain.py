"""Maintenance: handoff summary extraction and deterministic budget audit
(``--no-llm`` path; the LLM audit itself runs against a live Ollama daemon
and is exercised end-to-end in the cross-cli smoke, not here)."""

from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO

from agent_memory.features.maintain.command import (
    _archive_with_summary,
    handoff,
    maintain,
)


def _seed_bank(tmp_path) -> None:
    bank = tmp_path / ".memory-bank"
    bank.mkdir(exist_ok=True)
    (bank / "currentTask.md").write_text(
        "# Current Task\n\n## Goal\n- ship the auth refresh flow\n", encoding="utf-8"
    )
    (bank / "progress.md").write_text(
        "# Progress\n\n- 2026-07-04 shipped login\n- 2026-07-03 fixed token bug\n", encoding="utf-8"
    )
    (bank / "activeContext.md").write_text(
        "# Active Context\n\n## 2026-07-04\n- mid-deploy\n", encoding="utf-8"
    )


def test_handoff_includes_active_task_and_recent_progress(tmp_path) -> None:
    _seed_bank(tmp_path)
    buf = StringIO()
    with redirect_stdout(buf):
        handoff(tmp_path)
    out = buf.getvalue()
    assert "## Session Handoff" in out
    assert "ship the auth refresh flow" in out  # active task surfaced
    assert "activeContext.md" in out  # pointer to paste target


def test_maintain_no_llm_emits_budget_report(tmp_path) -> None:
    _seed_bank(tmp_path)
    buf = StringIO()
    with redirect_stdout(buf):
        maintain(tmp_path, apply_safe=False, no_llm=True)
    out = buf.getvalue()
    assert "Memory Bank Audit" in out
    assert "PROPOSE-ONLY" in out
    # every seeded core file gets a section
    assert "currentTask.md" in out
    assert "progress.md" in out


def test_maintain_no_llm_can_write_report_to_file(tmp_path) -> None:
    _seed_bank(tmp_path)
    out_path = tmp_path / "audit.md"
    buf = StringIO()
    with redirect_stdout(buf):
        maintain(tmp_path, apply_safe=False, no_llm=True, output=str(out_path))
    assert out_path.exists()
    assert "Memory Bank Audit" in out_path.read_text(encoding="utf-8")


def test_archive_with_summary_shrinks_when_tail_count_zero(tmp_path) -> None:
    """Regression: ``lines[-0:]`` == full list when tail_count==0 → the file used to
    GROW. The guard must yield header + note only (≤ max_lines)."""
    path = tmp_path / "systemPatterns.md"
    path.write_text("# Patterns\n" + "\n".join(f"- rule {i}" for i in range(6)), encoding="utf-8")
    before = path.read_text(encoding="utf-8").splitlines()
    buf = StringIO()
    with redirect_stdout(buf):
        # max_lines=3 → tail_count = max(0, 3 - 1 - 2) == 0 (the bug path)
        changed = _archive_with_summary(path, max_lines=3, no_llm=True)
    after = path.read_text(encoding="utf-8").splitlines()
    assert changed is True
    assert len(after) <= 3, f"file grew or stayed oversized: {len(after)} lines\n{after}"
    assert len(after) < len(before), "archive must shrink the source file"
    # archived copy holds the removed middle (archive_dir = path.parent / topics/archive)
    matches = sorted((tmp_path / "topics" / "archive").glob("systemPatterns-*.md"))
    assert matches, "archive file must exist"
    assert "rule 0" in matches[0].read_text(encoding="utf-8")


def test_archive_with_summary_topic_file_does_not_nest(tmp_path) -> None:
    """Regression: a file already inside topics/ must archive to the bank's
    canonical <bank>/topics/archive — NOT topics/topics/archive. Anchoring the
    archive dir on path.parent unconditionally double-nested topic archives
    (session-handoffs/agent-sessions), scattering durable memory no reader
    looks at (2026-07-22 cleanup)."""
    bank = tmp_path / ".memory-bank"
    topics = bank / "topics"
    topics.mkdir(parents=True)
    topic = topics / "session-handoffs.md"
    topic.write_text(
        "# Handoffs\n" + "\n".join(f"- handoff {i}" for i in range(20)), encoding="utf-8"
    )
    buf = StringIO()
    with redirect_stdout(buf):
        changed = _archive_with_summary(topic, max_lines=6, no_llm=True)
    assert changed is True

    archive = topics / "archive"
    matches = sorted(archive.glob("session-handoffs-*.md"))
    assert matches, "topic archive must land in <bank>/topics/archive"
    assert "handoff 0" in matches[0].read_text(encoding="utf-8")
    # The nested duplicate must NOT be created.
    assert not (topics / "topics").exists(), "topic file archived into topics/topics/"

    # A core file still converges on the same canonical archive dir.
    core = bank / "progress.md"
    core.write_text("# Progress\n" + "\n".join(f"- p {i}" for i in range(20)), encoding="utf-8")
    with redirect_stdout(StringIO()):
        assert _archive_with_summary(core, max_lines=6, no_llm=True) is True
    assert sorted(archive.glob("progress-*.md")), "core archive shares the canonical dir"
    assert not (bank / "topics" / "topics").exists()
