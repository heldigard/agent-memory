"""`maintain/auto.py` internals: staleness, budgets, index freshness.

Pure checks (no Ollama) — exercised without patching the daemon. The
`run_auto_maintain` happy path is in `test_smoke.py`; here we hit the
deterministic branches it depends on directly.
"""

from __future__ import annotations

import json
from contextlib import redirect_stdout
from datetime import UTC, datetime, timedelta
from io import StringIO
from pathlib import Path

from agent_memory.features.bank.command import init_memory
from agent_memory.features.maintain.auto import (
    _collect_topic_overruns,
    _file_has_stale_entry,
    _index_is_stale,
    _over_budget,
    _parse_entry_timestamp,
    _refresh_index,
    check_budgets,
    check_index_freshness,
    check_staleness,
    run_auto_maintain,
)


def _seed(tmp_path) -> None:
    init_memory(tmp_path)


def test_check_staleness_empty_when_all_fresh(tmp_path) -> None:
    _seed(tmp_path)
    assert check_staleness(tmp_path) == []


def test_check_staleness_finds_old_unresolved_entry(tmp_path) -> None:
    _seed(tmp_path)
    (tmp_path / ".memory-bank/progress.md").write_text(
        "# Progress\n\n- 2019-01-01 | status:active | investigate ancient issue\n",
        encoding="utf-8",
    )
    (tmp_path / ".memory-bank/currentTask.md").write_text(
        "# Current Task\n\n- 2019-01-01 | status:blocked | waiting on a dependency\n",
        encoding="utf-8",
    )
    stale = check_staleness(tmp_path)
    assert stale
    assert stale == [".memory-bank/currentTask.md"]


def test_check_staleness_ignores_historical_and_topic_dates(tmp_path) -> None:
    _seed(tmp_path)
    bank = tmp_path / ".memory-bank"
    (bank / "currentTask.md").write_text(
        "# Current Task\n\n- 2019-01-01 shipped ancient thing\n",
        encoding="utf-8",
    )
    (bank / "activeContext.md").write_text(
        "# Active Context\n\n- 2019-01-01 | status:completed | old handoff\n",
        encoding="utf-8",
    )
    (bank / "REFERENCE.md").write_text(
        "# Reference\n\n- 2019-01-01 | status:live | durable runbook\n",
        encoding="utf-8",
    )
    (bank / "topics" / "archive-example.md").write_text(
        "# Archive\n\n- 2019-01-01 | status:active | historical snapshot\n",
        encoding="utf-8",
    )
    assert check_staleness(tmp_path) == []


def test_check_staleness_reports_malformed_operational_timestamp(tmp_path) -> None:
    _seed(tmp_path)
    (tmp_path / ".memory-bank/currentTask.md").write_text(
        "# Current Task\n\n- not-a-date | status:active | malformed legacy handoff\n",
        encoding="utf-8",
    )
    assert check_staleness(tmp_path) == [".memory-bank/currentTask.md"]


def test_check_budgets_returns_empty_for_fresh_bank(tmp_path) -> None:
    _seed(tmp_path)
    assert check_budgets(tmp_path) == []


def test_check_budgets_flags_topic_over_limit(tmp_path) -> None:
    _seed(tmp_path)
    topics = tmp_path / ".memory-bank" / "topics"
    topics.mkdir(exist_ok=True)
    (topics / "fat.md").write_text("# fat\n" + ("x\n" * 900), encoding="utf-8")
    over = check_budgets(tmp_path)
    assert any(item["file"] == "topics/fat.md" for item in over)


def test_check_index_freshness_skips_when_no_manifest(tmp_path) -> None:
    """No index yet -> not stale (caller decides whether to build)."""
    _seed(tmp_path)
    errors: list[str] = []
    assert check_index_freshness(tmp_path, errors) is False
    assert errors == []


def test_run_auto_maintain_no_bank_reports_error(tmp_path) -> None:
    """Without `.memory-bank` the result carries an error and stays serializable."""
    buf = StringIO()
    with redirect_stdout(buf):
        summary = run_auto_maintain(tmp_path, check_only=True)
    assert summary["errors"] == ["no .memory-bank"]
    json.dumps(summary)  # must round-trip cleanly


def test_run_auto_maintain_warns_to_stderr_on_over_budget(tmp_path, capsys) -> None:
    """Over-budget files trigger a stderr warning the hook can surface to the user."""
    _seed(tmp_path)
    # push progress.md past its 300-line hard budget so check_budgets flags it
    fat = "# Progress\n" + ("- 2026-07-01 shipped\n" * 320)
    (tmp_path / ".memory-bank/progress.md").write_text(fat, encoding="utf-8")
    run_auto_maintain(tmp_path, check_only=True)
    err = capsys.readouterr().err
    assert "Over budget" in err


# --- helpers and edge branches (coverage) ---


def test_parse_entry_timestamp_handles_empty_invalid_and_valid() -> None:
    assert _parse_entry_timestamp(None) is None
    assert _parse_entry_timestamp("") is None
    # dated but out-of-range time trips strptime's ValueError path, not the guard
    assert _parse_entry_timestamp("2026-13-99T99:99:99Z") is None
    parsed = _parse_entry_timestamp("2026-07-18T12:00:00Z")
    assert parsed == datetime(2026, 7, 18, 12, 0, 0, tzinfo=UTC)


def test_file_has_stale_entry_returns_false_on_read_error(tmp_path, monkeypatch) -> None:
    """An unreadable operational file fails closed (no stale signal), never raises."""
    _seed(tmp_path)
    path = tmp_path / ".memory-bank" / "currentTask.md"
    path.write_text("- 2019-01-01 | status:active | old\n", encoding="utf-8")
    monkeypatch.setattr(
        Path,
        "read_text",
        lambda *a, **k: (_ for _ in ()).throw(OSError("denied")),
    )
    cutoff = datetime.now(UTC) - timedelta(days=1)
    assert _file_has_stale_entry(path, cutoff) is False


def test_check_staleness_empty_when_no_bank(tmp_path) -> None:
    # tmp_path has no .memory-bank -> early return []
    assert check_staleness(tmp_path) == []


def test_check_staleness_skips_missing_operational_files(tmp_path) -> None:
    _seed(tmp_path)
    # delete two of the three operational files; the remaining one is fresh -> []
    bank = tmp_path / ".memory-bank"
    (bank / "currentTask.md").unlink()
    (bank / "activeContext.md").unlink()
    assert check_staleness(tmp_path) == []


def test_check_budgets_empty_when_no_bank(tmp_path) -> None:
    assert check_budgets(tmp_path) == []


def test_over_budget_returns_none_for_missing_file(tmp_path) -> None:
    from agent_memory.shared.text import line_count

    assert line_count(tmp_path / "absent.md") == 0
    assert _over_budget(tmp_path / "absent.md", 10) is None


def test_collect_topic_overruns_skips_archive_and_missing_dir(tmp_path) -> None:
    mb = tmp_path / ".memory-bank"
    # no topics dir at all -> early return (mutates nothing), returns None
    over: list[dict[str, object]] = []
    assert _collect_topic_overruns(mb, over) is None
    assert over == []

    topics = mb / "topics"
    topics.mkdir(parents=True)
    # archive files are exempt even when huge
    (topics / "archive-old.md").write_text("# a\n" + "x\n" * 900, encoding="utf-8")
    # a normal topic over the soft limit is flagged
    (topics / "fat.md").write_text("# fat\n" + "x\n" * 900, encoding="utf-8")
    over = []
    _collect_topic_overruns(mb, over)
    files = [str(item["file"]) for item in over]
    assert "topics/fat.md" in files
    assert all("archive-" not in f for f in files)


def test_index_is_stale_false_without_manifest(tmp_path) -> None:
    _seed(tmp_path)
    manifest = tmp_path / ".memory-bank" / ".index" / "manifest.json"
    assert _index_is_stale(manifest, tmp_path / ".memory-bank") is False


def test_refresh_index_surfaces_build_error_and_exception(tmp_path, monkeypatch) -> None:
    import agent_memory.features.semantic.index as index_mod

    errors: list[str] = []

    # build_index returns an error dict -> error recorded, returns False
    monkeypatch.setattr(index_mod, "build_index", lambda root, rebuild=False: {"error": "boom"})
    assert _refresh_index(tmp_path, errors) is False
    assert errors == ["boom"]

    # build_index raises -> caught, error recorded, returns False
    errors.clear()

    def boom(root, rebuild=False):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(index_mod, "build_index", boom)
    assert _refresh_index(tmp_path, errors) is False
    assert errors and "index refresh failed" in errors[0]


def test_check_index_freshness_reports_when_stale_and_ollama_down(tmp_path, monkeypatch) -> None:
    """Stale manifest + unreachable Ollama -> recorded error, no refresh."""
    _seed(tmp_path)
    import agent_memory.features.maintain.auto as auto_mod

    mb = tmp_path / ".memory-bank"
    manifest_dir = mb / ".index"
    manifest_dir.mkdir()
    manifest = manifest_dir / "manifest.json"
    # old manifest, then touch a memory file so _index_is_stale is True
    manifest.write_text("[]", encoding="utf-8")
    import os
    import time

    old = time.time() - 3600
    os.utime(manifest, (old, old))
    (mb / "currentTask.md").write_text("# touched\n", encoding="utf-8")

    monkeypatch.setattr(auto_mod, "ollama_is_alive", lambda timeout=2.0: False)
    monkeypatch.setattr(auto_mod, "_refresh_index", lambda root, errs: errs.append("x") or True)
    errors: list[str] = []
    assert check_index_freshness(tmp_path, errors) is False
    assert "index stale but Ollama unreachable" in errors


def test_check_index_freshness_refreshes_when_stale_and_ollama_up(tmp_path, monkeypatch) -> None:
    _seed(tmp_path)
    import agent_memory.features.maintain.auto as auto_mod

    mb = tmp_path / ".memory-bank"
    manifest_dir = mb / ".index"
    manifest_dir.mkdir()
    manifest = manifest_dir / "manifest.json"
    manifest.write_text("[]", encoding="utf-8")
    import os
    import time

    old = time.time() - 3600
    os.utime(manifest, (old, old))
    (mb / "currentTask.md").write_text("# touched\n", encoding="utf-8")

    monkeypatch.setattr(auto_mod, "ollama_is_alive", lambda timeout=2.0: True)
    monkeypatch.setattr(auto_mod, "_refresh_index", lambda root, errs: True)
    errors: list[str] = []
    assert check_index_freshness(tmp_path, errors) is True
    assert errors == []


def test_run_auto_maintain_warns_on_stale_entries(tmp_path, capsys) -> None:
    """Stale operational entries surface a stderr warning distinct from budget."""
    _seed(tmp_path)
    (tmp_path / ".memory-bank/currentTask.md").write_text(
        "- 2019-01-01 | status:active | investigate ancient issue\n", encoding="utf-8"
    )
    run_auto_maintain(tmp_path, check_only=True)
    err = capsys.readouterr().err
    assert "Stale entries" in err
    assert "currentTask.md" in err
