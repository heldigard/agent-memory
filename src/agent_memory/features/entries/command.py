"""Entry parsing, status taxonomy, and archive/injection guards.

Re-exports the shared implementations from :mod:`agent_memory.shared.entries`
for backward compatibility. The CLI-specific ``add_entry`` function remains here.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# Re-export all shared entry helpers for backward compatibility.
from agent_memory.shared.entries import (  # noqa: F401
    archive_window_hours,
    filter_lines_for_injection,
    injection_window_hours,
    is_duplicate,
    is_protected_from_archive,
    is_stale_for_injection,
    now_iso,
    parse_entry,
    strip_entry_prefix,
    topic_path,
    validate_status,
)
from agent_memory.shared.paths import bank_dir, iter_memory_files


def add_entry(root: Path, text: str, status: str | None = None) -> None:
    """Append a safe, deduped entry to the ``activeContext.md`` file."""
    from agent_memory.features.bank.command import add_entry as bank_add_entry

    bank_add_entry(root, "activeContext.md", text, status=status)


def _with_superseded_status(line: str) -> str:
    """Return one structured entry line with ``status:superseded``."""
    if re.search(r"\|\s*status:[A-Za-z]+\b", line):
        return re.sub(r"\|\s*status:[A-Za-z]+\b", "| status:superseded", line, count=1)
    return re.sub(
        r"^(\s*-\s*\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}:\d{2}Z)?)(?=\s*(?:\||:))",
        r"\1 | status:superseded",
        line,
        count=1,
    )


def supersede_entry(root: Path, query: str, file_name: str | None = None) -> int:
    """Mark one uniquely matching structured entry as superseded.

    Refuses zero or multiple matches so maintenance cannot silently invalidate
    unrelated durable facts. ``file_name`` optionally narrows the search.
    """
    normalized_query = query.strip().lower()
    if not normalized_query:
        print("error: supersede query is empty", file=sys.stderr)
        return 2
    memory = bank_dir(root)
    matches: list[tuple[Path, int, list[str]]] = []
    for path in iter_memory_files(memory):
        if file_name and path.name != Path(file_name).name:
            continue
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        for index, line in enumerate(lines):
            info = parse_entry(line)
            if (
                info.get("ts")
                and info.get("status") != "superseded"
                and normalized_query in line.lower()
            ):
                matches.append((path, index, lines))
    if len(matches) != 1:
        print(
            f"error: supersede query matched {len(matches)} entries; expected exactly 1",
            file=sys.stderr,
        )
        return 2
    path, index, lines = matches[0]
    lines[index] = _with_superseded_status(lines[index])
    path.write_text("".join(lines), encoding="utf-8")
    print(f"Superseded: {path.relative_to(memory)}:{index + 1}")
    return 0
