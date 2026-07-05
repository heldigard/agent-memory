"""Markdown chunking for semantic indexing.

Split a ``.md`` file into chunks under ``MAX_CHUNK_CHARS`` while tracking the
active heading and exact line range for citation. Packs consecutive small
paragraphs so each chunk carries enough context.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

from agent_memory.shared.config import MAX_CHUNK_CHARS

DEFAULT_MAX_CHARS = MAX_CHUNK_CHARS


@dataclass
class ChunkCtx:
    """Mutable chunker state shared across the per-line / per-block helpers."""

    max_chars: int = DEFAULT_MAX_CHARS
    chunks: list[dict] = field(default_factory=list)
    heading: str = ""


def make_chunk(heading: str, start: int, end: int, text: str) -> dict:
    """Build one chunk dict with a short content hash for dedup."""
    return {
        "heading": heading,
        "start": start,
        "end": end,
        "text": text,
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()[:16],
    }


def chunk_file(path: Path, max_chars: int = DEFAULT_MAX_CHARS) -> list[dict]:
    """Split ``path`` into chunks under ``max_chars`` (heading + line-range aware)."""
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    ctx = ChunkCtx(max_chars=max_chars)
    _chunk_lines(ctx, lines)
    return ctx.chunks


def _chunk_lines(ctx: ChunkCtx, lines: list[str]) -> None:
    """Walk ``lines`` (1-based) and populate ``ctx.chunks``."""
    buf: list[tuple[int, int, list[str]]] = []
    for i, line in enumerate(lines, 1):
        if line.lstrip().startswith("#"):
            _flush(ctx, buf)
            ctx.heading = line.strip()
            buf = [(i, i, [line])]
        elif line.strip() == "":
            buf = _maybe_flush_on_blank(ctx, buf)
        elif buf:
            _append_to_last(buf, line, i)
        else:
            buf = [(i, i, [line])]
    _flush(ctx, buf)


def _maybe_flush_on_blank(
    ctx: ChunkCtx, buf: list[tuple[int, int, list[str]]]
) -> list[tuple[int, int, list[str]]]:
    """Flush on a paragraph break only when the buffer is already half-full."""
    if buf and _buf_len(buf) >= ctx.max_chars // 2:
        _flush(ctx, buf)
        return []
    return buf


def _flush(ctx: ChunkCtx, buf: list[tuple[int, int, list[str]]]) -> None:
    """Pack the buffered paragraphs into one (or more) chunk(s)."""
    if not buf:
        return
    all_lines = [t for _, _, group in buf for t in group]
    start, end = buf[0][0], buf[-1][1]
    block = "\n".join(all_lines)
    if len(block) <= ctx.max_chars:
        ctx.chunks.append(make_chunk(ctx.heading, start, end, block))
        return
    _pack_oversized(ctx, all_lines, start, end)


def _pack_oversized(ctx: ChunkCtx, all_lines: list[str], start: int, end: int) -> None:
    """Greedy line packing for a block larger than ``ctx.max_chars``."""
    cur: list[str] = []
    cur_start = start
    for ln, line in zip(range(start, end + 1), all_lines, strict=False):
        if len(line) >= ctx.max_chars:
            cur = _flush_cur(ctx, cur, cur_start, ln - 1)
            _split_long_line(line, ctx.heading, ln, ctx.max_chars, ctx.chunks)
            cur_start = ln + 1
            continue
        cur, cur_start = _consolidate(ctx, cur, cur_start, line, ln)
    if cur:
        ctx.chunks.append(make_chunk(ctx.heading, cur_start, end, "\n".join(cur)))


def _consolidate(
    ctx: ChunkCtx, cur: list[str], cur_start: int, line: str, ln: int
) -> tuple[list[str], int]:
    """Either extend ``cur`` or flush it and start a new one with ``line``."""
    candidate = [*cur, line]
    if len("\n".join(candidate)) >= ctx.max_chars and cur:
        ctx.chunks.append(make_chunk(ctx.heading, cur_start, ln - 1, "\n".join(cur)))
        return [line], ln
    return candidate, cur_start


def _flush_cur(ctx: ChunkCtx, cur: list[str], cur_start: int, end: int) -> list[str]:
    """Append the packed ``cur`` as a chunk if non-empty; return empty list."""
    if cur:
        ctx.chunks.append(make_chunk(ctx.heading, cur_start, end, "\n".join(cur)))
    return []


def _buf_len(buf: list[tuple[int, int, list[str]]]) -> int:
    return sum(len(t) + 1 for _, _, group in buf for t in group)


def _append_to_last(buf: list[tuple[int, int, list[str]]], line: str, i: int) -> None:
    start, _, group = buf[-1]
    group.append(line)  # mutate in place (avoids O(n^2) list copy per line)
    buf[-1] = (start, i, group)


def _split_long_line(line: str, heading: str, ln: int, max_chars: int, chunks: list[dict]) -> None:
    """Char-split a single line longer than ``max_chars``."""
    off = 0
    while off < len(line):
        chunks.append(make_chunk(heading, ln, ln, line[off : off + max_chars]))
        off += max_chars
