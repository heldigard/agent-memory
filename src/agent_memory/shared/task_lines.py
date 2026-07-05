"""Active-task-line heuristic shared by ``recall`` and ``maintain`` (handoff).

A single canonical implementation of "does this line describe CURRENT (not
historical, not checked-off) task work?" — previously duplicated in
``features/semantic/recall.py`` and ``features/maintain/command.py`` with
subtle divergence (recall skipped ``- [x]`` checked lines; maintain did not).

The default call ``is_active_task_line(line)`` matches the historical maintain
behavior EXCEPT it now also skips checked tasks, which is strictly more
correct for handoff extraction too.
"""

from __future__ import annotations

import re
from datetime import date, datetime

ACTIVE_STATUS_RE = re.compile(
    r"\b(active|wip|live|in[- ]?progress|actual|activo|en curso)\b", re.IGNORECASE
)
HISTORICAL_TASK_RE = re.compile(
    r"\b(history|hist[oó]rico|complete|completed|done|shipped|merged|closed|finished|"
    r"archive|archived|old|viejo|terminad[oa]|completad[oa]|finalizad[oa])\b",
    re.IGNORECASE,
)
TASK_DATE_RE = re.compile(r"\b(20\d{2}-\d{2}-\d{2})\b")
CHECKED_TASK_RE = re.compile(r"^\s*[-*]\s+\[[xX]\]\s+")


def task_age_days(iso: str) -> int | None:
    """Days between an ISO date and today. None if unparseable."""
    try:
        d = datetime.fromisoformat(iso).date()
    except ValueError:
        return None
    return (date.today() - d).days


def is_active_task_line(
    line: str,
    max_age_days: int = 14,
    no_active_task: bool = False,
    completed_doc: bool = False,
) -> bool:
    """Heuristic: a line describing current (not historical, not checked) task work.

    ``no_active_task`` / ``completed_doc`` flags relax the rule for documents that
    signal their state structurally (used by recall's task-query extraction).
    """
    if CHECKED_TASK_RE.match(line):
        return False
    clean = line.strip().lstrip("-* ").strip()
    clean = re.sub(r"^\[\s\]\s+", "", clean).strip()
    if not clean or clean.startswith(("#", ">", "<!--")):
        return False
    if no_active_task and not ACTIVE_STATUS_RE.search(clean):
        return False
    if completed_doc and not _is_unchecked_or_active(line, clean):
        return False
    if HISTORICAL_TASK_RE.search(clean) and not ACTIVE_STATUS_RE.search(clean):
        return False
    return _task_date_ok(clean, max_age_days)


def _is_unchecked_or_active(line: str, clean: str) -> bool:
    """True if the line is an unchecked task or explicitly marked active."""
    unchecked = re.match(r"^\s*[-*]\s+\[\s\]\s+", line)
    return bool(unchecked) or bool(ACTIVE_STATUS_RE.search(clean))


def _task_date_ok(clean: str, max_age_days: int) -> bool:
    """True if no date, recent date, or the line is explicitly active."""
    match = TASK_DATE_RE.search(clean)
    if not match or ACTIVE_STATUS_RE.search(clean):
        return True
    age = task_age_days(match.group(1))
    return age <= max_age_days if age is not None else True
