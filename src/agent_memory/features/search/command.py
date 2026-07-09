"""Keyword search over a project's ``.memory-bank/``.

Pure keyword matching (all-terms-AND) across core files and topics. Semantic
search lives in ``features/semantic``; this is the fast grep-style fallback.
"""

from __future__ import annotations

import re
from pathlib import Path

from agent_memory.shared.entries import is_inactive_search_line
from agent_memory.shared.paths import bank_dir, iter_all_lines


def _query_terms(query: str) -> list[str]:
    """Lowercase tokens longer than one char; empty query aborts."""
    terms = [t.lower() for t in re.findall(r"[\w.-]+", query) if len(t) > 1]
    if not terms:
        raise SystemExit("Search query is empty.")
    return terms


def _line_matches(line: str, terms: list[str]) -> bool:
    lower = line.lower()
    return all(term in lower for term in terms)


def search_memory(
    root: Path, query: str, max_results: int = 20, *, include_inactive: bool = False
) -> None:
    """Search core and topic memory files; print matches as ``rel:line: text``."""
    memory = bank_dir(root)
    if not memory.exists():
        return
    print(f"## Memory Search: {query}")
    terms = _query_terms(query)
    results = 0
    for rel, lineno, line in iter_all_lines(memory):
        if not include_inactive and is_inactive_search_line(line):
            continue
        if not _line_matches(line, terms):
            continue
        print(f"- {rel}:{lineno}: {line[:220]}")
        results += 1
        if results >= max_results:
            return
    if results == 0:
        print("- no matches")
