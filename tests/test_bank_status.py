"""bank.command status, staleness, and append-branch coverage.

Init creates the structure; read_memory is covered in test_read_memory.py.
Here: status_bank (text + --json), status_data (missing/present/over-budget),
staleness scan + threshold resolver, add_entry new-file/session branches, and
update_topic_index bootstrap.
"""

from __future__ import annotations

import json
import re
from datetime import date, timedelta
from pathlib import Path

from agent_memory.features.bank.command import (
    _cap_startup_chars,
    _collect_stale,
    _collect_stale_json,
    _parse_line_date,
    _resolve_threshold_days,
    _stale_lines,
    add_entry,
    add_topic_entry,
    init_memory,
    status_bank,
    status_data,
    update_topic_index,
)

_DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")


def _seed(tmp_path: Path) -> Path:
    init_memory(tmp_path)
    return tmp_path


def test_status_data_missing_bank(tmp_path: Path) -> None:
    data = status_data(tmp_path)
    assert data["exists"] is False
    assert "files" not in data


def test_status_data_reports_present_and_missing_core(tmp_path: Path) -> None:
    _seed(tmp_path)
    # delete one core file so the present:False branch shows
    (tmp_path / ".memory-bank" / "REFERENCE.md").unlink()
    data = status_data(tmp_path)
    by_name = {f["name"]: f for f in data["files"]}
    assert by_name["REFERENCE.md"]["present"] is False
    assert by_name["progress.md"]["present"] is True
    assert "flag" in by_name["progress.md"]


def test_status_data_flags_over_budget_topic(tmp_path: Path) -> None:
    _seed(tmp_path)
    fat = tmp_path / ".memory-bank" / "topics" / "fat.md"
    fat.write_text("# fat\n" + "x\n" * 900, encoding="utf-8")
    data = status_data(tmp_path)
    topic = next(t for t in data["topics"]["items"] if t["name"] == "fat.md")
    assert topic["over_limit"] is True


def test_status_bank_json_and_text_render(tmp_path: Path, capsys) -> None:
    _seed(tmp_path)
    capsys.readouterr()  # drain init_memory output so only the JSON remains
    status_bank(tmp_path, json_out=True)
    snapshot = json.loads(capsys.readouterr().out)
    assert snapshot["exists"] is True

    status_bank(tmp_path, json_out=False)
    out = capsys.readouterr().out
    assert "Status: present" in out


def test_status_bank_missing_renders_status_missing(tmp_path: Path, capsys) -> None:
    status_bank(tmp_path)
    assert "Status: missing" in capsys.readouterr().out


def test_status_bank_missing_core_file_line(tmp_path: Path, capsys) -> None:
    _seed(tmp_path)
    (tmp_path / ".memory-bank" / "progress.md").unlink()
    status_bank(tmp_path)
    assert "progress.md: missing" in capsys.readouterr().out


def test_status_bank_more_than_ten_topics(tmp_path: Path, capsys) -> None:
    _seed(tmp_path)
    topics = tmp_path / ".memory-bank" / "topics"
    for i in range(12):
        (topics / f"t{i}.md").write_text(f"# t{i}\nbody\n", encoding="utf-8")
    status_bank(tmp_path)
    out = capsys.readouterr().out
    assert "more topics" in out


def test_status_bank_renders_stale_entries(tmp_path: Path, capsys) -> None:
    _seed(tmp_path)
    (tmp_path / ".memory-bank" / "progress.md").write_text(
        "- 2019-01-01 shipped ancient thing a long time ago indeed\n", encoding="utf-8"
    )
    status_bank(tmp_path)
    out = capsys.readouterr().out
    assert "staleness" in out
    assert "2019-01-01" in out


def test_parse_line_date_invalid_and_valid() -> None:
    assert _parse_line_date("no date here", _DATE_RE) is None
    # Feb 30 is not a real date -> ValueError -> None
    assert _parse_line_date("2026-02-30 junk", _DATE_RE) is None
    assert _parse_line_date("2026-07-18 entry", _DATE_RE) == date(2026, 7, 18)


def test_resolve_threshold_days_override_env_and_fallback(monkeypatch) -> None:
    assert _resolve_threshold_days(7) == 7  # explicit override wins
    monkeypatch.setenv("MEMORY_STALENESS_DAYS", "30")
    assert _resolve_threshold_days(None) == 30
    monkeypatch.setenv("MEMORY_STALENESS_DAYS", "garbage")
    assert _resolve_threshold_days(None) == 14


def test_stale_lines_and_collect_stale(tmp_path: Path) -> None:
    _seed(tmp_path)
    progress = tmp_path / ".memory-bank" / "progress.md"
    progress.write_text(
        "- 2019-01-01 old enough to be stale here\n- 2026-07-18 fresh entry today\n",
        encoding="utf-8",
    )
    cutoff = date.today() - timedelta(days=14)
    stale = _stale_lines("progress.md", progress, _DATE_RE, cutoff)
    assert len(stale) == 1
    assert stale[0][0] == "progress.md"

    memory = tmp_path / ".memory-bank"
    # delete activeContext so the not-exists continue branch is exercised
    (memory / "activeContext.md").unlink()
    collected = _collect_stale(memory, _DATE_RE, cutoff)
    assert any(name == "progress.md" for name, _, _ in collected)


def test_collect_stale_json_shape(tmp_path: Path) -> None:
    _seed(tmp_path)
    (tmp_path / ".memory-bank" / "progress.md").write_text(
        "- 2019-01-01 ancient stale entry with enough text\n", encoding="utf-8"
    )
    rows = _collect_stale_json(tmp_path / ".memory-bank", None)
    assert rows
    assert {"file", "date", "snippet"} <= set(rows[0])


def test_add_entry_creates_new_file_and_appends_session(tmp_path: Path, capsys) -> None:
    init_memory(tmp_path)
    # target a brand-new arbitrary md file -> create branch
    add_entry(tmp_path, "custom.md", "first custom note", status="active", session="sid-1")
    path = tmp_path / ".memory-bank" / "custom.md"
    assert path.exists()
    content = path.read_text(encoding="utf-8")
    assert "first custom note" in content
    assert "status:active" in content
    assert "session:sid-1" in content


def test_update_topic_index_bootstraps_missing_index(tmp_path: Path) -> None:
    memory = tmp_path / ".memory-bank"
    memory.mkdir()
    (memory / "topics").mkdir()
    # _index.md absent -> created with the bootstrap header
    update_topic_index(memory, "auth-flow", "Auth Flow")
    idx = memory / "topics" / "_index.md"
    assert idx.exists()
    assert "(auth-flow.md)" in idx.read_text(encoding="utf-8")
    # second call must not duplicate
    update_topic_index(memory, "auth-flow", "Auth Flow")
    assert idx.read_text(encoding="utf-8").count("(auth-flow.md)") == 1


def test_add_topic_entry_with_status_header(tmp_path: Path) -> None:
    init_memory(tmp_path)
    add_topic_entry(tmp_path, "deploy notes", "rolled out v2", status="completed")
    path = tmp_path / ".memory-bank" / "topics" / "deploy-notes.md"
    content = path.read_text(encoding="utf-8")
    assert "status:completed" in content
    assert "rolled out v2" in content


def test_cap_startup_chars_removes_orphan_headings() -> None:
    # A heading that fits, followed by a line that overflows the cap, leaves the
    # heading as a trailing orphan: remove_orphans must pop it and the elision
    # marker is appended within budget.
    lines = ["keep this content line", "### OrphanHeading", "x" * 12_000]
    capped = _cap_startup_chars(lines)
    joined = "\n".join(capped)
    assert len(joined) <= 12_000
    assert "### OrphanHeading" not in capped
    assert "keep this content line" in capped


def test_read_memory_topic_truncation(bank: Path, capsys) -> None:
    from agent_memory.features.bank.command import read_memory

    topic = bank / ".memory-bank" / "topics" / "long.md"
    topic.write_text(
        "# Long\n" + "\n".join(f"line {i}" for i in range(200)) + "\n", encoding="utf-8"
    )
    read_memory(bank, per_file_lines=10, total_lines=100, topic="long", topic_lines=5)
    out = capsys.readouterr().out
    assert "line 0" in out
    assert "total lines" in out
