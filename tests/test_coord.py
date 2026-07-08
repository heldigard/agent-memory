"""coord bridge — ``agent-coordination-status`` subprocess wrapper.

The sibling project may or may not be installed. We verify both paths:
missing binary → friendly stderr + exit 1; present binary → dispatched with
``--project <root>`` and exit code propagated. A timeout is enforced so a
hung registry can never wedge the SessionStart hook.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import agent_memory.features.coord.command as coord_mod


def test_coord_status_missing_binary(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(coord_mod.shutil, "which", lambda name: None)
    rc = coord_mod.coord_status(tmp_path)
    err = capsys.readouterr().err
    assert rc == 1
    assert "not installed" in err


def test_coord_status_dispatches_with_project(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(coord_mod.shutil, "which", lambda name: "/fake/bin/acs")
    seen: dict[str, object] = {}

    class _FakeResult:
        returncode = 0

    def fake_run(cmd: list[str], **kwargs: object) -> object:
        seen["cmd"] = list(cmd)
        seen["timeout"] = kwargs.get("timeout")
        return _FakeResult()

    monkeypatch.setattr(coord_mod.subprocess, "run", fake_run)
    assert coord_mod.coord_status(tmp_path) == 0
    cmd = list(seen["cmd"])  # type: ignore[arg-type]
    assert cmd[0] == "/fake/bin/acs"
    assert "--project" in cmd
    assert str(tmp_path) in cmd
    assert seen["timeout"] == 30


def test_coord_cleanup_passes_cleanup_flag(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(coord_mod.shutil, "which", lambda name: "/fake/bin/acs")
    monkeypatch.setattr(coord_mod, "ORCH_SCRIPT", tmp_path / "missing-orch.py")
    seen: list[list[str]] = []

    class _FakeResult:
        returncode = 0

    def fake_run(cmd: list[str], **_kwargs: object) -> object:
        seen.append(list(cmd))
        return _FakeResult()

    monkeypatch.setattr(coord_mod.subprocess, "run", fake_run)
    assert coord_mod.coord_cleanup(tmp_path) == 0
    assert "--cleanup" in seen[0]


def test_coord_cleanup_runs_broker_cleanup_when_available(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(coord_mod.shutil, "which", lambda name: "/fake/bin/acs")
    orch = tmp_path / "cli-orchestration.py"
    orch.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
    monkeypatch.setattr(coord_mod, "ORCH_SCRIPT", orch)
    seen: list[list[str]] = []

    class _FakeResult:
        returncode = 0
        stderr = ""

    def fake_run(cmd: list[str], **_kwargs: object) -> object:
        seen.append(list(cmd))
        return _FakeResult()

    monkeypatch.setattr(coord_mod.subprocess, "run", fake_run)
    assert coord_mod.coord_cleanup(tmp_path) == 0
    assert seen[0][0] == "/fake/bin/acs"
    assert seen[1][1] == str(orch)
    assert seen[1][2] == "cleanup"
    assert "--project" in seen[1]


def test_coord_timeout_returns_1(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(coord_mod.shutil, "which", lambda name: "/fake/bin/acs")

    def fake_run(cmd: list[str], **kwargs: object) -> object:
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=int(kwargs.get("timeout", 30)))  # type: ignore[arg-type]

    monkeypatch.setattr(coord_mod.subprocess, "run", fake_run)
    assert coord_mod.coord_status(tmp_path) == 1
    assert "timed out" in capsys.readouterr().err
