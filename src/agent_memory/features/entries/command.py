"""Entry parsing, status taxonomy, and archive/injection guards.

Re-exports the shared implementations from :mod:`agent_memory.shared.entries`
for backward compatibility. The CLI-specific ``add_entry`` function remains here.
"""

from __future__ import annotations

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


def add_entry(root: Path, text: str, status: str | None = None) -> None:
    """Append a safe, deduped entry to the ``activeContext.md`` file."""
    from agent_memory.features.bank.command import add_entry as bank_add_entry

    bank_add_entry(root, "activeContext.md", text, status=status)
