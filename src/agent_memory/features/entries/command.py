"""Entry parsing, status taxonomy, and archive/injection guards.

An entry is a memory-bank line of the form::

    - 2026-06-28T14:32:15Z | status:active | session:bt1ba8ulh | <text>

Legacy formats (date-only, no status) parse too. The status taxonomy protects
in-flight and live-reference work from archival (the "prompt improver"
incident: an agent compacted memory mid-deploy and lost completed work).
"""

from __future__ import annotations

import os
import re
from datetime import UTC, datetime
from pathlib import Path

from agent_memory.shared.config import (
    DEFAULT_ARCHIVE_WINDOW_HOURS,
    DEFAULT_INJECTION_WINDOW_HOURS,
    NEVER_ARCHIVED,
    TOPICS_DIR,
    VALID_STATUS,
)
from agent_memory.shared.paths import bank_dir
from agent_memory.shared.text import slugify

_ENTRY_TS_RE = re.compile(r"^\s*-\s*(\d{4}-\d{2}-\d{2})(?:T(\d{2}:\d{2}:\d{2})Z)?")
_STATUS_SEG_RE = re.compile(r"\|\s*status:([A-Za-z]+)\b")
_SESSION_SEG_RE = re.compile(r"\|\s*session:([A-Za-z0-9_:-]+)\b")


def now_iso() -> str:
    """ISO-8601 UTC timestamp with seconds (``2026-06-28T14:32:15Z``)."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_entry(line: str) -> dict[str, str | None]:
    """Parse one entry line into ``{ts, status, session, text}``.

    ``ts`` is a full ISO string (date-only legacy entries get ``T00:00:00Z``
    appended) or None for non-entry lines. ``text`` has the timestamp and any
    status/session metadata stripped, so two entries with the same text but
    different status compare equal (for dedup)."""
    m = _ENTRY_TS_RE.match(line)
    if not m:
        return {"ts": None, "status": None, "session": None, "text": line.strip()}
    date_part, time_part = m.group(1), m.group(2) or "00:00:00"
    ts = f"{date_part}T{time_part}Z"
    sm = _STATUS_SEG_RE.search(line)
    sem = _SESSION_SEG_RE.search(line)
    text = line
    text = re.sub(r"^\s*-\s*\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}:\d{2}Z)?\s*:?\s*", "", text)
    text = re.sub(r"\s*\|\s*status:[A-Za-z]+\b", "", text)
    text = re.sub(r"\s*\|\s*session:[A-Za-z0-9_:-]+\b", "", text)
    text = re.sub(r"^\s*\|\s*", "", text)
    return {
        "ts": ts,
        "status": sm.group(1).lower() if sm else None,
        "session": sem.group(1) if sem else None,
        "text": text.strip(),
    }


def strip_entry_prefix(line: str) -> str:
    """Return just the human text of an entry line (any format)."""
    return parse_entry(line)["text"] or ""


def validate_status(status: str | None) -> str | None:
    """Return the status lowercased if valid, else raise SystemExit."""
    if not status:
        return None
    if status not in VALID_STATUS:
        raise SystemExit(f"Invalid status '{status}'. Valid: {sorted(VALID_STATUS)}")
    return status


def _entry_age_hours(ts: str | None) -> float | None:
    """Hours between an ISO ts and now. None if missing/unparseable."""
    if not ts:
        return None
    try:
        dt = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError:
        return None
    return (datetime.now(UTC) - dt).total_seconds() / 3600.0


def archive_window_hours() -> float:
    """Completed entries younger than this (hours) are protected from archive.
    Tuned for a background deploy that may run for hours."""
    raw = os.environ.get("MEMORY_ACTIVE_WINDOW_HOURS")
    if raw is None:
        return DEFAULT_ARCHIVE_WINDOW_HOURS
    try:
        return max(0.0, float(raw))
    except ValueError:
        return DEFAULT_ARCHIVE_WINDOW_HOURS


def injection_window_hours() -> float:
    """How long active/wip entries stay eligible for prompt injection.

    Archival preserves active work indefinitely; prompt injection is stricter
    (a stale active handoff should not steer new sessions forever)."""
    raw = os.environ.get("MEMORY_INJECTION_ACTIVE_WINDOW_HOURS")
    if raw is None:
        return DEFAULT_INJECTION_WINDOW_HOURS
    try:
        return max(0.0, float(raw))
    except ValueError:
        return DEFAULT_INJECTION_WINDOW_HOURS


def _session_pid(session: str | None) -> int | None:
    match = re.match(r"pid:(\d+)$", session or "")
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def _pid_is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def is_stale_for_injection(line: str) -> bool:
    """True when a line should be hidden from prompt/context injection.

    Does NOT change archive behavior: old/dead active entries are preserved in
    files but kept out of startup context where they could misdirect."""
    info = parse_entry(line)
    if info["status"] not in {"active", "wip"}:
        return False
    pid = _session_pid(info["session"])
    if pid is not None and not _pid_is_alive(pid):
        return True
    age = _entry_age_hours(info["ts"])
    return age is not None and age > injection_window_hours()


def filter_lines_for_injection(_name: str, lines: list[str]) -> list[str]:
    """Drop stale active/wip entries from memory reads without mutating files.

    The ``_name`` arg is accepted for call-site symmetry with file-aware
    helpers but is not currently used to decide filtering."""
    if os.environ.get("MEMORY_FILTER_STALE_ACTIVE", "1") == "0":
        return lines
    return [line for line in lines if not is_stale_for_injection(line)]


def is_protected_from_archive(line: str) -> bool:
    """True if a line must NOT be moved to ``topics/archive/`` during compaction.

    Protected = work in flight or live reference (business rules, runbooks,
    gotchas, active features, in-progress deploys). Only completed work past
    the freshness window, or undated legacy entries, may be archived."""
    info = parse_entry(line)
    status = info["status"]
    if status in NEVER_ARCHIVED:
        return True
    if status == "completed":
        age = _entry_age_hours(info["ts"])
        # Recent completed = protected; old completed = archivable; unknown age = protect.
        return age is None or age < archive_window_hours()
    return False


def is_duplicate(path: Path, text: str) -> bool:
    """True if ``text`` (without its timestamp/status prefix) already exists in
    ``path``."""
    if not path.exists():
        return False
    clean = text.strip()
    if not clean:
        return False
    content = path.read_text(encoding="utf-8", errors="replace")
    return any(strip_entry_prefix(line) == clean for line in content.splitlines())


def topic_path(root: Path, name: str) -> Path:
    """Resolve a topic name to its ``topics/<slug>.md`` path under the bank."""
    return bank_dir(root) / TOPICS_DIR / f"{slugify(name)}.md"
