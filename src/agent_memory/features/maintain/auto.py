"""Lightweight memory-bank maintenance for SessionStart (auto-maintain).

Three cheap checks (runs in the background on session start):
  1. Semantic index freshness → incremental re-index if stale (Ollama required)
  2. Staleness detector → warns about entries older than ``STALENESS_DAYS``
  3. Budget check → warns when core files exceed their line budgets

Absorbed from the ecosystem ``memory-auto-maintain.py`` standalone script.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from agent_memory.shared.config import (
    FILES,
    INDEX_DIRNAME,
    MANIFEST_FILE,
    STALENESS_DAYS,
    TOPIC_INDEX_LIMIT,
    TOPIC_SOFT_LIMIT,
    TOPICS_DIR,
)
from agent_memory.shared.entries import parse_entry
from agent_memory.shared.ollama import is_alive as ollama_is_alive
from agent_memory.shared.paths import bank_dir, iter_memory_files
from agent_memory.shared.text import line_count

_OPERATIONAL_STATE_FILES = (
    "currentTask.md",
    "activeContext.md",
    "agent-sessions.md",
)
_STALE_OPERATIONAL_STATUSES = frozenset({"active", "wip", "blocked"})


def _parse_entry_timestamp(value: str | None) -> datetime | None:
    """Parse a structured entry timestamp, returning ``None`` when invalid."""
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError:
        return None


def _file_has_stale_entry(path: Path, cutoff: datetime) -> bool:
    """True only for an old unresolved structured entry in ``path``.

    Session-start maintenance is an operational signal, not a historical audit:
    stable references, completed work, archives, and legacy date-only prose
    remain searchable but must not make every session look stale.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    for line in text.splitlines():
        entry = parse_entry(line)
        if entry["status"] not in _STALE_OPERATIONAL_STATUSES:
            continue
        timestamp = _parse_entry_timestamp(entry["ts"])
        if timestamp is None or timestamp < cutoff:
            return True
    return False


def check_staleness(root: Path) -> list[str]:
    """Mutable state files with old unresolved operational entries.

    Deliberately skips reference/topic/archive files: their dates describe
    durable history, not work that needs attention at SessionStart.
    """
    mb = bank_dir(root)
    if not mb.is_dir():
        return []
    cutoff = datetime.now(UTC) - timedelta(days=STALENESS_DAYS)
    stale: list[str] = []
    for name in _OPERATIONAL_STATE_FILES:
        fpath = mb / name
        if not fpath.is_file():
            continue
        if _file_has_stale_entry(fpath, cutoff):
            stale.append(str(fpath.relative_to(root)))
    return stale


def _over_budget(path: Path, limit: int) -> dict[str, int] | None:
    """Return ``{lines, budget}`` if ``path`` is over ``limit``, else None."""
    if not path.is_file():
        return None
    count = line_count(path)
    return {"lines": count, "budget": limit} if count > limit else None


def check_budgets(root: Path) -> list[dict[str, object]]:
    """Over-budget core + topic files as ``{file, lines, budget}``."""
    mb = bank_dir(root)
    if not mb.is_dir():
        return []
    over: list[dict[str, object]] = []
    for fname, (_, budget) in FILES.items():
        item = _over_budget(mb / fname, budget)
        if item:
            over.append({"file": fname, **item})
    _collect_topic_overruns(mb, over)
    return over


def _collect_topic_overruns(mb: Path, over: list[dict[str, object]]) -> None:
    """Append over-budget topic files (archives exempt) to ``over``."""
    topics = mb / TOPICS_DIR
    if not topics.is_dir():
        return
    for tp in topics.glob("*.md"):
        if tp.name.startswith("archive-"):
            continue
        limit = TOPIC_INDEX_LIMIT if tp.name == "_index.md" else TOPIC_SOFT_LIMIT
        item = _over_budget(tp, limit)
        if item:
            over.append({"file": f"{TOPICS_DIR}/{tp.name}", **item})


def _index_is_stale(manifest: Path, mb: Path) -> bool:
    """True if any memory ``.md`` is newer than the index manifest."""
    if not manifest.exists():
        return False
    manifest_mtime = manifest.stat().st_mtime
    return any(f.stat().st_mtime > manifest_mtime for f in iter_memory_files(mb))


def _refresh_index(root: Path, errors: list[str]) -> bool:
    """Lazy-import the semantic builder and run an incremental re-index."""
    try:
        from agent_memory.features.semantic.index import build_index
    except Exception as exc:  # pragma: no cover — semantic slice missing at runtime
        errors.append(f"index refresh import failed: {exc}")
        return False
    try:
        stats = build_index(root, rebuild=False)
        if "error" in stats:
            errors.append(stats["error"])
            return False
        return True
    except Exception as exc:  # pragma: no cover
        errors.append(f"index refresh failed: {exc}")
        return False


def check_index_freshness(root: Path, errors: list[str]) -> bool:
    """Refresh the semantic index if it is stale and Ollama is reachable."""
    mb = bank_dir(root)
    manifest = mb / INDEX_DIRNAME / MANIFEST_FILE
    if not _index_is_stale(manifest, mb):
        return False
    if not ollama_is_alive(timeout=2.0):
        errors.append("index stale but Ollama unreachable")
        return False
    return _refresh_index(root, errors)


def run_auto_maintain(root: Path, *, check_only: bool = False) -> dict[str, object]:
    """Run the three SessionStart checks; return a serializable summary dict."""
    mb = bank_dir(root)
    if not mb.is_dir():
        return {
            "index_refreshed": False,
            "stale_files": [],
            "over_budget": [],
            "errors": ["no .memory-bank"],
        }

    errors: list[str] = []
    index_refreshed = False if check_only else check_index_freshness(root, errors)
    stale_files = check_staleness(root)
    if stale_files:
        print(f"⚠ Stale entries (>{STALENESS_DAYS}d): {', '.join(stale_files)}", file=sys.stderr)
    over_budget = check_budgets(root)
    for item in over_budget:
        print(
            f"⚠ Over budget: {item['file']} ({item['lines']}/{item['budget']} lines)",
            file=sys.stderr,
        )
    return {
        "index_refreshed": index_refreshed,
        "stale_files": stale_files,
        "over_budget": over_budget,
        "errors": errors,
    }
