"""Keyword search over a project's ``.memory-bank/``.

Pure keyword matching (all-terms-AND) across core files and topics. Semantic
search lives in ``features/semantic``; this is the fast grep-style fallback.
"""

from __future__ import annotations

import json
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
    root: Path,
    query: str,
    max_results: int = 20,
    *,
    include_inactive: bool = False,
    json_out: bool = False,
) -> None:
    """Search core and topic memory files; print matches as ``rel:line: text``."""
    memory = bank_dir(root)
    if not memory.exists():
        if json_out:
            print(json.dumps({"query": query, "count": 0, "results": []}))
        return
    terms = _query_terms(query)
    matches: list[dict[str, object]] = []
    for rel, lineno, line in iter_all_lines(memory):
        if not include_inactive and is_inactive_search_line(line):
            continue
        if not _line_matches(line, terms):
            continue
        matches.append({"file": rel, "line": lineno, "text": line[:220]})
        if len(matches) >= max_results:
            break
    if json_out:
        print(
            json.dumps(
                {"query": query, "count": len(matches), "results": matches},
                ensure_ascii=False,
            )
        )
        return
    print(f"## Memory Search: {query}")
    if not matches:
        print("- no matches")
        return
    for m in matches:
        print(f"- {m['file']}:{m['line']}: {m['text']}")
