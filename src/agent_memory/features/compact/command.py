"""Compaction: shrink over-budget files and archive whole topics.

Over-budget core files have their middle archived to ``topics/archive/`` (never
deleted). Whole topics move there too, after a broken-reference check. The
status taxonomy from ``features/entries`` protects in-flight and live-reference
entries from ever being archived.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from agent_memory.features.entries.command import is_protected_from_archive
from agent_memory.shared.config import FILES, TOPIC_SOFT_LIMIT, TOPICS_DIR
from agent_memory.shared.paths import bank_dir, iter_memory_files


def archive_old_lines(path: Path, lines: list[str], max_lines: int) -> list[str]:
    """Archive excess lines to ``topics/archive/`` instead of deleting.

    Never archives protected entries (status active/wip/blocked/live, or
    completed work fresher than the active window). If the at-risk block is
    entirely protected, the file is left over budget rather than drop work."""
    if len(lines) <= max_lines:
        return lines
    header = lines[:1] if lines else [f"# {path.stem}"]
    tail_count = max(0, max_lines - len(header) - 1)
    middle = lines[1:-tail_count] if tail_count > 0 else lines[1:]
    if not middle:
        return lines

    protected = [ln for ln in middle if is_protected_from_archive(ln)]
    archivable = [ln for ln in middle if not is_protected_from_archive(ln)]
    if not archivable:
        print(
            f"  Protected {len(protected)} active/live entries in {path.name}; "
            "skipped archive (work in flight). Raise budget or wait for completion."
        )
        return lines

    _write_archive(path, archivable, len(protected))
    msg = f"  Archived {len(archivable)} lines to {path.stem}-{date.today().isoformat()}.md"
    if protected:
        msg += f"; protected {len(protected)} active/live entries inline"
    print(msg)

    tail = lines[-tail_count:] if tail_count > 0 else []
    if protected:
        return header + protected + tail
    return [*header, f"> Compacted on {date.today().isoformat()}", *tail]


def _write_archive(path: Path, archivable: list[str], protected_count: int) -> None:
    """Append the archivable block to today's archive file for ``path``."""
    memory = path.parent
    archive_dir = memory / TOPICS_DIR / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    archive_path = archive_dir / f"{path.stem}-{date.today().isoformat()}.md"
    note = f" ({protected_count} active/live entries kept inline)" if protected_count else ""
    content = f"# {path.stem} Archive\n> Archived on {date.today().isoformat()}{note}\n\n"
    content += "\n".join(archivable) + "\n"
    if archive_path.exists():
        archive_path.write_text(
            archive_path.read_text(encoding="utf-8") + content, encoding="utf-8"
        )
    else:
        archive_path.write_text(content, encoding="utf-8")


def compact_file(path: Path, max_lines: int) -> bool:
    """Enforce ``max_lines`` on ``path`` via :func:`archive_old_lines`."""
    if not path.exists():
        return False
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    if len(lines) <= max_lines:
        return False
    compacted = archive_old_lines(path, lines, max_lines)
    path.write_text("\n".join(compacted) + "\n", encoding="utf-8")
    return True


def _compact_topics(memory: Path) -> list[str]:
    """Compact every topic file over the soft limit; return the changed names."""
    changed: list[str] = []
    for path in sorted((memory / TOPICS_DIR).glob("*.md")):
        if compact_file(path, TOPIC_SOFT_LIMIT):
            changed.append(f"{TOPICS_DIR}/{path.name}")
    return changed


def compact_memory(root: Path, include_topics: bool = False) -> None:
    """Enforce line budgets on all core files (and optionally topics)."""
    memory = bank_dir(root)
    changed = [name for name, (_, limit) in FILES.items() if compact_file(memory / name, limit)]
    if include_topics:
        changed.extend(_compact_topics(memory))
    print("Compacted: " + (", ".join(changed) if changed else "none"))


def find_refs_to_slug(root: Path, slug: str) -> list[tuple[str, int, str]]:
    """Active ``.md`` files referencing a topic via ``(slug.md)`` or ``[[slug]]``.

    Excludes the topic itself, ``topics/_index.md`` (entry removed on archive),
    and anything under ``topics/archive/``."""
    memory = bank_dir(root)
    slug_md = f"{slug}.md"
    wiki = f"[[{slug}]]"
    refs: list[tuple[str, int, str]] = []
    for f in iter_memory_files(memory):
        if f.name == "_index.md":
            continue
        if f.name == slug_md and f.parent.name == TOPICS_DIR:
            continue
        try:
            content = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        refs.extend(_scan_line_refs(f, memory, content, slug_md, wiki))
    return refs


def _scan_line_refs(
    f: Path, memory: Path, content: str, slug_md: str, wiki: str
) -> list[tuple[str, int, str]]:
    """Collect ``(relpath, lineno, snippet)`` refs from one file's content."""
    out: list[tuple[str, int, str]] = []
    for i, line in enumerate(content.splitlines(), 1):
        if slug_md not in line and wiki not in line:
            continue
        out.append((str(f.relative_to(memory)), i, line.strip()[:100]))
    return out


def _remove_topic_from_index(index_path: Path, slug: str) -> bool:
    """Drop the ``(slug.md)`` entry from ``topics/_index.md``. True if changed."""
    if not index_path.exists():
        return False
    marker = f"({slug}.md)"
    lines = index_path.read_text(encoding="utf-8", errors="replace").splitlines()
    kept = [ln for ln in lines if marker not in ln]
    if len(kept) == len(lines):
        return False
    index_path.write_text("\n".join(kept) + "\n", encoding="utf-8")
    return True


def _print_dangling_refs(slug: str, refs: list[tuple[str, int, str]]) -> None:
    """Print a readable ABORT report for references that would break."""
    print(f"ABORT: {len(refs)} active reference(s) to '{slug}.md' would break:")
    for path, line, text in refs[:10]:
        print(f"  {path}:{line}: {text}")
    if len(refs) > 10:
        print(f"  ... {len(refs) - 10} more")
    print("Resolve them, or rerun with --force to archive anyway.")


def archive_topic(root: Path, slug: str, *, force: bool = False) -> None:
    """Move a topic to ``topics/archive/<slug>-YYYY-MM-DD.md`` and drop its
    ``_index.md`` entry. Refuses on dangling references unless ``force=True``."""
    memory = bank_dir(root)
    topics = memory / TOPICS_DIR
    src = topics / f"{slug}.md"
    if not src.exists():
        raise SystemExit(f"Topic not found: {src}")

    if not force:
        refs = find_refs_to_slug(root, slug)
        if refs:
            _print_dangling_refs(slug, refs)
            raise SystemExit(1)

    archive_dir = topics / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    dst = archive_dir / f"{slug}-{date.today().isoformat()}.md"
    body = src.read_text(encoding="utf-8", errors="replace")
    if dst.exists():
        dst.write_text(
            dst.read_text(encoding="utf-8", errors="replace") + "\n\n---\n\n" + body,
            encoding="utf-8",
        )
    else:
        dst.write_text(body, encoding="utf-8")
    src.unlink()
    removed = _remove_topic_from_index(topics / "_index.md", slug)
    print(f"Archived topic: {slug}.md -> topics/archive/{dst.name}")
    print(
        "Removed entry from topics/_index.md."
        if removed
        else "(no _index.md entry found to remove)."
    )
