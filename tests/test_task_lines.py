"""Tests for shared.task_lines — active task detection and age calculation."""

from __future__ import annotations

from datetime import date, timedelta

from agent_memory.shared.task_lines import is_active_task_line, task_age_days


class TestTaskAgeDays:
    def test_valid_iso_date(self) -> None:
        today = date.today()
        five_days_ago = (today - timedelta(days=5)).isoformat()
        assert task_age_days(five_days_ago) == 5

    def test_invalid_date_returns_none(self) -> None:
        assert task_age_days("invalid-date") is None
        assert task_age_days("") is None


class TestIsActiveTaskLine:
    def test_checked_task_returns_false(self) -> None:
        assert is_active_task_line("- [x] Done task") is False
        assert is_active_task_line("* [X] Done task uppercase") is False

    def test_empty_or_comment_returns_false(self) -> None:
        assert is_active_task_line("") is False
        assert is_active_task_line("   ") is False
        assert is_active_task_line("# Heading") is False
        assert is_active_task_line("> Quote block") is False
        assert is_active_task_line("<!-- HTML comment -->") is False

    def test_historical_keywords_return_false_unless_active(self) -> None:
        assert is_active_task_line("- [ ] complete this task") is False
        assert is_active_task_line("- [ ] finalizado: task") is False
        # But if explicitly marked active, it returns True
        assert is_active_task_line("- [ ] complete this task status:active") is True

    def test_no_active_task_flag(self) -> None:
        # With no_active_task=True, must have active status keywords
        assert is_active_task_line("- [ ] Some task", no_active_task=True) is False
        assert is_active_task_line("- [ ] Some task status:active", no_active_task=True) is True
        assert is_active_task_line("- [ ] Some task status:wip", no_active_task=True) is True

    def test_completed_doc_flag(self) -> None:
        # With completed_doc=True, must be unchecked or active status
        assert is_active_task_line("- [ ] Task in some doc", completed_doc=True) is True
        assert is_active_task_line("Just text task", completed_doc=True) is False
        assert is_active_task_line("Just text task status:active", completed_doc=True) is True

    def test_task_date_filtering(self) -> None:
        today = date.today()

        # Recent task date passes
        recent_date = (today - timedelta(days=5)).isoformat()
        assert is_active_task_line(f"- [ ] Task from {recent_date}") is True

        # Old task date fails
        old_date = (today - timedelta(days=20)).isoformat()
        assert is_active_task_line(f"- [ ] Task from {old_date}") is False

        # Old task date passes if explicitly marked active
        assert is_active_task_line(f"- [ ] Task from {old_date} status:active") is True

        # Custom max_age_days threshold
        assert is_active_task_line(f"- [ ] Task from {old_date}", max_age_days=25) is True

        # Invalid date in task line defaults to True
        assert is_active_task_line("- [ ] Task from 2026-99-99") is True
