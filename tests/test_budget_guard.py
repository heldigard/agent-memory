"""Stop-hook budget guard: warnings fire at 80% (yellow) and >=100% (red),
archives are exempt, and the _index uses the small topic-index budget."""

from __future__ import annotations

from pathlib import Path

from agent_memory.hooks.budget_guard import collect_warnings, format_warning
from agent_memory.shared.config import TOPIC_INDEX_LIMIT


def _make_bank(tmp_path: Path) -> Path:
    bank = tmp_path / ".memory-bank"
    bank.mkdir()
    return bank


def test_format_warning_levels() -> None:
    assert format_warning("f.md", 50, 100) == ""  # under threshold
    assert "YELLOW" in format_warning("f.md", 85, 100)
    assert "RED" in format_warning("f.md", 100, 100)


def test_no_warnings_on_empty_bank(tmp_path) -> None:
    assert collect_warnings(_make_bank(tmp_path)) == []


def test_red_warning_for_over_budget_core_file(tmp_path) -> None:
    bank = _make_bank(tmp_path)
    (bank / "MEMORY.md").write_text(
        "# m\n" + "\n".join(f"line {i}" for i in range(200)) + "\n", encoding="utf-8"
    )
    warnings = collect_warnings(bank)
    assert any("MEMORY.md" in w and "RED" in w for w in warnings)


def test_yellow_warning_near_80pct(tmp_path) -> None:
    bank = _make_bank(tmp_path)
    # currentTask.md budget is 80; 65 lines = ~81% -> yellow
    (bank / "currentTask.md").write_text(
        "# t\n" + "\n".join(f"- item {i}" for i in range(64)) + "\n", encoding="utf-8"
    )
    warnings = collect_warnings(bank)
    assert any("currentTask.md" in w and "YELLOW" in w for w in warnings)


def test_topic_uses_soft_limit_and_index_uses_small_limit(tmp_path) -> None:
    bank = _make_bank(tmp_path)
    topics = bank / "topics"
    topics.mkdir()
    # a slug topic well under 80% of the soft limit -> no warning
    (topics / "deep.md").write_text(
        "# d\n" + "\n".join("x" for _ in range(100)) + "\n", encoding="utf-8"
    )
    # _index.md over its small (80) budget -> red
    (topics / "_index.md").write_text(
        "# idx\n" + "\n".join(f"- t{i}" for i in range(TOPIC_INDEX_LIMIT + 5)) + "\n",
        encoding="utf-8",
    )
    warnings = collect_warnings(bank)
    assert any("_index.md" in w for w in warnings)
    assert not any("deep.md" in w for w in warnings)


def test_archive_topic_files_are_exempt(tmp_path) -> None:
    bank = _make_bank(tmp_path)
    topics = bank / "topics"
    topics.mkdir()
    (topics / "archive-2026-07-04.md").write_text("# huge\n" + "x\n" * 5000, encoding="utf-8")
    assert collect_warnings(bank) == []
