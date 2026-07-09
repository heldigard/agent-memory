"""Semantic-subcommand CLI handlers (index / search / recall / status / clean).

Each handler takes a parsed ``root`` plus the needed args, prints to stdout, and
returns an exit code. Wired up by ``agent_memory.cli``.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

from agent_memory.features.semantic import recall as recall_mod
from agent_memory.features.semantic.hybrid import hybrid_search
from agent_memory.features.semantic.index import build_index, index_dir, load_index
from agent_memory.features.semantic.search import keyword_fallback
from agent_memory.features.semantic.search import search as dense_search
from agent_memory.shared.entries import filter_inactive_search_text
from agent_memory.shared.ollama import is_alive as ollama_is_alive


@dataclass
class SearchOpts:
    """Search-mode flags bundled so ``cmd_search`` stays under the param budget."""

    min_score: float = 0.20
    dense: bool = False
    do_rerank: bool = False
    include_inactive: bool = False


def cmd_index(root: Path, rebuild: bool) -> int:
    stats = build_index(root, rebuild=rebuild)
    if "error" in stats:
        print(stats["error"], file=sys.stderr)
        return 1
    print(
        f"Indexed {stats['chunks']} chunks from {stats['files_total']} files "
        f"(chunks reused {stats.get('chunks_reused', 0)}, "
        f"re-embedded {stats.get('chunks_reembedded', 0)}; "
        f"files reused {stats['files_reused']}, "
        f"re-embedded {stats['files_reembedded']}; "
        f"rebuild={stats['rebuild']}, model={stats['model']})"
    )
    if stats.get("model_changed"):
        print(
            f"NOTE: embedding model changed since last index -> forced full re-embed "
            f"(model sidecar guard). All vectors now use {stats['model']}."
        )
    if stats["chunks_skipped_no_ollama"]:
        print(
            f"WARNING: {stats['chunks_skipped_no_ollama']} chunks skipped (Ollama unavailable). "
            "Re-run `index` once the daemon is up.",
            file=sys.stderr,
        )
    print(f"Index: {stats['index_dir']}")
    return 0


def cmd_search(root: Path, query: str, k: int, opts: SearchOpts) -> int:
    vectors, manifest = load_index(index_dir(root))
    if opts.dense:
        records = dense_search(root, query, k=k, min_score=opts.min_score)
    else:
        records = hybrid_search(vectors, manifest, query, k=k, do_rerank=opts.do_rerank)
    if not records and not ollama_is_alive():
        records = keyword_fallback(root, query, k=k, include_inactive=opts.include_inactive)
        print("[Ollama down — using keyword fallback]", file=sys.stderr)
    if not opts.include_inactive:
        records = _filter_inactive_records(records)
    _print_records(records, query)
    return 0


def _filter_inactive_records(records: list[dict]) -> list[dict]:
    """Strip superseded lines from chunks and discard empty historical hits."""
    active: list[dict] = []
    for record in records:
        text = filter_inactive_search_text(str(record.get("text", "")))
        if not text:
            continue
        active.append({**record, "text": text})
    return active


def cmd_recall(root: Path, k: int, query: str | None, min_score: float, full: bool) -> int:
    result = recall_mod.recall(root, k=k, query=query, min_score=min_score)
    if "error" in result:
        print(result["error"], file=sys.stderr)
        return 1
    _print_recall_result(result, full)
    return 0


def cmd_status(root: Path) -> int:
    from agent_memory.features.semantic.status import status as index_status

    st = index_status(root)
    for key, val in st.items():
        print(f"{key:18} {val}")
    if st["stale_files"] or st["orphan_chunks"]:
        print("Run `index` (or `clean`) to refresh.")
    return 0


def cmd_clean(root: Path) -> int:
    stats = build_index(root, rebuild=False)
    print(
        f"Cleaned -> {stats['chunks']} chunks "
        f"(reused {stats.get('chunks_reused', 0)}, "
        f"re-embedded {stats.get('chunks_reembedded', 0)}), "
        f"{stats['orphans_dropped']} orphans dropped, "
        f"{stats['files_reused']} files reused, "
        f"{stats['files_reembedded']} re-embedded."
    )
    return 0


def _print_records(records: list[dict], query: str) -> None:
    print(f"## Semantic Memory Search: {query}")
    if not records:
        print("- no matches")
        return
    for r in records:
        print(_record_line(r))


def _record_line(r: dict) -> str:
    """One formatted line for a search hit."""
    if r.get("fallback"):
        tag = " [keyword fallback]"
    elif r.get("rerank_score") is not None:
        tag = f" [rerank={r['rerank_score']}]"
    elif r.get("method"):
        tag = f" [{r['method']}]"
    else:
        tag = ""
    head = f" ({r['heading']})" if r.get("heading") else ""
    line = f"- {r['file']}:{r['start']}-{r['end']} score={r.get('score', 0)}{head}{tag}"
    return line + "\n    " + r["text"].replace("\n", " ")[:240]


def _print_recall_result(result: dict, full: bool) -> None:
    """Format a recall result (passive or active) for stdout."""
    query = result["query"]
    hits = result["hits"]
    source = result.get("source", "currentTask.md")
    fb_tag = " [keyword fallback]" if result.get("fallback") else ""
    mode_tag = "active re-query" if source == "query" else "currentTask.md"
    ms = result.get("min_score")
    ms_tag = f" | min_score>={ms}" if ms is not None else ""
    print(f"## Recall ({mode_tag}){fb_tag}{ms_tag}")
    print(f"query: {query[:200]}")
    if not hits:
        print(
            "- no relevant memory found above threshold "
            "(consider `semindex`, writing more topics, or lowering --min-score)"
        )
    for h in hits:
        print(_recall_hit_line(h, full))


def _recall_hit_line(h: dict, full: bool) -> str:
    """One formatted block for a recall hit (snippet or full text)."""
    head = f" ({h['heading']})" if h.get("heading") else ""
    mtype = h.get("type")
    type_tag = f" [{mtype}]" if mtype else ""
    if full:
        return (
            f"### Matched Memory: {h['file']} (Lines {h['start']}-{h['end']}, "
            f"score={h.get('score', 0)}){head}{type_tag}\n{h['text']}\n"
        )
    return (
        f"- {h['file']}:{h['start']}-{h['end']} score={h.get('score', 0)}{head}{type_tag}\n"
        f"    {h['text'].replace(chr(10), ' ')[:200]}"
    )
