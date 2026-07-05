"""Context-graph triples (``decisions.graph.jsonl``).

Structured ``(subject, predicate, object)`` facts complementing the flat
markdown files. Resolves join queries (two-hop traversals) and supersession
that neither keyword nor semantic search can answer.
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

from agent_memory.shared.config import GRAPH_FILE, GRAPH_PREDICATES
from agent_memory.shared.paths import bank_dir
from agent_memory.shared.text import ensure_safe_text


def graph_path(root: Path) -> Path:
    return bank_dir(root) / GRAPH_FILE


def _graph_load(path: Path) -> list[dict]:
    rows: list[dict] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            print(
                f"warn: skipping malformed graph line: {line[:80]}", file=__import__("sys").stderr
            )
    return rows


def _graph_next_id(rows: list[dict]) -> str:
    max_n = 0
    for r in rows:
        m = re.match(r"g_(\d+)", r.get("id", ""))
        if m:
            max_n = max(max_n, int(m.group(1)))
    return f"g_{max_n + 1:03d}"


def _graph_append(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def split_csv(value: str | None) -> list[str] | None:
    """Parse a comma-separated CLI arg into a clean list (or None)."""
    if not value:
        return None
    return [v.strip() for v in value.split(",") if v.strip()]


def graph_add(root: Path, s: str, p: str, o: str, meta: dict | None = None) -> int:
    """Append a triple. Warns on non-standard predicates (still appends).

    ``meta`` optionally carries ``src`` (source file), ``aliases`` (subject
    aliases), and ``supersedes`` (fact ids this invalidates)."""
    m = meta or {}
    ensure_safe_text(f"{s} {p} {o}")
    path = graph_path(root)
    rows = _graph_load(path)
    if p not in GRAPH_PREDICATES:
        print(f"warn: '{p}' not in standard predicates {sorted(GRAPH_PREDICATES)}")
    rid = _graph_next_id(rows)
    row = {
        "id": rid,
        "s": s,
        "p": p,
        "o": o,
        "t": str(date.today()),
        "src": m.get("src") or "systemPatterns.md",
        "supersedes": m.get("supersedes") or [],
        "aliases": m.get("aliases") or [],
    }
    _graph_append(path, row)
    print(f"added {rid}: ({s}) -[{p}]-> ({o})")
    return 0


def _subj_matches(row: dict, subject: str) -> bool:
    if row.get("s") == subject:
        return True
    return subject in (row.get("aliases") or [])


def graph_query(root: Path, subject: str, pred: str | None = None) -> int:
    """Print triples for ``subject`` (alias-aware), optionally filtered by predicate."""
    rows = _graph_load(graph_path(root))
    hits = [r for r in rows if _subj_matches(r, subject) and (pred is None or r.get("p") == pred)]
    if not hits:
        suffix = f" with predicate {pred}" if pred else ""
        print(f"no triples for subject '{subject}'{suffix}")
        return 1
    for r in hits:
        print(f"{r.get('id')}  ({r.get('s')}) -[{r.get('p')}]-> ({r.get('o')})  @{r.get('t')}")
    return 0


def graph_join(root: Path, start: str, pred1: str, pred2: str) -> int:
    """Two-hop traversal: ``start -[pred1]-> X -[pred2]-> Y``."""
    rows = _graph_load(graph_path(root))
    first = {r["o"] for r in rows if r.get("s") == start and r.get("p") == pred1}
    if not first:
        print(f"no {pred1} edges from '{start}'")
        return 1
    results = [r for r in rows if r.get("p") == pred2 and r.get("s") in first]
    if not results:
        print(f"no {pred2} edges from intermediate {sorted(first)}")
        return 1
    for r in results:
        print(f"{start} -[{pred1}]-> {r.get('s')} -[{pred2}]-> {r.get('o')}  [{r.get('id')}]")
    return 0


def graph_show(root: Path) -> int:
    """List all triples."""
    rows = _graph_load(graph_path(root))
    if not rows:
        print(f"(empty graph — {GRAPH_FILE} not found)")
        return 1
    print(f"{len(rows)} triple(s) in {GRAPH_FILE}:")
    for r in rows:
        sup = f"  SUPERSEDES={r['supersedes']}" if r.get("supersedes") else ""
        ali = f"  aliases={r['aliases']}" if r.get("aliases") else ""
        print(
            f"  {r.get('id')}  ({r.get('s')}) -[{r.get('p')}]-> ({r.get('o')})  @{r.get('t')}{sup}{ali}"
        )
    return 0


def graph_supersede(root: Path, new_id: str, old_id: str) -> int:
    """Mark ``new_id`` as superseding ``old_id`` (rewrites metadata in place)."""
    path = graph_path(root)
    rows = _graph_load(path)
    target = next((r for r in rows if r.get("id") == new_id), None)
    if target is None:
        print(f"error: fact id '{new_id}' not found", file=__import__("sys").stderr)
        return 2
    if not any(r.get("id") == old_id for r in rows):
        print(f"error: superseded id '{old_id}' not found", file=__import__("sys").stderr)
        return 2
    target["supersedes"] = sorted(set(target.get("supersedes") or []) | {old_id})
    _graph_rewrite(path, rows)
    print(f"{new_id} now supersedes {target['supersedes']}")
    return 0


def _graph_rewrite(path: Path, rows: list[dict]) -> None:
    """Overwrite the graph file with ``rows`` (enriched metadata, append-only intent)."""
    with path.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


def graph_stale(root: Path) -> int:
    """Show superseded (invalidated) facts and what replaced them."""
    rows = _graph_load(graph_path(root))
    superseded = {str(sid) for r in rows for sid in (r.get("supersedes") or [])}
    if not superseded:
        print("no superseded facts — everything is current")
        return 0
    by_map: dict[str, list[str]] = {}
    for r in rows:
        for sid in r.get("supersedes") or []:
            by_map.setdefault(str(sid), []).append(str(r.get("id") or "?"))
    stale = [r for r in rows if str(r.get("id") or "") in superseded]
    print(f"{len(stale)} stale fact(s):")
    for r in stale:
        rid = str(r.get("id") or "?")
        print(
            f"  {rid}  ({r.get('s')}) -[{r.get('p')}]-> ({r.get('o')})  superseded by {by_map.get(rid, [])}"
        )
    return 0
