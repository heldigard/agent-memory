"""Tests for the decision-tracker hook."""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

from agent_memory.hooks.decision_tracker import extract_decisions, main


@pytest.fixture
def clean_env(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    # Hermeticity: WORKER_ENV_VARS / DECISION_TRACKER_DISABLE leak from the host
    # proxy shell into the pytest subprocess and would short-circuit main()
    # before it reads stdin, making the append tests flake. Clear them so the
    # hook runs the full path under test.
    for var in (
        "DECISION_TRACKER_DISABLE",
        "CODEX_WORKER",
        "NO_DELEGATE",
        "CLAUDE_CODE_SUBAGENT_MODEL",
    ):
        monkeypatch.delenv(var, raising=False)
    return tmp_path


def test_extract_decisions() -> None:
    # Explicit marker
    res = extract_decisions("Some text\nDECISION: We will use Postgres.")
    assert res == ["We will use Postgres"]

    # Decision verb
    res = extract_decisions("We decided to write unit tests for hooks.")
    assert res == ["write unit tests for hooks"]


def test_extract_decisions_uses_canonical_secret_redaction() -> None:
    key = "sk-" + "abc123def456ghi789jkl012mno"

    res = extract_decisions(f"DECISION: Use Authorization: Bearer {key} for staged migration.")

    assert res == ["Use [REDACTED] for staged migration"]
    assert key not in res[0]


def test_decision_tracker_no_bank(clean_env: Path, monkeypatch) -> None:
    transcript = clean_env / "transcript.jsonl"
    transcript.write_text(
        json.dumps(
            {
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "text",
                            "text": ("DECISION: Adopt AST parsing for all project source files."),
                        }
                    ],
                }
            }
        )
        + "\n",
        encoding="utf-8",
    )

    fake_stdin = io.StringIO(json.dumps({"transcript_path": str(transcript)}))
    monkeypatch.setattr(sys, "stdin", fake_stdin)
    assert main() == 0
    assert not (clean_env / ".memory-bank" / "systemPatterns.md").exists()


def test_decision_tracker_appends(clean_env: Path, monkeypatch) -> None:
    bank = clean_env / ".memory-bank"
    bank.mkdir()
    patterns = bank / "systemPatterns.md"
    patterns.write_text("# Patterns\n", encoding="utf-8")

    transcript = clean_env / "transcript.jsonl"
    transcript.write_text(
        json.dumps(
            {
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "text",
                            "text": ("DECISION: Adopt AST parsing for all project source files."),
                        }
                    ],
                }
            }
        )
        + "\n",
        encoding="utf-8",
    )

    fake_stdin = io.StringIO(json.dumps({"transcript_path": str(transcript)}))
    monkeypatch.setattr(sys, "stdin", fake_stdin)
    assert main() == 0

    content = patterns.read_text(encoding="utf-8")
    assert "Adopt AST parsing for all project source files" in content
