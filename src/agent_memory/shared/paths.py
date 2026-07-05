"""Project-root, bank-dir, and file-name resolution shared by all features.

Honor ``--root`` explicitly: if a memory bank sits at the given path, return
it WITHOUT climbing to a parent git root (a nested project under a git-rooted
parent must resolve its OWN bank, not the parent's).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from agent_memory.shared.config import FILE_ALIASES, FILES, TOPICS_DIR


def project_root(start: Path | None = None) -> Path:
    """Resolve the project root: explicit bank here → here; else git toplevel;
    else the start dir. Never climbs past an existing bank at ``start``."""
    cwd = (start or Path.cwd()).resolve()
    if (cwd / ".memory-bank").is_dir():
        return cwd
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=1,
        )
    except (OSError, subprocess.TimeoutExpired):
        return cwd
    if result.returncode == 0 and result.stdout.strip():
        return Path(result.stdout.strip()).resolve()
    return cwd


def bank_dir(root: Path) -> Path:
    """Return the ``.memory-bank`` path for a project root."""
    return root / ".memory-bank"


def file_name(value: str) -> str:
    """Resolve a user-supplied target to a concrete ``*.md`` filename.

    Accepts a canonical name (``MEMORY.md``), an alias (``memory``), or a
    bare slug (``notes`` → ``notes.md``)."""
    key = value.strip()
    lower = key.lower().replace(".md", "")
    if key in FILES:
        return key
    if lower in FILE_ALIASES:
        return FILE_ALIASES[lower]
    if not key.endswith(".md"):
        key += ".md"
    return key


def iter_memory_files(memory: Path) -> list[Path]:
    """Core ``*.md`` files plus ``topics/*.md`` (excludes ``topics/archive/``)."""
    files = list(memory.glob("*.md"))
    topics = memory / TOPICS_DIR
    if topics.exists():
        files.extend(topics.glob("*.md"))
    return sorted(files)
