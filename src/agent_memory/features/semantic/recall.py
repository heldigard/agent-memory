"""Session-start recall and active re-query.

Two modes:
  * passive (default): read ``currentTask.md`` as the query (SessionStart).
  * active (``query=...``): re-search with a refined query mid-task.

Score-threshold self-stop prunes hits below ``min_score`` (no noise-padding).
Each hit is tagged with a memory type (episodic/semantic/relational) derived
from its path.
"""

from __future__ import annotations

import re
from pathlib import Path

from agent_memory.features.semantic.hybrid import hybrid_search, is_pure_bm25_hit
from agent_memory.features.semantic.index import index_dir, load_index
from agent_memory.features.semantic.search import keyword_fallback
from agent_memory.shared.config import DEFAULT_K, EPISODIC_MARKERS, MIN_SCORE
from agent_memory.shared.paths import bank_dir
from agent_memory.shared.task_lines import is_active_task_line

NO_ACTIVE_TASK_RE = re.compile(
    r"\b(?:sin tarea .*activa|no active task|no current task|no hay tarea .*activa)\b",
    re.IGNORECASE,
)
COMPLETED_HEADING_RE = re.compile(
    r"^#{1,4}\s+.*\b(?:completed|complete|terminad[oa]|completad[oa]|finalizad[oa])\b",
    re.IGNORECASE | re.MULTILINE,
)


def classify_memory(path: str) -> str:
    """Path-derived memory type: episodic / semantic / relational."""
    p = (path or "").lower()
    if "decisions.graph" in p:
        return "relational"
    if any(marker in p for marker in EPISODIC_MARKERS):
        return "episodic"
    return "semantic"


def extract_query_from_task(task_text: str, max_chars: int = 300) -> str:
    """Turn a ``currentTask.md`` body into a search query."""
    no_active_task = bool(NO_ACTIVE_TASK_RE.search(task_text))
    completed_doc = bool(COMPLETED_HEADING_RE.search(task_text))
    lines: list[str] = []
    for raw in task_text.splitlines():
        s = _clean_task_line(raw, no_active_task, completed_doc)
        if s:
            lines.append(s)
        if sum(len(t) for t in lines) >= max_chars:
            break
    return " ".join(lines)[:max_chars].strip()


def _clean_task_line(raw: str, no_active_task: bool, completed_doc: bool) -> str:
    """Return the cleaned text of an active task line, or '' to skip."""
    s = raw.strip()
    if not is_active_task_line(s, no_active_task=no_active_task, completed_doc=completed_doc):
        return ""
    s = re.sub(r"^\s*[-*]\s+\[\s\]\s+", "", s).strip()
    s = re.sub(r"^\*{1,2}[^*:*]{1,40}\*{1,2}:\s*", "", s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"\1", s)
    return s


def recall(
    root: Path, k: int = DEFAULT_K, query: str | None = None, min_score: float = MIN_SCORE
) -> dict:
    """Retrieve task-relevant memory hits (passive from currentTask or active re-query)."""
    memory = bank_dir(root)
    q, source = _resolve_query(memory, query)
    if q is None:
        return {"error": source}
    _, manifest = load_index(index_dir(root))
    if not manifest:
        return {"error": "no semantic index — run `semindex` first", "query": q}
    hits, used_fallback = _gather_hits(root, q, k)
    hits = [h for h in hits if h.get("file") != "currentTask.md"]
    if not used_fallback:
        # Mirror hybrid._filter_min_score via the shared is_pure_bm25_hit so the
        # two paths cannot drift: pure-BM25 hits carry dense score 0.0 by
        # construction, and thresholding them would silently empty recall
        # whenever Ollama is down.
        hits = [
            h
            for h in hits
            if h.get("score") is None
            or is_pure_bm25_hit(h.get("score"), h.get("method"))
            or h.get("score", 0.0) >= min_score
        ]
    for h in hits:
        h["type"] = classify_memory(h.get("file", ""))
    return {
        "query": q,
        "source": "query" if query else "currentTask.md",
        "hits": hits[:k],
        "fallback": used_fallback,
        "min_score": min_score if not used_fallback else None,
    }


def _resolve_query(memory: Path, query: str | None) -> tuple[str | None, str]:
    """Return ``(query, error_or_source)``. On error, query is None."""
    if query:
        q = query.strip()
        return (q, "") if q else (None, "empty --query passed to active recall")
    task_path = memory / "currentTask.md"
    if not task_path.exists():
        return None, f"no currentTask.md at {task_path} (pass query= for active re-query)"
    q = extract_query_from_task(task_path.read_text(encoding="utf-8", errors="replace"))
    if not q:
        return None, "currentTask.md has no usable task text"
    return q, ""


def _gather_hits(root: Path, q: str, k: int) -> tuple[list[dict], bool]:
    """Run hybrid search, fall back to keyword if empty. Returns (hits, used_fallback)."""
    vectors, manifest = load_index(index_dir(root))
    hits = hybrid_search(vectors, manifest, q, k=k + 5)
    if hits:
        return hits, False
    return keyword_fallback(root, q, k=k + 5), True
