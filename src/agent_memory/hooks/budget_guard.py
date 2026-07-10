"""Stop hook: warn when ``.memory-bank/`` files approach or exceed their budgets.

Advisory only — NEVER auto-truncates (the user decides). Yellow at 80%, red at
>=100%. Budgets are the single source of truth in :mod:`agent_memory.shared.config`
(no duplicate literals), so the CLI ``status`` command and this hook always agree.

Registered in ``~/.claude/settings.json`` as a Stop hook; ``~/.claude/hooks/
memory-bank-budget-guard.py`` is a backcompat shim that imports :func:`main`.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from agent_memory.shared.config import FILES, TOPIC_INDEX_LIMIT, TOPIC_SOFT_LIMIT, TOPICS_DIR
from agent_memory.shared.text import line_count

WARN_PCT = 0.80  # 80% threshold for the yellow warning


def format_warning(fname: str, n: int, limit: int) -> str:
    """Return a YELLOW/RED warning line, or '' if the file is within budget."""
    pct = n / limit
    if n > limit:
        return (
            f"  [RED]    {fname}: {n} lines (budget {limit}, {pct:.0%}) — over budget,"
            " archive or split"
        )
    if pct >= WARN_PCT:
        return (
            f"  [YELLOW] {fname}: {n} lines (budget {limit}, {pct:.0%}) — approaching limit,"
            " consider archiving soon"
        )
    return ""


def _warning_for(fname: str, path: Path, limit: int, warnings: list[str]) -> None:
    """Append a warning line for one file if it is over the warn threshold."""
    if not path.exists():
        return
    msg = format_warning(fname, line_count(path), limit)
    if msg:
        warnings.append(msg)


def _check_topics(bank: Path, warnings: list[str]) -> None:
    """Append warnings for topic files over their budgets (archives exempt)."""
    topics = bank / TOPICS_DIR
    if not topics.is_dir():
        return
    for p in topics.glob("*.md"):
        _check_topic_file(p, warnings)


def _check_topic_file(p: Path, warnings: list[str]) -> None:
    """Warn for one topic file; archives are exempt, _index uses the small limit."""
    if p.name.startswith("archive-"):
        return
    limit = TOPIC_INDEX_LIMIT if p.name == "_index.md" else TOPIC_SOFT_LIMIT
    _warning_for(f"{TOPICS_DIR}/{p.name}", p, limit, warnings)


def collect_warnings(bank: Path) -> list[str]:
    """Return all YELLOW/RED warning lines for the core + topic files in ``bank``."""
    warnings: list[str] = []
    for fname, (_, limit) in FILES.items():
        _warning_for(fname, bank / fname, limit, warnings)
    _check_topics(bank, warnings)
    return warnings


def main() -> int:
    """Print warnings to stderr (advisory; exit 0 always — never blocks Stop)."""
    project_root = Path(os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd()))
    bank = project_root / ".memory-bank"
    if not bank.is_dir():
        return 0  # no memory bank, silent pass
    warnings = collect_warnings(bank)
    if not warnings:
        return 0
    header = f"Memory bank budget check (warn at {WARN_PCT:.0%}):\n"
    action = (
        "\nAction: archive detail to topics/archive-<date>-<topic>.md; keep summary"
        " in core. Or split a long topic into 2-3 focused ones."
    )
    sys.stderr.write(header + "\n".join(warnings) + action + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
