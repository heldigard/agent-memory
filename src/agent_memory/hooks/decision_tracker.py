"""Decision tracker — Stop hook.

Captures architectural/technical decisions articulated in the assistant's
final message of a turn and persists them to
<project-root>/.memory-bank/systemPatterns.md.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# --- Constants -----------------------------------------------------------

MARKER_RE: re.Pattern[str] = re.compile(
    r"(?:^|\n)\s*(?:#{1,4}\s*)?(?:DECISION|DECISI(?:O|Ó)N)\s*:\s*(.+)",
    re.IGNORECASE,
)

DECISION_VERB_RES: list[re.Pattern[str]] = [
    re.compile(r"(?:decided to|going with|settled on|opting for)\s+(.{15,280})", re.I),
    re.compile(r"chose\s+.+?\s+over\s+.{5,200}", re.I),
    re.compile(
        r"(?:decid(?:o|í|imos)\s+(?:usar|ir por|implementar|adoptar)|"
        r"optamos por|elegimos)\s+(.{15,280})",
        re.I,
    ),
    re.compile(r"the approach (?:is|will be)\s+(.{15,280})", re.I),
]

SECRET_RE: re.Pattern[str] = re.compile(
    r"(?:api[_-]?key|password|passwd|secret|token|bearer|authorization"
    r"|private[_-]?key|client[_-]?secret|access[_-]?key)\s*[:=]\s*\S+",
    re.IGNORECASE,
)

QUICK_GATE: re.Pattern[str] = re.compile(
    r"decision|decisi|decided|going with|settled on|opting for|chose .+ over"
    r"|optamos por|elegimos|decid(?:o|í|imos)|the approach is",
    re.IGNORECASE,
)

WORKER_ENV_VARS: tuple[str, ...] = (
    "CODEX_WORKER",
    "NO_DELEGATE",
    "CLAUDE_CODE_SUBAGENT_MODEL",
)

MAX_PATTERNS_LINES: int = 500  # systemPatterns.md budget
MAX_DECISION_LEN: int = 300
MIN_DECISION_LEN: int = 20
MAX_CAPTURES_PER_TURN: int = 3
SIMILARITY_THRESHOLD: float = 0.70
TRANSCRIPT_TAIL_LINES: int = 400  # bounded read of the session JSONL

OUTPUT: str = '{"continue": true}'


# --- Helpers -------------------------------------------------------------


def _project_root() -> Path:
    """Detect project root: prefer CLAUDE_PROJECT_DIR, then git, fallback cwd."""
    env_dir = os.environ.get("CLAUDE_PROJECT_DIR")
    if env_dir:
        return Path(env_dir)
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if result.returncode == 0 and result.stdout.strip():
            return Path(result.stdout.strip())
    except (OSError, subprocess.TimeoutExpired):
        pass
    return Path(os.getcwd())


def _keyword_overlap(existing: str, candidate: str) -> float:
    """Cheap set-overlap dedup between two entries."""

    def words(s: str) -> set[str]:
        return {w.lower() for w in re.findall(r"\w{4,}", s)}

    ew, cw = words(existing), words(candidate)
    if not cw:
        return 0.0
    return len(ew & cw) / len(cw)


def _scrub_secrets(text: str) -> str:
    return SECRET_RE.sub("[REDACTED]", text)


def _trim(decision: str) -> str | None:
    """Trim to a sentence boundary within [MIN, MAX] length."""
    d = decision.strip().rstrip(".;,")
    sent_end = re.search(r"[.!?]\s", d)
    if sent_end and sent_end.start() >= MIN_DECISION_LEN:
        d = d[: sent_end.start()]
    if len(d) > MAX_DECISION_LEN:
        d = d[: MAX_DECISION_LEN - 3].rstrip() + "..."
    d = d.strip()
    if len(d) >= MIN_DECISION_LEN:
        return d
    return None


def extract_decisions(text: str) -> list[str]:
    """Return deduped decision strings: marker matches first, then verbs."""
    found: list[str] = []

    # 1. explicit markers (high confidence)
    for m in MARKER_RE.finditer(text):
        chunk = m.group(1).split("\n", 1)[0]
        cleaned = _trim(_scrub_secrets(chunk))
        if cleaned and cleaned not in found:
            found.append(cleaned)

    # 2. decision verbs (only if no explicit marker was used)
    if not found:
        for pat in DECISION_VERB_RES:
            for m in pat.finditer(text):
                grp = m.group(1) if m.groups() else m.group(0)
                cleaned = _trim(_scrub_secrets(grp))
                if cleaned and cleaned not in found:
                    found.append(cleaned)
                if len(found) >= MAX_CAPTURES_PER_TURN:
                    break
            if len(found) >= MAX_CAPTURES_PER_TURN:
                break

    return found[:MAX_CAPTURES_PER_TURN]


def last_assistant_text(transcript_path: Path) -> str:
    """Read the last assistant text block from the session JSONL (bounded)."""
    try:
        with transcript_path.open(encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()[-TRANSCRIPT_TAIL_LINES:]
    except OSError:
        return ""
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not isinstance(entry, dict):
            continue
        msg = entry.get("message")
        if not isinstance(msg, dict) or msg.get("role") != "assistant":
            continue
        parts: list[str] = []
        content_list = msg.get("content", [])
        if not isinstance(content_list, list):
            continue
        for block in content_list:
            if isinstance(block, dict) and block.get("type") == "text":
                t = block.get("text", "")
                if isinstance(t, str) and t:
                    parts.append(t)
        text = "\n".join(parts).strip()
        if text:
            return text
    return ""


def _line_count(path: Path) -> int:
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            return sum(1 for _ in fh)
    except OSError:
        return 0


def _is_duplicate(patterns_path: Path, entry: str) -> bool:
    try:
        content = patterns_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return any(
        _keyword_overlap(line, entry) > SIMILARITY_THRESHOLD
        for line in content.splitlines()
        if line.startswith("- ")
    )


# --- Main ----------------------------------------------------------------


def main() -> int:
    print(OUTPUT)

    # Guards
    if os.environ.get("DECISION_TRACKER_DISABLE") == "1":
        return 0
    if any(os.environ.get(v) for v in WORKER_ENV_VARS):
        return 0

    try:
        payload = json.load(sys.stdin)
    except (ValueError, OSError):
        return 0
    if not isinstance(payload, dict):
        return 0
    # Avoid recursion during a Stop continuation
    if payload.get("stop_hook_active"):
        return 0

    transcript = payload.get("transcript_path")
    if not transcript:
        return 0

    text = last_assistant_text(Path(transcript))
    if not text or not QUICK_GATE.search(text):
        return 0

    decisions = extract_decisions(text)
    if not decisions:
        return 0

    # Per-project memory bank
    root = _project_root()
    bank = root / ".memory-bank"
    if not bank.is_dir():
        return 0
    patterns_path = bank / "systemPatterns.md"
    if patterns_path.exists() and _line_count(patterns_path) >= MAX_PATTERNS_LINES:
        return 0

    today = datetime.now().strftime("%Y-%m-%d")
    appended = 0
    try:
        if patterns_path.exists() and patterns_path.stat().st_size > 0:
            with patterns_path.open("r+b") as f:
                f.seek(-1, 2)
                if f.read(1) != b"\n":
                    f.write(b"\n")
        with patterns_path.open("a", encoding="utf-8") as f:
            for d in decisions:
                entry = f"- [{today}] {d}"
                if _is_duplicate(patterns_path, entry):
                    continue
                f.write(entry + "\n")
                appended += 1
    except OSError:
        pass
    if appended:
        sys.stderr.write(
            f"[decision-tracker] captured {appended} decision(s) → {patterns_path.name}\n"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
