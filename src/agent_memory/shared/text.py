"""Text-safety helpers: secret rejection, size caps, slugification."""

from __future__ import annotations

import contextlib
import os
import re
import tempfile
from pathlib import Path

from agent_memory.shared.config import SECRET_RE


def redact_secrets(text: str) -> str:
    """Replace credential-shaped material while retaining surrounding context."""
    return SECRET_RE.sub("[REDACTED]", text)


def ensure_safe_text(text: str, max_chars: int = 1200) -> None:
    """Refuse oversize or secret-shaped text. Raises ``SystemExit`` so a CLI
    call stops cleanly without writing a dangerous entry."""
    if len(text) > max_chars:
        raise SystemExit(f"Refusing to write memory entry over {max_chars} characters.")
    if SECRET_RE.search(text):
        raise SystemExit("Refusing to write likely secret/token material to memory.")


def atomic_write_text(path: Path, content: str) -> None:
    """Write ``content`` to ``path`` atomically.

    Writes a unique temp file in the SAME directory as ``path``, then
    ``os.replace``s it onto the target. A crash at any point leaves either
    the old file or the new file — never a truncated/partial write. This is
    the corruption fix for the shared markdown banks: a concurrent reader
    (or a crash mid-write) cannot observe a half-written file.

    Same-directory tmp is required: ``os.replace`` is atomic only within one
    filesystem. The tmp name is unique per call (``tempfile.mkstemp``), so
    two concurrent atomic writes to the same target do not clobber each
    other's staging file.

    Note on the residual compact-vs-append logical race: this helper removes
    the dangerous failure (truncation/corruption) but not the lost-update
    window where a ``compact`` (read→rewrite) overlaps an ``add`` (append).
    Append-mode writes are POSIX-atomic and compaction is infrequent, so the
    accepted residual is at most one occasionally-eaten small entry — never
    corruption. A full flock protocol across every writer is the heavier
    follow-up if that ever bites in practice; the semantic index (the
    high-frequency parallel path) already uses flock + atomic save."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        os.replace(tmp, path)
    except BaseException:
        # Any failure (including KeyboardInterrupt): the target was not yet
        # replaced, so it keeps its prior content. Remove the staging tmp so a
        # crashed write leaves no litter. Errors during cleanup are swallowed.
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise


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
