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


def test_doctor_accepts_relative_root_ref_and_ignores_code_examples(tmp_path: Path) -> None:
    root = _seed_min(tmp_path)
    bank = root / ".memory-bank"
    (bank / "pattern.md").write_text("# Pattern\n", encoding="utf-8")
    (bank / "MEMORY.md").write_text(
        "[Pattern](pattern.md)\n`(placeholder.md)`\n[[slug]]\n```md\n[[fenced-example]]\n```\n",
        encoding="utf-8",
    )
    findings = run_doctor(root)
    assert not [finding for finding in findings if finding.check == "broken-ref"]


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


def test_doctor_warns_when_tags_up_but_embed_fails(tmp_path: Path, monkeypatch) -> None:
    """Partial Ollama installs: /api/tags OK, /api/embeddings broken."""
    import agent_memory.features.doctor.command as doctor_mod

    root = _seed_min(tmp_path)
    # Force the index path past "no index" so ollama health is evaluated.
    bank = root / ".memory-bank"
    idx = bank / ".index"
    idx.mkdir(exist_ok=True)
    import numpy as np

    np.savez(idx / "vectors.npz", vectors=np.zeros((1, 3), dtype=np.float32))
    (idx / "manifest.json").write_text(
        '[{"file":"MEMORY.md","mtime":0,"heading":"","start":1,"end":1,"sha256":"x","text":"hi"}]',
        encoding="utf-8",
    )
    monkeypatch.setattr(doctor_mod, "ollama_is_alive", lambda: True)
    monkeypatch.setattr(doctor_mod, "ollama_embed_ready", lambda: False)
    findings = run_doctor(root)
    embed = [f for f in findings if f.check == "ollama-embed"]
    assert embed, findings
    assert embed[0].severity == "warn"
    assert "embeddings" in embed[0].detail


def test_doctor_info_when_ollama_down(tmp_path: Path, monkeypatch) -> None:
    import agent_memory.features.doctor.command as doctor_mod

    root = _seed_min(tmp_path)
    bank = root / ".memory-bank"
    idx = bank / ".index"
    idx.mkdir(exist_ok=True)
    import numpy as np

    np.savez(idx / "vectors.npz", vectors=np.zeros((1, 3), dtype=np.float32))
    (idx / "manifest.json").write_text(
        '[{"file":"MEMORY.md","mtime":0,"heading":"","start":1,"end":1,"sha256":"x","text":"hi"}]',
        encoding="utf-8",
    )
    monkeypatch.setattr(doctor_mod, "ollama_is_alive", lambda: False)
    findings = run_doctor(root)
    assert any(f.check == "index" and "not reachable" in f.detail for f in findings)


def test_doctor_harness_integration_missing(tmp_path: Path, monkeypatch) -> None:
    # Point home to a temp dir so that the shims check doesn't find them and reports warnings.
    fake_home = tmp_path / "fake_home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    monkeypatch.setattr("shutil.which", lambda name: None)  # Make sure agent-memory is not on PATH

    root = _seed_min(tmp_path)
    findings = run_doctor(root)
    harness_shims = [f for f in findings if f.check == "harness-shim"]
    harness_path = [f for f in findings if f.check == "harness-path"]
    assert len(harness_shims) > 0
    assert len(harness_path) > 0


def test_doctor_mismatched_index_version_or_model(tmp_path: Path) -> None:
    root = _seed_min(tmp_path)
    # Seed a mismatched index.
    bank = root / ".memory-bank"
    idx = bank / ".index"
    idx.mkdir(exist_ok=True)
    (idx / "vectors.npz").write_bytes(b"")  # empty / mock
    (idx / "manifest.json").write_text("[]", encoding="utf-8")

    from agent_memory.shared.config import EMBED_MODEL_FILE, VERSION_FILE

    (idx / EMBED_MODEL_FILE).write_text("wrong-model", encoding="utf-8")
    (idx / VERSION_FILE).write_text("wrong-version", encoding="utf-8")

    findings = run_doctor(root)
    model_mismatch = [f for f in findings if f.check == "index-model"]
    version_mismatch = [f for f in findings if f.check == "index-version"]
    assert model_mismatch
    assert version_mismatch


def test_doctor_graph_clean_and_absent(tmp_path: Path) -> None:
    root = _seed_min(tmp_path)
    # no graph file at all → no graph findings
    assert not [f for f in run_doctor(root) if f.check == "graph"]
    # healthy graph → still no graph findings
    from agent_memory.features.graph.command import graph_add

    graph_add(root, "A", "DECIDED", "useX")
    graph_add(root, "B", "DEPENDS_ON", "A")
    assert not [f for f in run_doctor(root) if f.check == "graph"]


def test_doctor_graph_duplicate_ids_is_error(tmp_path: Path) -> None:
    root = _seed_min(tmp_path)
    graph = root / ".memory-bank" / "decisions.graph.jsonl"
    graph.write_text(
        '{"id": "g_001", "s": "A", "p": "DECIDED", "o": "x"}\n'
        '{"id": "g_001", "s": "B", "p": "DECIDED", "o": "y"}\n',
        encoding="utf-8",
    )
    findings = [f for f in run_doctor(root) if f.check == "graph"]
    assert any(f.severity == "error" and "duplicate" in f.detail for f in findings), findings


def test_doctor_graph_malformed_and_dangling_supersedes_warn(tmp_path: Path) -> None:
    root = _seed_min(tmp_path)
    graph = root / ".memory-bank" / "decisions.graph.jsonl"
    graph.write_text(
        '{"id": "g_001", "s": "A", "p": "DECIDED", "o": "x", "supersedes": ["g_999"]}\n'
        "not-json-at-all\n",
        encoding="utf-8",
    )
    findings = [f for f in run_doctor(root) if f.check == "graph"]
    assert any("malformed" in f.detail for f in findings), findings
    assert any("g_999" in f.detail for f in findings), findings
    assert all(f.severity == "warn" for f in findings), findings


def test_doctor_graph_reports_schema_and_normalized_metadata(tmp_path: Path) -> None:
    root = _seed_min(tmp_path)
    graph = root / ".memory-bank" / "decisions.graph.jsonl"
    graph.write_text(
        '{"id":"g_001","s":"A","p":"OWNS","o":"B","aliases":"bad"}\n'
        '{"id":"g_002","s":"A","p":"OWNS"}\n'
        "null\n",
        encoding="utf-8",
    )

    findings = [finding for finding in run_doctor(root) if finding.check == "graph"]

    assert any("2 invalid-schema line(s) skipped" in finding.detail for finding in findings)
    assert any("1 invalid metadata field(s) normalized" in finding.detail for finding in findings)


def test_doctor_flags_index_staging_leftovers(tmp_path: Path) -> None:
    """An interrupted atomic save strands `.vectors.tmp.npz`-style files in
    `.index/`; doctor must surface them as a low-severity disk-hygiene nudge."""
    root = _seed_min(tmp_path)
    idx = root / ".memory-bank" / ".index"
    idx.mkdir(parents=True)
    (idx / ".vectors.tmp.npz").write_bytes(b"")
    (idx / ".manifest.tmp.json").write_text("[]", encoding="utf-8")

    findings = [f for f in run_doctor(root) if f.check == "index-tmp"]

    assert len(findings) == 1
    assert findings[0].severity == "warn"
    assert "2 staging file(s)" in findings[0].detail
    assert ".vectors.tmp.npz" in findings[0].detail


def test_doctor_clean_index_dir_has_no_tmp_finding(tmp_path: Path) -> None:
    """A real index file (no ``.tmp`` staging residue) must not trip the check."""
    root = _seed_min(tmp_path)
    idx = root / ".memory-bank" / ".index"
    idx.mkdir(parents=True)
    (idx / "vectors.npz").write_bytes(b"")

    assert not [f for f in run_doctor(root) if f.check == "index-tmp"]


def test_doctor_flags_overlong_line_in_injection_window(tmp_path: Path) -> None:
    """A >500-char line inside the injectable prefix loses its tail on SessionStart."""
    root = _seed_min(tmp_path)
    bank = root / ".memory-bank"
    (bank / "CONTEXT.md").write_text("# Context\n- " + "x" * 600 + "\n", encoding="utf-8")

    findings = [f for f in run_doctor(root) if f.check == "injection-window"]

    assert len(findings) == 1
    assert findings[0].severity == "warn"
    assert "CONTEXT.md:2" in findings[0].detail
    assert "602 chars > 500" in findings[0].detail


def test_doctor_ignores_overlong_line_past_injection_window(tmp_path: Path) -> None:
    """Historical log lines beyond the injectable prefix are never injected — exempt."""
    root = _seed_min(tmp_path)
    bank = root / ".memory-bank"
    filler = "".join(f"- entry {i}\n" for i in range(15))
    (bank / "progress.md").write_text("# Progress\n" + filler + "- " + "y" * 700 + "\n")

    assert not [f for f in run_doctor(root) if f.check == "injection-window"]


def test_doctor_injection_window_healthy_bank_clean(tmp_path: Path) -> None:
    root = _seed_min(tmp_path)
    assert not [f for f in run_doctor(root) if f.check == "injection-window"]
