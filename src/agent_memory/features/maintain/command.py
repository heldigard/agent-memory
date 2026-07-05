"""LLM-assisted maintenance and session handoff.

The local Ollama model PROPOSES (duplicates, stale candidates, compaction
summaries); the big model DECIDES. ``maintain`` mutates nothing by default;
``--apply-safe`` only does additive compaction (archive middle + summary).
Destructive edits stay manual.
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from agent_memory.shared.config import (
    FILES,
    MAINT_AUDIT_CHAR_BUDGET,
    MAINT_AUDIT_LINE_CAP,
    MAINT_MODEL_DEFAULT,
    TOPIC_SOFT_LIMIT,
    TOPICS_DIR,
)
from agent_memory.shared.ollama import generate as ollama_generate
from agent_memory.shared.ollama import is_alive as ollama_is_alive
from agent_memory.shared.paths import bank_dir


@dataclass
class CoreFile:
    """One core memory-bank file with its description and line budget."""

    name: str
    desc: str
    limit: int
    path: Path


@dataclass
class MaintCtx:
    """Mutable maintenance context shared across the per-file processing loop."""

    apply_safe: bool
    no_llm: bool
    ollama_up: bool
    report: list[str] = field(default_factory=list)
    applied: list[str] = field(default_factory=list)


def _maint_model() -> str:
    return os.environ.get("CODEQ_SUMMARY_MODEL", MAINT_MODEL_DEFAULT)


def _maint_disabled(no_llm: bool) -> bool:
    return (
        no_llm
        or os.environ.get("CODEQ_NO_LLM") == "1"
        or os.environ.get("PROJECT_MEMORY_NO_LLM") == "1"
    )


def _ollama_call(prompt: str, *, no_llm: bool) -> str | None:
    """Best-effort local Ollama call. Returns text or None."""
    if _maint_disabled(no_llm):
        return None
    return ollama_generate(prompt, model=_maint_model(), temperature=0.2, num_ctx=8192)


def _summarize_block(block: str, filename: str, *, no_llm: bool) -> str | None:
    """1-3 line factual summary of a block being archived."""
    prompt = (
        "You are summarizing a slice of a project's memory-bank file so a senior"
        " LLM can decide later whether to read the full archive.\n\n"
        "Write 1-3 short bullet lines capturing the DURABLE facts only (decisions,"
        " root causes, IDs, dates). Drop chatter, repeats, and anything momentary."
        " Be factual — do not invent. If a bullet is uncertain, omit it.\n\n"
        f"FILE: {filename}\n\nBLOCK:\n{block[:MAINT_AUDIT_CHAR_BUDGET]}\n\nDurable-fact bullets:"
    )
    raw = _ollama_call(prompt, no_llm=no_llm)
    return raw.strip().strip('"').strip("'") if raw else None


def _audit_file(filename: str, content: str, *, no_llm: bool) -> str | None:
    """Semantic audit: propose duplicate + stale candidates as markdown."""
    truncated = (
        "\n[NOTE: file truncated for audit — findings apply to the visible part only.]"
        if len(content) > MAINT_AUDIT_CHAR_BUDGET
        else ""
    )
    prompt = (
        "You are auditing a project memory-bank file for a senior LLM that will"
        " DECIDE what to do. Your job is to PROPOSE, never to decide.\n\n"
        "Find:\n  1. DUPLICATES — entries that say the same thing in different words.\n"
        "  2. STALE — entries referencing removed tools/files/symbols or reverted decisions.\n\n"
        "Output rules (STRICT):\n  - Markdown only. No code fences.\n"
        "  - No duplicates -> write exactly: 'Duplicates: none.'\n"
        "  - No stale -> write exactly: 'Stale: none.'\n"
        "  - Each proposal MUST say 'PROPOSE' and give a one-line reason.\n"
        "  - Max 6 proposals per category. When unsure, omit.\n\n"
        f"FILE: {filename}\n\nCONTENT:\n{content[:MAINT_AUDIT_CHAR_BUDGET]}{truncated}\n\nAudit:"
    )
    raw = _ollama_call(prompt, no_llm=no_llm)
    return raw.strip().strip('"').strip("'") if raw else None


def _archive_with_summary(path: Path, max_lines: int, *, no_llm: bool) -> bool:
    """Additive compaction: archive the middle block with a 1-3 line summary."""
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    if len(lines) <= max_lines:
        return False
    header = lines[:1] if lines else [f"# {path.stem}"]
    tail_count = max(0, max_lines - len(header) - 2)
    middle = lines[1:-tail_count] if tail_count > 0 else lines[1:]
    if not middle:
        return False
    summary = (
        _summarize_block("\n".join(middle), path.name, no_llm=no_llm) or "(summary unavailable)"
    )
    _write_summary_archive(path, middle, summary)
    note = (
        f"> Compacted {date.today().isoformat()}: middle archived with LLM summary"
        f" → topics/archive/{path.stem}-{date.today().isoformat()}.md"
    )
    compacted = header + [note] + lines[-tail_count:]
    path.write_text("\n".join(compacted) + "\n", encoding="utf-8")
    print(f"  Compacted-with-summary {path.name}: archived {len(middle)} lines")
    return True


def _write_summary_archive(path: Path, middle: list[str], summary: str) -> None:
    """Prepend a summary header to today's archive file, then the full block."""
    archive_dir = path.parent / TOPICS_DIR / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    archive_path = archive_dir / f"{path.stem}-{date.today().isoformat()}.md"
    block = (
        f"# {path.stem} Archive (compacted {date.today().isoformat()})\n"
        f"> Source: {path.name} | Lines archived: {len(middle)}\n"
        f"> ollama-summary ({_maint_model()}); VERIFY before reasoning:\n\n"
        f"{summary}\n\n--- full archived block below ---\n\n" + "\n".join(middle) + "\n"
    )
    if archive_path.exists():
        archive_path.write_text(archive_path.read_text(encoding="utf-8") + block, encoding="utf-8")
    else:
        archive_path.write_text(block, encoding="utf-8")


def _emit_audit(ctx: MaintCtx, name: str, lines: list[str]) -> None:
    """Append the Ollama audit block (or skip-notes) for one file."""
    if not ctx.ollama_up:
        return
    if len(lines) > MAINT_AUDIT_LINE_CAP:
        ctx.report.append(
            f"> (semantic audit skipped — file {MAINT_AUDIT_LINE_CAP}+ lines;"
            " the 4-12B model can't see it whole. Read it directly if needed.)"
        )
        ctx.report.append("")
        return
    audit = _audit_file(name, "\n".join(lines), no_llm=ctx.no_llm)
    if audit:
        ctx.report.extend(f"> {line}" for line in audit.splitlines())
    else:
        ctx.report.append("> (audit returned empty)")
    ctx.report.append("")


def _process_file(ctx: MaintCtx, cf: CoreFile) -> None:
    """Audit one core file and append its section to the report."""
    if not cf.path.exists():
        return
    lines = cf.path.read_text(encoding="utf-8", errors="replace").splitlines()
    pct = round(100 * len(lines) / cf.limit) if cf.limit else 0
    flag = " ⚠️ OVER 80% BUDGET" if pct >= 80 else ""
    ctx.report.append(f"## {cf.name} — {len(lines)}/{cf.limit} lines ({pct}%){flag}")
    ctx.report.append(f"*{cf.desc}*")
    ctx.report.append("")
    if ctx.apply_safe and pct >= 80 and _archive_with_summary(cf.path, cf.limit, no_llm=ctx.no_llm):
        ctx.applied.append(cf.name)
    _emit_audit(ctx, cf.name, lines)


def _report_topics(ctx: MaintCtx, memory: Path) -> None:
    """Append a section for topic files over 80% of the soft limit."""
    topics_dir = memory / TOPICS_DIR
    if not topics_dir.exists():
        return
    big = []
    for tp in sorted(topics_dir.glob("*.md")):
        n = len(tp.read_text(encoding="utf-8", errors="replace").splitlines())
        if n > TOPIC_SOFT_LIMIT * 0.8:
            big.append(f"{tp.name} ({n}/{TOPIC_SOFT_LIMIT})")
    if big:
        ctx.report.append(f"## topics/ over 80% budget ({TOPIC_SOFT_LIMIT} lines)")
        ctx.report.extend(f"- {b}" for b in big)
        ctx.report.append("")


def maintain(
    root: Path, *, apply_safe: bool = False, output: str | None = None, no_llm: bool = False
) -> None:
    """LLM-assisted audit (propose-only); ``--apply-safe`` adds additive compaction."""
    memory = bank_dir(root)
    today = date.today().isoformat()
    disabled = _maint_disabled(no_llm)
    header = (
        "> Deterministic budget audit — PROPOSE-ONLY."
        if disabled
        else f"> Generated by local `{_maint_model()}` — PROPOSE-ONLY."
    )
    ollama_up = ollama_is_alive(timeout=10.0) and not disabled
    ctx = MaintCtx(apply_safe=apply_safe, no_llm=no_llm, ollama_up=ollama_up)
    ctx.report = [
        f"# Memory Bank Audit — {root.name} ({today})",
        header,
        "> Verify each proposal before acting. Destructive edits stay manual.",
        "",
    ]
    if not ollama_up:
        reason = "Ollama skipped (--no-llm/CODEQ_NO_LLM)" if disabled else "Ollama unavailable"
        ctx.report.extend([f"> ⚠️ {reason} — only deterministic budget checks below.", ""])
    for name, (desc, limit) in FILES.items():
        _process_file(ctx, CoreFile(name, desc, limit, memory / name))
    _report_topics(ctx, memory)
    if ctx.applied:
        ctx.report.append("## Applied (--apply-safe)")
        ctx.report.append("Additive compaction-with-summary ran on: " + ", ".join(ctx.applied))
        ctx.report.append("")
    body = "\n".join(ctx.report) + "\n"
    if output:
        Path(output).write_text(body, encoding="utf-8")
        print(f"Audit report written to {output}", file=sys.stderr)
    print(body)


def handoff(root: Path) -> None:
    """Generate a session handoff summary for ``activeContext.md``."""
    memory = bank_dir(root)
    today = date.today().isoformat()
    parts = [f"## Session Handoff - {today}"]
    _extend_handoff(parts, memory / "currentTask.md", "\n### Active Task", _active_task_lines, 5)
    _extend_handoff(parts, memory / "progress.md", "\n### Recent Progress", _recent_progress, 5)
    _extend_handoff(
        parts, memory / "activeContext.md", "\n### Previous Context", _previous_context, 3
    )
    parts.append("\n### Next Steps")
    parts.append("- [ ] TODO: Fill in next steps before ending session")
    parts.append("\n### Blockers")
    parts.append("- None / describe any blockers")
    print("\n".join(parts))
    print(f"\n# Copy the section above into {memory / 'activeContext.md'}")


def _extend_handoff(parts: list[str], path: Path, heading: str, extractor, limit: int) -> None:
    """Append ``heading`` + up to ``limit`` extracted lines from ``path`` if present."""
    if not path.exists():
        return
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    selected = extractor(lines, limit)
    if selected:
        parts.append(heading)
        parts.extend(selected)


def _active_task_lines(lines: list[str], limit: int) -> list[str]:
    """Lines describing the active task (date-bounded, not historical)."""
    out: list[str] = []
    for line in lines:
        if _is_active_task_line(line):
            out.append(line)
        if len(out) >= limit:
            break
    return out


def _recent_progress(lines: list[str], limit: int) -> list[str]:
    """Last ``limit`` date-prefixed progress bullets."""
    return [line for line in lines if line.strip().startswith("- 20")][-limit:]


def _previous_context(lines: list[str], limit: int) -> list[str]:
    """Last ``limit`` session/date markers from activeContext."""
    markers = [
        line
        for line in lines
        if line.strip().startswith("- 20") or line.strip().startswith("## Session")
    ]
    return markers[-limit:]


_HISTORICAL_RE = re.compile(
    r"\b(history|hist[oó]rico|complete|completed|done|shipped|merged|closed|finished|"
    r"archive|archived|old|viejo|terminad[oa]|completad[oa]|finalizad[oa])\b",
    re.IGNORECASE,
)
_ACTIVE_RE = re.compile(r"\b(active|wip|live|in[- ]?progress|activo|en curso)\b", re.IGNORECASE)
_TASK_DATE_RE = re.compile(r"\b(20\d{2}-\d{2}-\d{2})\b")


def _is_active_task_line(line: str, max_age_days: int = 14) -> bool:
    """Heuristic: a line describing current (not historical) task work."""
    clean = line.strip().lstrip("-* ").strip()
    if not clean or clean.startswith(("#", ">", "<!--")):
        return False
    if _HISTORICAL_RE.search(clean) and not _ACTIVE_RE.search(clean):
        return False
    return _date_ok(clean, max_age_days)


def _date_ok(clean: str, max_age_days: int) -> bool:
    """True if no date, or the date is recent, or the line is explicitly active."""
    match = _TASK_DATE_RE.search(clean)
    if not match or _ACTIVE_RE.search(clean):
        return True
    try:
        age = (date.today() - date.fromisoformat(match.group(1))).days
    except ValueError:
        return True
    return age <= max_age_days
