"""Memory-bank structure: init, status, read, and entry/topic append.

The bank is the on-disk ``.memory-bank/`` directory at a project root. This
slice owns its shape (bootstrap, inspect, bounded read) and the append of new
entries/topics. Compaction lives in ``features/compact``.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from pathlib import Path

from agent_memory.features.bank.templates import TOPIC_INDEX_TEMPLATE, render_templates
from agent_memory.features.compact.command import compact_file
from agent_memory.features.entries.command import (
    filter_lines_for_injection,
    is_duplicate,
    now_iso,
    topic_path,
    validate_status,
)
from agent_memory.shared.config import FILES, READ_ORDER, TOPIC_SOFT_LIMIT, TOPICS_DIR
from agent_memory.shared.paths import bank_dir, file_name
from agent_memory.shared.text import ensure_safe_text, write_if_missing


def init_memory(root: Path) -> None:
    """Create the standard ``.memory-bank/`` files (and ``topics/``) if missing."""
    memory = bank_dir(root)
    memory.mkdir(parents=True, exist_ok=True)
    (memory / TOPICS_DIR).mkdir(parents=True, exist_ok=True)
    templates = render_templates(root.name)
    created = [name for name, body in templates.items() if write_if_missing(memory / name, body)]
    if write_if_missing(memory / TOPICS_DIR / "_index.md", TOPIC_INDEX_TEMPLATE):
        created.append(f"{TOPICS_DIR}/_index.md")
    print(f"Project root: {root}")
    print(f"Memory bank: {memory}")
    print("Created: " + (", ".join(created) if created else "none (already initialized)"))


def _core_flag(name: str, n: int) -> str:
    """Budget flag for a core file: ``ok`` or ``over limit N/M``; ``index`` for
    non-budgeted entries like ``topics/_index.md``."""
    if name not in FILES:
        return "index"
    limit = FILES[name][1]
    return "ok" if n <= limit else f"over limit {n}/{limit}"


def status_bank(root: Path) -> None:
    """Print bank location, per-file line counts vs budgets, and staleness."""
    memory = bank_dir(root)
    print(f"Project root: {root}")
    print(f"Memory bank: {memory}")
    if not memory.exists():
        print("Status: missing")
        return
    print("Status: present")
    for name in READ_ORDER:
        path = memory / name
        if not path.exists():
            print(f"- {name}: missing")
            continue
        n = len(path.read_text(encoding="utf-8", errors="replace").splitlines())
        print(f"- {name}: {n} lines ({_core_flag(name, n)})")
    topics = sorted((memory / TOPICS_DIR).glob("*.md"))
    topic_files = [p for p in topics if p.name != "_index.md"]
    print(f"- {TOPICS_DIR}/: {len(topic_files)} topic files")
    for path in topic_files[:10]:
        n = len(path.read_text(encoding="utf-8", errors="replace").splitlines())
        flag = "ok" if n <= TOPIC_SOFT_LIMIT else f"large {n}/{TOPIC_SOFT_LIMIT}; read selectively"
        print(f"  - {path.name}: {n} lines ({flag})")
    if len(topic_files) > 10:
        print(f"  ... {len(topic_files) - 10} more topics")
    _report_staleness(memory)


def _parse_line_date(line: str, date_re: re.Pattern[str]) -> date | None:
    """Return the first YYYY-MM-DD date in ``line``, or None if absent/invalid."""
    m = date_re.search(line)
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def _resolve_threshold_days(override: int | None) -> int:
    """Resolve the staleness threshold: explicit override, else env, else 14."""
    if override is not None:
        return override
    import os

    raw = os.environ.get("MEMORY_STALENESS_DAYS", "14")
    try:
        return int(raw)
    except ValueError:
        return 14


def _stale_lines(
    name: str, path: Path, date_re: re.Pattern[str], cutoff: date
) -> list[tuple[str, str, date]]:
    """Collect ``(name, snippet, date)`` for lines older than ``cutoff``."""
    out: list[tuple[str, str, date]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        d = _parse_line_date(line, date_re)
        if d is not None and d < cutoff:
            out.append((name, line.strip()[:90], d))
    return out


def _collect_stale(
    memory: Path, date_re: re.Pattern[str], cutoff: date
) -> list[tuple[str, str, date]]:
    """Scan the staleness-sensitive core files for stale date-prefixed entries."""
    stale: list[tuple[str, str, date]] = []
    for name in ("activeContext.md", "progress.md", "currentTask.md", "CONTEXT.md"):
        path = memory / name
        if not path.exists():
            continue
        stale.extend(_stale_lines(name, path, date_re, cutoff))
    return stale


def _report_staleness(memory: Path, threshold_days: int | None = None) -> None:
    """Flag date-prefixed entries in core files older than the threshold.

    Informational only — never edits. Helps catch stale handoffs."""
    threshold = _resolve_threshold_days(threshold_days)
    cutoff = date.today() - timedelta(days=threshold)
    date_re = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
    stale = _collect_stale(memory, date_re, cutoff)
    if not stale:
        return
    stale.sort(key=lambda x: x[2])
    print(
        f"- staleness: {len(stale)} entr{'y' if len(stale) == 1 else 'ies'} > {threshold}d"
        " (set MEMORY_STALENESS_DAYS to tune; archive to topics/)"
    )
    for name, snippet, d in stale[:8]:
        print(f"  - {d.isoformat()} {name}: {snippet}")
    if len(stale) > 8:
        print(f"  ... {len(stale) - 8} more stale entries")


def read_memory(
    root: Path,
    per_file_lines: int,
    total_lines: int,
    topic: str | None = None,
    topic_lines: int = 80,
) -> None:
    """Print bounded memory context (startup order), or one topic if given."""
    memory = bank_dir(root)
    if not memory.exists():
        return
    if topic:
        path = topic_path(root, topic)
        if not path.exists():
            raise SystemExit(f"Topic not found: {path}")
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        print(f"## Project Memory Topic: {path}")
        print("\n".join(lines[:topic_lines]))
        if len(lines) > topic_lines:
            print(f"... ({len(lines)} total lines)")
        return

    emitted = 0
    print(f"## Project Memory Bank: {memory}")
    order = list(dict.fromkeys(READ_ORDER[:3] + [f"{TOPICS_DIR}/_index.md"] + READ_ORDER[3:]))
    for name in order:
        path = memory / name
        if not path.exists():
            continue
        lines = filter_lines_for_injection(
            name, path.read_text(encoding="utf-8", errors="replace").splitlines()
        )
        if not lines:
            continue
        remaining = total_lines - emitted
        if remaining <= 0:
            break
        take = max(0, min(per_file_lines, remaining - 2))
        if take <= 0:
            break
        print(f"\n### {name}")
        print("\n".join(lines[:take]))
        emitted += take + 2
        if len(lines) > take:
            print(f"... ({len(lines)} total lines)")


def add_entry(
    root: Path,
    target: str,
    text: str,
    status: str | None = None,
    session: str | None = None,
) -> None:
    """Append a safe, deduped entry to a core file (or arbitrary ``*.md``)."""
    ensure_safe_text(text)
    validate_status(status)
    memory = bank_dir(root)
    memory.mkdir(parents=True, exist_ok=True)
    path = memory / file_name(target)
    if not path.exists():
        path.write_text(f"# {path.stem}\n", encoding="utf-8")
    if is_duplicate(path, text):
        print(f"Skipped duplicate in {path}")
        return
    seg = [f"- {now_iso()}"]
    if status:
        seg.append(f"status:{status}")
    if session:
        seg.append(f"session:{session}")
    entry = " | ".join(seg) + f" | {text.strip()}\n"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(entry)
    _, limit = FILES.get(path.name, ("Topic memory", 60))
    compact_file(path, limit)
    print(f"Updated: {path}")


def update_topic_index(memory: Path, slug: str, title: str) -> None:
    """Append a topic entry to ``topics/_index.md`` (no-op if already listed)."""
    topics = memory / TOPICS_DIR
    topics.mkdir(parents=True, exist_ok=True)
    index = topics / "_index.md"
    if not index.exists():
        index.write_text(
            "# Topic Index\n> Deep project memory. Search/read on demand.\n\n## Topics\n",
            encoding="utf-8",
        )
    content = index.read_text(encoding="utf-8", errors="replace")
    if f"({slug}.md)" not in content:
        with index.open("a", encoding="utf-8") as handle:
            handle.write(f"- [{title}]({slug}.md)\n")


def add_topic_entry(root: Path, topic: str, text: str, status: str | None = None) -> None:
    """Append a deep-context block to ``topics/<slug>.md`` and register it."""
    ensure_safe_text(text, max_chars=4000)
    validate_status(status)
    memory = bank_dir(root)
    (memory / TOPICS_DIR).mkdir(parents=True, exist_ok=True)
    path = topic_path(root, topic)
    if not path.exists():
        path.write_text(
            f"# {topic.strip()}\n> Deep memory topic. Read on demand; keep entries factual.\n\n",
            encoding="utf-8",
        )
    header = f"\n## {now_iso()}"
    if status:
        header += f" | status:{status}"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(header + "\n" + text.strip() + "\n")
    update_topic_index(memory, path.stem, topic.strip())
    print(f"Updated topic: {path}")
