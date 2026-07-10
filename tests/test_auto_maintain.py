"""`maintain/auto.py` internals: staleness, budgets, index freshness.

Pure checks (no Ollama) — exercised without patching the daemon. The
`run_auto_maintain` happy path is in `test_smoke.py`; here we hit the
deterministic branches it depends on directly.
"""

from __future__ import annotations

import json
from contextlib import redirect_stdout
from io import StringIO

from agent_memory.features.bank.command import init_memory
from agent_memory.features.maintain.auto import (
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
