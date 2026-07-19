"""Tests for the decision-tracker hook."""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

from agent_memory.hooks import decision_tracker as dt
from agent_memory.hooks.decision_tracker import (
    MAX_CAPTURES_PER_TURN,
    MAX_DECISION_LEN,
    MAX_PATTERNS_LINES,
    TRANSCRIPT_TAIL_LINES,
    _append_decisions,
    _assistant_text_blocks,
    _ensure_trailing_newline,
    _keyword_overlap,
    _line_count,
    _project_root,
    _read_existing_lines,
    _scan_verb_decisions,
    _trim,
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


# --- helpers + main guard branches (coverage) ---


def test_keyword_overlap_edges() -> None:
    assert _keyword_overlap("same words here clearly", "same words here clearly") == 1.0
    assert _keyword_overlap("alpha beta gamma delta", "completely different words entirely") == 0.0
    # candidate with no 4+ char words -> 0.0 (guards divide-by-zero)
    assert _keyword_overlap("anything here", "a b c") == 0.0


def test_trim_sentence_boundary_max_and_too_short() -> None:
    # sentence boundary at a period is preferred when beyond MIN length
    assert _trim("We will use Postgres. Then more stuff.") == "We will use Postgres"
    # over MAX_DECISION_LEN truncates with ellipsis
    long_decision = "x" * (MAX_DECISION_LEN + 50)
    trimmed = _trim(long_decision)
    assert trimmed is not None and trimmed.endswith("...") and len(trimmed) == MAX_DECISION_LEN
    # below MIN length -> None
    assert _trim("short") is None
    # trailing punctuation stripped
    assert _trim("alpha beta gamma delta epsilon zeta.").endswith("epsilon zeta")


def test_scan_verb_decisions_caps_at_max() -> None:
    # Newline-separated: the `.{15,280}` capture stops at the line end, so each
    # verb yields a distinct capture; the 4th trips the MAX_CAPTURES_PER_TURN guard.
    text = "\n".join(
        [
            "We decided to write comprehensive unit tests for the parser module",
            "We decided to adopt vector embeddings for semantic search",
            "We decided to refactor the whole index build pipeline",
            "We decided to ship another decision that must not be captured",
        ]
    )
    found = _scan_verb_decisions(text)
    assert len(found) == MAX_CAPTURES_PER_TURN


def test_assistant_text_blocks_filters_non_text() -> None:
    blocks: list[object] = [
        {"type": "text", "text": "keep me"},
        "not a dict",
        {"type": "tool_use", "text": "drop"},
        {"type": "text", "text": ""},  # empty -> dropped
        {"type": "text"},  # missing text attr -> dropped
        {"type": "text", "text": 123},  # non-str -> dropped
    ]
    assert _assistant_text_blocks(blocks) == ["keep me"]


def test_last_assistant_text_handles_missing_and_nonassistant(tmp_path) -> None:
    missing = tmp_path / "nope.jsonl"
    assert last_assistant_text(missing) == ""
    # garbage + non-dict + non-assistant + content-not-list + empty text -> ""
    transcript = tmp_path / "mixed.jsonl"
    transcript.write_text(
        "not-json\n"
        + "42\n"
        + json.dumps({"message": {"role": "user", "content": [{"type": "text", "text": "u"}]}})
        + "\n"
        + json.dumps({"message": {"role": "assistant", "content": "not-a-list"}})
        + "\n"
        + json.dumps({"message": {"role": "assistant", "content": [{"type": "text", "text": ""}]}})
        + "\n",
        encoding="utf-8",
    )
    assert last_assistant_text(transcript) == ""


def test_last_assistant_text_skips_blank_lines(tmp_path) -> None:
    transcript = tmp_path / "blanks.jsonl"
    # trailing blank lines after the assistant block: reversed iteration hits
    # them first and must `continue` past them to reach the assistant text.
    transcript.write_text(
        json.dumps(
            {"message": {"role": "assistant", "content": [{"type": "text", "text": "kept"}]}}
        )
        + "\n\n   \n",
        encoding="utf-8",
    )
    assert last_assistant_text(transcript) == "kept"


def test_line_count_and_read_existing_handle_missing(tmp_path) -> None:
    assert _line_count(tmp_path / "absent.md") == 0
    assert _read_existing_lines(tmp_path / "absent.md") == []


def test_ensure_trailing_newline(tmp_path) -> None:
    p = tmp_path / "f.md"
    # missing file -> no-op, no crash
    _ensure_trailing_newline(p)
    # empty file -> no-op
    p.write_text("", encoding="utf-8")
    _ensure_trailing_newline(p)
    assert p.read_bytes() == b""
    # file without trailing newline -> one appended
    p.write_text("a", encoding="utf-8")
    _ensure_trailing_newline(p)
    assert p.read_bytes() == b"a\n"
    # file already ending in newline -> unchanged
    p.write_text("a\n", encoding="utf-8")
    _ensure_trailing_newline(p)
    assert p.read_bytes() == b"a\n"


def test_append_decisions_skips_near_duplicate(tmp_path) -> None:
    p = tmp_path / "patterns.md"
    existing = ["- [2026-07-18] write unit tests for the parser module"]
    p.write_text(existing[0] + "\n", encoding="utf-8")
    # near-duplicate (>0.70 overlap) is skipped; novel entry is appended
    appended = _append_decisions(
        p,
        ["write unit tests for the parser module", "completely novel unrelated decision here"],
        "2026-07-18",
        existing,
    )
    assert appended == 1
    assert "completely novel unrelated decision here" in p.read_text(encoding="utf-8")


def test_project_root_env_then_git_then_cwd(monkeypatch, tmp_path) -> None:
    # explicit env wins
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    assert _project_root() == tmp_path

    # no env: git toplevel succeeds
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)

    class _Ok:
        returncode = 0
        stdout = str(tmp_path) + "\n"

    monkeypatch.setattr(dt.subprocess, "run", lambda *a, **k: _Ok())
    assert _project_root() == tmp_path

    # no env: git fails (non-zero) -> cwd fallback
    class _Fail:
        returncode = 1
        stdout = ""

    monkeypatch.setattr(dt.subprocess, "run", lambda *a, **k: _Fail())
    monkeypatch.setattr(dt.os, "getcwd", lambda: str(tmp_path / "cwd"))
    assert _project_root() == tmp_path / "cwd"

    # no env: git times out -> cwd fallback (OSError/TimeoutExpired swallowed)
    def _timeout(*a, **k):
        raise dt.subprocess.TimeoutExpired(cmd="git", timeout=2)

    monkeypatch.setattr(dt.subprocess, "run", _timeout)
    assert _project_root() == tmp_path / "cwd"


def test_main_guards_short_circuit_before_stdin(clean_env, monkeypatch) -> None:
    # DISABLE flag
    monkeypatch.setenv("DECISION_TRACKER_DISABLE", "1")
    monkeypatch.setattr(sys, "stdin", io.StringIO("not even read"))
    assert main() == 0

    # WORKER env var present
    monkeypatch.delenv("DECISION_TRACKER_DISABLE", raising=False)
    monkeypatch.setenv("CODEX_WORKER", "1")
    assert main() == 0


def test_main_invalid_payload_and_flags(clean_env, monkeypatch) -> None:
    monkeypatch.delenv("CODEX_WORKER", raising=False)
    # unparseable stdin
    monkeypatch.setattr(sys, "stdin", io.StringIO("{not json"))
    assert main() == 0
    # valid json but not a dict
    monkeypatch.setattr(sys, "stdin", io.StringIO("[1, 2, 3]"))
    assert main() == 0
    # stop_hook_active continuation
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"stop_hook_active": True})))
    assert main() == 0
    # dict but no transcript_path
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({})))
    assert main() == 0


def test_main_skips_when_no_quick_gate_or_no_decisions(clean_env, monkeypatch) -> None:
    bank = clean_env / ".memory-bank"
    bank.mkdir()
    transcript = clean_env / "t.jsonl"
    transcript.write_text(
        json.dumps(
            {"message": {"role": "assistant", "content": [{"type": "text", "text": "hello world"}]}}
        )
        + "\n",
        encoding="utf-8",
    )
    # passes QUICK_GATE but yields no decision -> no write
    gate_transcript = clean_env / "g.jsonl"
    gate_transcript.write_text(
        json.dumps(
            {
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "a decision was mentioned"}],
                }
            }
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"transcript_path": str(transcript)})))
    assert main() == 0
    assert not (bank / "systemPatterns.md").exists()

    monkeypatch.setattr(
        sys, "stdin", io.StringIO(json.dumps({"transcript_path": str(gate_transcript)}))
    )
    assert main() == 0
    assert not (bank / "systemPatterns.md").exists()


def test_main_skips_when_patterns_over_budget(clean_env, monkeypatch) -> None:
    bank = clean_env / ".memory-bank"
    bank.mkdir()
    patterns = bank / "systemPatterns.md"
    patterns.write_text("# Patterns\n" + "line\n" * (MAX_PATTERNS_LINES + 5), encoding="utf-8")
    transcript = clean_env / "t.jsonl"
    transcript.write_text(
        json.dumps(
            {
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": "DECISION: adopt the new indexing strategy today."}
                    ],
                }
            }
        )
        + "\n",
        encoding="utf-8",
    )
    before = patterns.read_text(encoding="utf-8")
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"transcript_path": str(transcript)})))
    assert main() == 0
    # over budget -> file untouched
    assert patterns.read_text(encoding="utf-8") == before


def test_main_survives_append_oserror(clean_env, monkeypatch) -> None:
    bank = clean_env / ".memory-bank"
    bank.mkdir()
    # create systemPatterns.md as a directory so the open() append raises OSError
    (bank / "systemPatterns.md").mkdir()
    transcript = clean_env / "t.jsonl"
    transcript.write_text(
        json.dumps(
            {
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": "DECISION: adopt the new indexing strategy today."}
                    ],
                }
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"transcript_path": str(transcript)})))
    # OSError is swallowed; hook returns 0 (never hard-fails on a memory write)
    assert main() == 0
