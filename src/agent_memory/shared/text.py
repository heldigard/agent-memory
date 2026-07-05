"""Text-safety helpers: secret rejection, size caps, slugification."""

from __future__ import annotations

import re
from pathlib import Path

from agent_memory.shared.config import SECRET_RE


def ensure_safe_text(text: str, max_chars: int = 1200) -> None:
    """Refuse oversize or secret-shaped text. Raises ``SystemExit`` so a CLI
    call stops cleanly without writing a dangerous entry."""
    if len(text) > max_chars:
        raise SystemExit(f"Refusing to write memory entry over {max_chars} characters.")
    if SECRET_RE.search(text):
        raise SystemExit("Refusing to write likely secret/token material to memory.")


def write_if_missing(path: Path, content: str) -> bool:
    """Write ``content`` to ``path`` only if it does not exist. Returns True if
    a file was created."""
    if path.exists():
        return False
    path.write_text(content, encoding="utf-8")
    return True


def split_csv(value: str | None) -> list[str] | None:
    """Parse a comma-separated CLI arg into a clean list (or None)."""
    if not value:
        return None
    return [v.strip() for v in value.split(",") if v.strip()]


def line_count(path: Path) -> int:
    """Count lines in a file, returning 0 on any OS error."""
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            return sum(1 for _ in fh)
    except OSError:
        return 0


def slugify(value: str) -> str:
    """Turn a topic name into a filesystem-safe slug (≤80 chars)."""
    slug = re.sub(r"[^a-z0-9._-]+", "-", value.strip().lower())
    slug = re.sub(r"-{2,}", "-", slug).strip("-._")
    if not slug:
        raise SystemExit("Topic name produced an empty slug.")
    return slug[:80]
