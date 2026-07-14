"""Tests for the decision-tracker hook."""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

from agent_memory.hooks.decision_tracker import (
    TRANSCRIPT_TAIL_LINES,
    extract_decisions,
    last_assistant_text,
    main,
)


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


def test_last_assistant_text_is_bounded_to_tail(clean_env: Path) -> None:
    """A transcript longer than the tail window keeps only the LAST assistant
    message — the deque-based read must not load the whole file, and must drop
    an assistant block that fell out of the tail window."""

    def _line(role: str, text: str) -> str:
        return json.dumps({"message": {"role": role, "content": [{"type": "text", "text": text}]}})

    early = _line("assistant", "DECISION: early block evicted from the tail window")
    # Padding deeper than the tail window so ``early`` falls off the back.
    padding = [_line("user", f"padding line number {i}") for i in range(TRANSCRIPT_TAIL_LINES + 10)]
    late = _line("assistant", "DECISION: latest block kept in the bounded tail")

    transcript = clean_env / "big.jsonl"
    transcript.write_text("\n".join([early, *padding, late]) + "\n", encoding="utf-8")

    text = last_assistant_text(transcript)
    assert "latest block kept in the bounded tail" in text
    assert "evicted from the tail window" not in text
