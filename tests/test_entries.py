"""Entry parsing, status taxonomy, and archive guards (pure logic, no Ollama)."""

from __future__ import annotations

import os
import re

from agent_memory.features.entries.command import (
    filter_lines_for_injection,
    is_duplicate,
    is_protected_from_archive,
    is_stale_for_injection,
    now_iso,
    parse_entry,
    strip_entry_prefix,
    supersede_entry,
    topic_path,
    validate_status,
)
from agent_memory.shared import entries as entries_mod
from agent_memory.shared.entries import (
    _coord_age_seconds,
    _coord_time,
    _coord_window_minutes,
    _entry_age_hours,
    _parse_iso,
    _pid_is_alive,
    _session_pid,
    archive_window_hours,
    injection_window_hours,
    is_stale_coordination_line,
)


def test_now_iso_is_utc_zulu() -> None:
    ts = now_iso()
    assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", ts), ts
    assert ts.endswith("Z")


def test_parse_new_active_with_session() -> None:
    line = "- 2026-06-28T14:32:15Z | status:active | session:bt1ba8ulh | deploy bg"
    info = parse_entry(line)
    assert info["ts"] == "2026-06-28T14:32:15Z"
    assert info["status"] == "active"
    assert info["session"] == "bt1ba8ulh"
    assert info["text"] == "deploy bg"


def test_parse_new_completed_no_session() -> None:
    info = parse_entry("- 2026-06-28T14:35:00Z | status:completed | shipped X")
    assert info["status"] == "completed"
    assert info["session"] is None
    assert info["text"] == "shipped X"


def test_parse_legacy_progress_colon() -> None:
    info = parse_entry("- 2026-06-23: fixed bug")
    assert info["ts"] == "2026-06-23T00:00:00Z"
    assert info["status"] is None
    assert info["text"] == "fixed bug"


def test_parse_legacy_deadends_no_colon() -> None:
    info = parse_entry("- 2026-05-16 tried X failed")
    assert info["ts"] == "2026-05-16T00:00:00Z"
    assert info["text"] == "tried X failed"


def test_parse_non_entry_line() -> None:
    info = parse_entry("## Heading or prose")
    assert info["ts"] is None
    assert info["text"] == "## Heading or prose"


def test_parse_malformed_timestamp_keeps_structured_status() -> None:
    info = parse_entry("- not-a-date | status:active | malformed handoff")
    assert info["ts"] is None
    assert info["status"] == "active"


def test_parse_live_status_never_archived() -> None:
    assert is_protected_from_archive("- 2026-06-28T00:00:00Z | status:live | runbook")


def test_parse_active_protected() -> None:
    assert is_protected_from_archive("- 2026-06-28T00:00:00Z | status:active | in-flight deploy")


def test_parse_wip_protected() -> None:
    assert is_protected_from_archive("- 2026-06-28T00:00:00Z | status:wip | half-done")


def test_parse_pid_session_dead_process_not_stale_until_pid_check() -> None:
    # pid 999999 almost certainly not alive -> a stale active line with a dead pid is filtered.
    line = "- 2026-06-28T00:00:00Z | status:active | session:pid:999999 | old handoff"
    assert is_stale_for_injection(line)


def test_stale_blocked_and_malformed_active_entries_are_not_injected() -> None:
    old_blocked = "- 2019-01-01T00:00:00Z | status:blocked | old blocker"
    malformed_active = "- not-a-date | status:active | malformed handoff"
    assert is_stale_for_injection(old_blocked)
    assert is_stale_for_injection(malformed_active)


def test_filter_injection_hides_inactive_statuses_but_keeps_progress() -> None:
    current = now_iso()
    lines = [
        "# Context",
        f"- {current} | status:completed | shipped safely",
        f"- {current} | status:superseded | old model choice",
        f"- {current} | status:archived | historic trace",
        f"- {current} | status:active | current objective",
    ]
    visible = filter_lines_for_injection("activeContext.md", lines)
    assert visible == [
        "# Context",
        f"- {current} | status:completed | shipped safely",
        f"- {current} | status:active | current objective",
    ]


def test_validate_status_lowercases_and_accepts_valid() -> None:
    assert validate_status("active") == "active"
    assert validate_status(None) is None


def test_validate_status_rejects_invalid() -> None:
    try:
        validate_status("frobulating")
    except SystemExit:
        return
    raise AssertionError("invalid status should have raised SystemExit")


def test_strip_entry_prefix_returns_human_text() -> None:
    assert strip_entry_prefix("- 2026-06-23: fixed bug") == "fixed bug"
    assert strip_entry_prefix("- 2026-06-28T00:00:00Z | status:active | x") == "x"


def test_is_duplicate_detects_existing_text(tmp_path) -> None:
    p = tmp_path / "progress.md"
    p.write_text("# progress\n- 2026-06-23: fixed bug\n", encoding="utf-8")
    assert is_duplicate(p, "fixed bug")
    assert not is_duplicate(p, "different text")
    assert not is_duplicate(p, "")


def test_topic_path_slugifies(tmp_path) -> None:
    p = topic_path(tmp_path, "Auth Flow Stuff!")
    assert p.name == "auth-flow-stuff.md"


def test_supersede_entry_requires_unique_match_and_updates_status(tmp_path, capsys) -> None:
    from agent_memory.features.bank.command import add_entry, init_memory

    init_memory(tmp_path)
    add_entry(tmp_path, "progress", "Crow was the primary model", status="completed")
    capsys.readouterr()
    assert supersede_entry(tmp_path, "Crow was", file_name="progress.md") == 0
    line = next(
        line
        for line in (tmp_path / ".memory-bank" / "progress.md").read_text().splitlines()
        if "Crow was" in line
    )
    assert parse_entry(line)["status"] == "superseded"


def test_supersede_entry_refuses_ambiguous_match(tmp_path, capsys) -> None:
    from agent_memory.features.bank.command import add_entry, init_memory

    init_memory(tmp_path)
    add_entry(tmp_path, "progress", "model decision alpha", status="completed")
    add_entry(tmp_path, "activeContext", "model decision beta", status="completed")
    capsys.readouterr()
    assert supersede_entry(tmp_path, "model decision") == 2
    assert "matched 2 entries" in capsys.readouterr().err


# --- shared/entries.py internal branches (coverage) ---


def test_entry_age_hours_handles_missing_invalid_valid() -> None:
    assert _entry_age_hours(None) is None
    assert _entry_age_hours("") is None
    assert _entry_age_hours("not-a-date") is None
    age = _entry_age_hours("2026-07-18T00:00:00Z")
    assert age is not None and age >= 0


def test_coord_window_minutes_env_parse_and_fallback(monkeypatch) -> None:
    monkeypatch.delenv("MEMORY_COORD_TEST_MIN", raising=False)
    assert _coord_window_minutes("MEMORY_COORD_TEST_MIN", 7.5) == 7.5
    monkeypatch.setenv("MEMORY_COORD_TEST_MIN", "3")
    assert _coord_window_minutes("MEMORY_COORD_TEST_MIN", 7.5) == 3.0
    monkeypatch.setenv("MEMORY_COORD_TEST_MIN", "garbage")
    assert _coord_window_minutes("MEMORY_COORD_TEST_MIN", 7.5) == 7.5
    monkeypatch.setenv("MEMORY_COORD_TEST_MIN", "-2")
    assert _coord_window_minutes("MEMORY_COORD_TEST_MIN", 7.5) == 0.0


def test_coord_time_and_parse_iso_invalid() -> None:
    assert _coord_time(None) is None
    assert _coord_time("") is None
    assert _coord_time('  "bad"  ') is None
    assert _parse_iso("nonsense") is None
    parsed = _parse_iso("2026-07-18T12:00:00Z")
    assert parsed is not None and parsed.year == 2026


def test_coord_age_seconds_none_for_unparseable() -> None:
    assert _coord_age_seconds(None) is None
    assert _coord_age_seconds("not-a-date") is None
    assert _coord_age_seconds("2019-01-01T00:00:00Z") is not None


def test_archive_and_injection_window_env_overrides(monkeypatch) -> None:
    monkeypatch.setenv("MEMORY_ACTIVE_WINDOW_HOURS", "99")
    assert archive_window_hours() == 99.0
    monkeypatch.setenv("MEMORY_ACTIVE_WINDOW_HOURS", "nope")
    assert archive_window_hours() == entries_mod.DEFAULT_ARCHIVE_WINDOW_HOURS
    monkeypatch.setenv("MEMORY_INJECTION_ACTIVE_WINDOW_HOURS", "5")
    assert injection_window_hours() == 5.0
    monkeypatch.setenv("MEMORY_INJECTION_ACTIVE_WINDOW_HOURS", "nope")
    assert injection_window_hours() == entries_mod.DEFAULT_INJECTION_WINDOW_HOURS


def test_session_pid_parses_chained_pid_prefix_and_rejects_garbage() -> None:
    assert _session_pid(None) is None
    assert _session_pid("not-a-session") is None
    assert _session_pid("pid:abc") is None  # regex requires digits
    assert _session_pid("pid:123") == 123
    assert _session_pid("pid:pid:42") == 42  # (?:pid:)+ allows chained prefix


def test_pid_is_alive_self_true_dead_false(monkeypatch) -> None:
    assert _pid_is_alive(os.getpid()) is True
    assert _pid_is_alive(999_999_999) is False  # almost certainly unused

    def raise_perm(pid, sig):
        raise PermissionError("no rights")

    monkeypatch.setattr(entries_mod.os, "kill", raise_perm)
    assert _pid_is_alive(1) is True  # PermissionError => process exists, not ours

    def raise_os(pid, sig):
        raise OSError("bad")

    monkeypatch.setattr(entries_mod.os, "kill", raise_os)
    assert _pid_is_alive(1) is False


def test_is_stale_coordination_line_branches() -> None:
    # plain prose without agent: marker -> never stale
    assert is_stale_coordination_line("# heading") is False
    # agent line with no parseable ts -> not stale (left intact)
    assert is_stale_coordination_line("- not-a-date | agent:claude") is False
    # completed with an unparseable ended -> age None -> stale
    assert (
        is_stale_coordination_line(
            "- 2026-07-19T00:00:00Z | agent:claude | status:completed | ended:bad"
        )
        is True
    )
    # completed long ago -> stale
    assert (
        is_stale_coordination_line(
            "- 2026-07-19T00:00:00Z | agent:claude | status:completed | ended:2019-01-01T00:00:00Z"
        )
        is True
    )
    # active with a dead pid -> stale regardless of heartbeat
    assert (
        is_stale_coordination_line(
            "- 2026-07-19T00:00:00Z | agent:claude | pid:999999999 | status:active "
            '| heartbeat:2026-07-19T00:00:00Z | task:"work"'
        )
        is True
    )
    # active with an unparseable heartbeat -> age None -> stale
    assert (
        is_stale_coordination_line(
            "- 2026-07-19T00:00:00Z | agent:claude | status:active | heartbeat:bad"
        )
        is True
    )
    # empty active (no task claim) with a stale pid: prefix -> stale past 60s
    assert (
        is_stale_coordination_line(
            "- 2019-01-01T00:00:00Z | agent:claude | pid:pid:999999999 | status:active | task:none."
        )
        is True
    )
    # empty active whose pid field does not resolve to a real PID still trips
    # the 60s "empty active" guard (not the dead-pid branch)
    assert (
        is_stale_coordination_line(
            "- 2019-01-01T00:00:00Z | agent:claude | pid:pid: | status:active | task:none."
        )
        is True
    )


def test_filter_lines_for_injection_env_bypass_and_topics(monkeypatch) -> None:
    lines = ["- 2019-01-01T00:00:00Z | status:superseded | old"]
    # opt-out: env disables filtering entirely
    monkeypatch.setenv("MEMORY_FILTER_STALE_ACTIVE", "0")
    assert filter_lines_for_injection("activeContext.md", lines) == lines
    monkeypatch.setenv("MEMORY_FILTER_STALE_ACTIVE", "1")

    # topics/_index.md strips operational-topic reference rows but keeps others
    index_lines = [
        "- [auth](auth.md)",
        "- [sessions](agent-sessions.md)",
        "- [handoffs](session-handoffs.md)",
    ]
    filtered = filter_lines_for_injection("topics/_index.md", index_lines)
    assert "- [auth](auth.md)" in filtered
    assert "agent-sessions.md" not in filtered
    assert "session-handoffs.md" not in filtered

    # agent-sessions.md drops stale registry records
    active_lines = [
        "- 2019-01-01T00:00:00Z | agent:claude | status:active | heartbeat:bad",
        "# keep",
    ]
    sessions = filter_lines_for_injection("agent-sessions.md", active_lines)
    assert sessions == ["# keep"]


def test_is_protected_from_archive_completed_recent_vs_old() -> None:
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC)

    def _ts(delta: timedelta) -> str:
        return (now - delta).strftime("%Y-%m-%dT%H:%M:%SZ")

    recent = f"- {_ts(timedelta(minutes=1))} | status:completed | shipped just now"
    assert is_protected_from_archive(recent) is True  # within archive window
    old = f"- {_ts(timedelta(days=400))} | status:completed | shipped long ago"
    assert is_protected_from_archive(old) is False  # past window -> archivable
    undated = "- not-a-date | status:completed | unknown age"
    assert is_protected_from_archive(undated) is True  # unknown age -> protect
    # a historical status (not in NEVER_ARCHIVED, not completed) is archivable
    assert is_protected_from_archive("- 2019-01-01T00:00:00Z | status:superseded | old") is False
    assert is_protected_from_archive("- prose with no status") is False


def test_is_duplicate_missing_file(tmp_path) -> None:
    assert is_duplicate(tmp_path / "absent.md", "anything") is False


def test_supersede_entry_legacy_date_only_line(tmp_path, capsys) -> None:
    """Legacy `- YYYY-MM-DD text` entries (no `|`/`:` after the date) must
    actually get the superseded status — the date-anchored regex used to leave
    them unchanged while the CLI still reported success."""
    from agent_memory.features.bank.command import init_memory

    init_memory(tmp_path)
    progress = tmp_path / ".memory-bank" / "progress.md"
    progress.write_text("# progress\n\n- 2026-06-28 plain legacy text\n", encoding="utf-8")
    assert supersede_entry(tmp_path, "plain legacy", file_name="progress.md") == 0
    line = next(line for line in progress.read_text().splitlines() if "plain legacy" in line)
    assert parse_entry(line)["status"] == "superseded"
