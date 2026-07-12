"""Tests for the recuerda-auto-append hook."""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

from agent_memory.hooks.recuerda_auto_append import main


@pytest.fixture
def clean_env(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    return tmp_path


def test_recuerda_hook_no_bank(clean_env: Path, monkeypatch) -> None:
    # No .memory-bank, hook should do nothing
    fake_stdin = io.StringIO(json.dumps({"prompt": "recuerda hacer tests"}))
    monkeypatch.setattr(sys, "stdin", fake_stdin)
    assert main() == 0
    assert not (clean_env / ".memory-bank" / "activeContext.md").exists()


def test_recuerda_hook_ignores_non_trigger(clean_env: Path, monkeypatch) -> None:
    bank = clean_env / ".memory-bank"
    bank.mkdir()
    active = bank / "activeContext.md"
    active.write_text("# Active\n", encoding="utf-8")

    fake_stdin = io.StringIO(json.dumps({"prompt": "hola mundo"}))
    monkeypatch.setattr(sys, "stdin", fake_stdin)
    assert main() == 0
    assert active.read_text(encoding="utf-8") == "# Active\n"


def test_recuerda_hook_appends_note(clean_env: Path, monkeypatch) -> None:
    bank = clean_env / ".memory-bank"
    bank.mkdir()
    active = bank / "activeContext.md"
    active.write_text("# Active\n", encoding="utf-8")

    fake_stdin = io.StringIO(json.dumps({"prompt": "recuerda verificar el compilador"}))
    monkeypatch.setattr(sys, "stdin", fake_stdin)
    assert main() == 0

    content = active.read_text(encoding="utf-8")
    assert "verificar el compilador" in content
    assert "recuerda" not in content  # trigger stripped


def test_recuerda_hook_no_double_append_when_no_mem_append(clean_env: Path, monkeypatch) -> None:
    bank = clean_env / ".memory-bank"
    bank.mkdir()
    active = bank / "activeContext.md"
    active.write_text("# Active\n", encoding="utf-8")

    fake_stdin = io.StringIO(json.dumps({"prompt": "recuerda verificar [NO_MEM_APPEND]"}))
    monkeypatch.setattr(sys, "stdin", fake_stdin)
    assert main() == 0
    assert active.read_text(encoding="utf-8") == "# Active\n"


def test_recuerda_hook_redacts_secrets_before_persisting(clean_env: Path, monkeypatch) -> None:
    bank = clean_env / ".memory-bank"
    bank.mkdir()
    active = bank / "activeContext.md"
    active.write_text("# Active\n", encoding="utf-8")
    key = "sk-" + "abc123def456ghi789jkl012mno"
    prompt = f"recuerda usar staging con access_token={key} para la prueba"
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"prompt": prompt})))

    assert main() == 0

    content = active.read_text(encoding="utf-8")
    assert "usar staging con [REDACTED] para la prueba" in content
    assert key not in content


def test_recuerda_hook_skips_note_that_is_only_a_secret(clean_env: Path, monkeypatch) -> None:
    bank = clean_env / ".memory-bank"
    bank.mkdir()
    active = bank / "activeContext.md"
    active.write_text("# Active\n", encoding="utf-8")
    key_name = "to" + "ken"
    prompt = f"recuerda {key_name}=synthetic-value"
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"prompt": prompt})))

    assert main() == 0
    assert active.read_text(encoding="utf-8") == "# Active\n"
