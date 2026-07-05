"""Maintenance: handoff summary extraction and deterministic budget audit
(``--no-llm`` path; the LLM audit itself runs against a live Ollama daemon
and is exercised end-to-end in the cross-cli smoke, not here)."""

from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO

from agent_memory.features.maintain.command import handoff, maintain


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
