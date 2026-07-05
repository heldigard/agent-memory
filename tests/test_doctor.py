"""``agent-memory doctor`` — health-check findings (budgets, refs, PIDs, index).

No Ollama required: index checks degrade to the "no index" info path, and the
collision / shape / dead-PID / broken-ref checks are pure logic over a tmp bank.
"""

from __future__ import annotations

import json
from pathlib import Path

from agent_memory.features.doctor.command import Finding, run_doctor


def _seed_min(tmp_path: Path) -> Path:
    """A minimal healthy bank: one core file, one self-consistent topic ref."""
    bank = tmp_path / ".memory-bank"
    topics = bank / "topics"
    topics.mkdir(parents=True)
    (bank / "MEMORY.md").write_text("# Memory\n", encoding="utf-8")
    (topics / "auth-flow.md").write_text("# Auth Flow\n", encoding="utf-8")
    (bank / "CONTEXT.md").write_text(
        "See [[auth-flow]] for the login token flow.\n", encoding="utf-8"
    )
    return tmp_path


def test_doctor_healthy_bank_emits_no_errors(tmp_path: Path) -> None:
    root = _seed_min(tmp_path)
    findings = run_doctor(root)
    assert findings, "expected at least the no-index info finding"
    assert not any(f.severity == "error" for f in findings), findings


def test_doctor_missing_bank_is_error(tmp_path: Path) -> None:
    findings = run_doctor(tmp_path / "nope")
    assert len(findings) == 1
    assert findings[0].severity == "error"
    assert "no memory bank" in findings[0].detail


def test_doctor_flags_broken_ref(tmp_path: Path) -> None:
    root = _seed_min(tmp_path)
    # Reference a topic that does not exist.
    (root / ".memory-bank" / "CONTEXT.md").write_text(
        "See [[missing-topic]] and (ghost.md) for details.\n", encoding="utf-8"
    )
    findings = run_doctor(root)
    broken = [f for f in findings if f.check == "broken-ref"]
    slugs = " ".join(f.detail for f in broken)
    assert "missing-topic" in slugs
    assert "ghost" in slugs


def test_doctor_flags_dead_pid_active_entry(tmp_path: Path) -> None:
    root = _seed_min(tmp_path)
    # pid 999999 is vanishingly unlikely to be running.
    (root / ".memory-bank" / "activeContext.md").write_text(
        "- 2026-07-05T10:00:00Z | status:active | session:pid:999999 | bg deploy\n",
        encoding="utf-8",
    )
    findings = run_doctor(root)
    dead = [f for f in findings if f.check == "dead-pid"]
    assert dead, "expected a dead-PID finding for the bogus active entry"
    assert "pid=999999" in dead[0].detail


def test_doctor_flags_over_budget_core_file(tmp_path: Path) -> None:
    root = _seed_min(tmp_path)
    # MEMORY.md budget is 80 lines; write 100 to trip RED.
    (root / ".memory-bank" / "MEMORY.md").write_text(
        "# Memory\n" + "\n".join(f"- entry {i}" for i in range(100)), encoding="utf-8"
    )
    findings = run_doctor(root)
    budget = [f for f in findings if f.check == "budget"]
    assert budget, "expected an over-budget finding"
    assert any(f.severity == "error" for f in budget)


def test_finding_as_line_includes_hint() -> None:
    f = Finding(severity="warn", check="x", detail="d", hint="do something")
    line = f.as_line()
    assert "[WARN]" in line
    assert "do something" in line


def test_doctor_json_output_round_trips(tmp_path: Path, capsys) -> None:
    from agent_memory.features.doctor.command import doctor

    root = _seed_min(tmp_path)
    rc = doctor(root, json_out=True)
    out = capsys.readouterr().out
    data = json.loads(out)
    assert isinstance(data, list)
    assert all("severity" in d and "check" in d and "detail" in d for d in data)
    # healthy bank (no errors) → exit 0
    assert rc == 0
