"""Entry parsing, status taxonomy, and archive guards (pure logic, no Ollama)."""

from __future__ import annotations

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
    lines = [
        "# Context",
        "- 2026-07-10T12:00:00Z | status:completed | shipped safely",
        "- 2026-07-10T12:00:00Z | status:superseded | old model choice",
        "- 2026-07-10T12:00:00Z | status:archived | historic trace",
        "- 2026-07-10T12:00:00Z | status:active | current objective",
    ]
    visible = filter_lines_for_injection("activeContext.md", lines)
    assert visible == [
        "# Context",
        "- 2026-07-10T12:00:00Z | status:completed | shipped safely",
        "- 2026-07-10T12:00:00Z | status:active | current objective",
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
