"""``agent-memory`` CLI entry — argparse + dispatch to feature slices.

Public entry point: :func:`main` (registered as the ``agent-memory`` console
script in ``pyproject.toml``). The legacy ``project-memory`` symlink points at
this same binary, so the 40-odd ecosystem references keep working untouched.
"""

from __future__ import annotations

import argparse
import json
import sys
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as pkg_version
from pathlib import Path
from typing import Any

from agent_memory.features.bank.command import (
    add_entry,
    add_topic_entry,
    init_memory,
    read_memory,
    status_bank,
)
from agent_memory.features.compact.command import archive_topic, compact_memory
from agent_memory.features.coord.command import coord_cleanup, coord_status
from agent_memory.features.doctor.command import doctor
from agent_memory.features.entries.command import supersede_entry
from agent_memory.features.graph.command import (
    graph_add,
    graph_join,
    graph_query,
    graph_show,
    graph_stale,
    graph_supersede,
)
from agent_memory.features.maintain.auto import run_auto_maintain
from agent_memory.features.maintain.command import handoff, maintain
from agent_memory.features.search.command import search_memory
from agent_memory.shared.paths import project_root
from agent_memory.shared.text import split_csv


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Build the top-level parser and all subcommand parsers."""
    parser = argparse.ArgumentParser(
        prog="agent-memory",
        description="Persistent per-project memory (.memory-bank/) for LLM coding agents.",
    )
    # argparse reports --version itself (exit 0); fallback string covers a
    # non-installed (no metadata) dev checkout.
    try:
        _ver = pkg_version("agent-memory-cli")
    except PackageNotFoundError:
        _ver = "0.0.0+local"
    parser.add_argument("--version", action="version", version=f"agent-memory {_ver}")
    parser.add_argument("--root", type=Path, help="Project root override")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="Create standard .memory-bank files if missing")
    status_p = sub.add_parser("status", help="Show memory bank files and line counts")
    status_p.add_argument("--json", action="store_true", help="Emit a machine-readable snapshot")
    sub.add_parser("handoff", help="Generate a session handoff summary for activeContext.md")
    compact = sub.add_parser("compact", help="Enforce line budgets on core memory files")
    compact.add_argument("--topics", action="store_true", help="Also compact topic files")

    maintain_p = sub.add_parser(
        "maintain",
        help="LLM-assisted audit: local Ollama PROPOSES duplicates/stale/over-budget "
        "(default mutates nothing); --apply-safe adds additive compact-with-summary",
    )
    maintain_p.add_argument("--apply-safe", action="store_true")
    maintain_p.add_argument("--no-llm", action="store_true")
    maintain_p.add_argument("-o", "--output", default=None)

    read = sub.add_parser("read", help="Print bounded memory context")
    read.add_argument("--per-file-lines", type=int, default=12)
    read.add_argument("--total-lines", type=int, default=80)
    read.add_argument("--topic", help="Read one deep-memory topic by name")
    read.add_argument("--topic-lines", type=int, default=80)
    read.add_argument("--deep", action="store_true", help="Higher budgets for complex tasks")

    add = sub.add_parser("add", help="Append a safe concise entry")
    add.add_argument("--file", required=True, help="Target file or alias")
    add.add_argument("--text", required=True)
    add.add_argument("--status", help="active|wip|blocked|live|completed|superseded")
    add.add_argument("--session", help="Orchestrator session id")

    topic = sub.add_parser("topic", help="Append deep context to topics/<slug>.md")
    topic.add_argument("--name", required=True)
    topic.add_argument("--text", required=True)
    topic.add_argument("--status", help="active|wip|blocked|live|completed|superseded")

    supersede = sub.add_parser(
        "supersede-entry", help="mark one uniquely matching durable entry as historical"
    )
    supersede.add_argument("query", help="case-insensitive unique text fragment")
    supersede.add_argument("--file", help="optional memory filename to narrow the match")

    search = sub.add_parser("search", help="Keyword search core and topic memory files")
    search.add_argument("query")
    search.add_argument("--max-results", type=int, default=20)
    search.add_argument(
        "--include-inactive", action="store_true", help="include superseded historical entries"
    )

    sem = sub.add_parser("semsearch", help="Semantic search (local Ollama embeddings)")
    sem.add_argument("query")
    sem.add_argument("-k", type=int, default=5)
    sem.add_argument("--min-score", type=float, default=0.20)
    sem.add_argument("--dense", action="store_true", help="Pure dense cosine (skip BM25)")
    sem.add_argument("--rerank", action="store_true", help="LLM rerank top candidates (slow)")
    sem.add_argument(
        "--include-inactive", action="store_true", help="include superseded historical entries"
    )

    semindex = sub.add_parser("semindex", help="Build/update the per-project semantic index")
    semindex.add_argument("--rebuild", action="store_true")

    sub.add_parser("semstatus", help="Semantic index health")
    sub.add_parser("semclean", help="Purge orphan embeddings + compact the index")

    semrecall = sub.add_parser(
        "semrecall", help="Session-start (currentTask.md) OR active mid-task re-query (--query)"
    )
    semrecall.add_argument("-k", type=int, default=5)
    semrecall.add_argument("--query", help="Active re-query (MRAgent-style reconstruction)")
    semrecall.add_argument("--min-score", type=float, default=0.20)
    semrecall.add_argument("--full", action="store_true")

    arch = sub.add_parser("archive-topic", help="Move a whole topic to topics/archive/")
    arch.add_argument("slug")
    arch.add_argument("--force", action="store_true")

    coord = sub.add_parser("coord", help="Cross-CLI agent coordination registry")
    coord.add_argument("--cleanup", action="store_true")

    doc = sub.add_parser(
        "doctor",
        help="Health check: budgets, broken refs, dead PIDs, index consistency",
    )
    doc.add_argument("--json", action="store_true", help="Emit findings as JSON")

    graph = sub.add_parser("graph", help="Context-graph triples (decisions.graph.jsonl)")
    gsub = graph.add_subparsers(dest="graph_cmd", required=True)
    _add_graph_parsers(gsub)

    sub.add_parser(
        "auto-maintain",
        help="Lightweight SessionStart maintenance (index refresh + staleness + budget)",
    )
    auto_check = sub.add_parser(
        "auto-maintain-check",
        help="Staleness + budget checks only (skips index refresh); for fast SessionStart paths",
    )
    auto_check.add_argument("--json", action="store_true", help="Emit JSON summary to stdout")

    return parser.parse_args(argv)


def _add_graph_parsers(gsub: Any) -> None:
    """Register the ``graph`` sub-subcommands."""
    g_add = gsub.add_parser("add", help="Append a (s, p, o) triple")
    g_add.add_argument("--s", required=True, dest="g_s", help="Subject entity")
    g_add.add_argument("--p", required=True, dest="g_p", help="Predicate")
    g_add.add_argument("--o", required=True, dest="g_o", help="Object entity")
    g_add.add_argument("--src", help="Source memory file (default systemPatterns.md)")
    g_add.add_argument("--aliases", help="Comma-separated alias names for the subject")
    g_add.add_argument("--supersedes", help="Comma-separated fact ids this invalidates")

    g_query = gsub.add_parser("query", help="Triples for a subject (alias-aware)")
    g_query.add_argument("subject")
    g_query.add_argument("--p", dest="g_p", help="Filter by predicate")

    g_join = gsub.add_parser("join", help="Two-hop traversal: start -[pred1]-> X -[pred2]-> Y")
    g_join.add_argument("start")
    g_join.add_argument("pred1")
    g_join.add_argument("pred2")

    gsub.add_parser("show", help="List all triples")
    g_sup = gsub.add_parser("supersede", help="Mark <new_id> as superseding <old_id>")
    g_sup.add_argument("new_id")
    g_sup.add_argument("old_id")
    gsub.add_parser("stale", help="Show superseded (invalidated) facts")


def main() -> int:
    """Dispatch a parsed command to its feature slice."""
    args = parse_args()
    root = project_root(args.root)
    cmd = args.command

    if cmd == "init":
        init_memory(root)
        return 0
    if cmd == "status":
        status_bank(root, json_out=args.json)
        return 0
    if cmd == "handoff":
        handoff(root)
        return 0
    if cmd == "compact":
        compact_memory(root, include_topics=args.topics)
        return 0
    if cmd == "maintain":
        maintain(root, apply_safe=args.apply_safe, output=args.output, no_llm=args.no_llm)
        return 0
    if cmd == "read":
        per_file = 25 if args.deep else args.per_file_lines
        total = 150 if args.deep else args.total_lines
        read_memory(root, per_file, total, topic=args.topic, topic_lines=args.topic_lines)
        return 0
    if cmd == "add":
        add_entry(root, args.file, args.text, status=args.status, session=args.session)
        return 0
    if cmd == "topic":
        add_topic_entry(root, args.name, args.text, status=args.status)
        return 0
    if cmd == "supersede-entry":
        return supersede_entry(root, args.query, file_name=args.file)
    if cmd == "archive-topic":
        archive_topic(root, args.slug, force=args.force)
        return 0
    if cmd == "search":
        search_memory(
            root,
            args.query,
            max_results=args.max_results,
            include_inactive=args.include_inactive,
        )
        return 0
    if cmd == "semsearch":
        from agent_memory.features.semantic.command import SearchOpts, cmd_search

        return cmd_search(
            root,
            args.query,
            args.k,
            SearchOpts(args.min_score, args.dense, args.rerank, args.include_inactive),
        )
    if cmd == "semindex":
        from agent_memory.features.semantic.command import cmd_index

        return cmd_index(root, args.rebuild)
    if cmd == "semstatus":
        from agent_memory.features.semantic.command import cmd_status

        return cmd_status(root)
    if cmd == "semclean":
        from agent_memory.features.semantic.command import cmd_clean

        return cmd_clean(root)
    if cmd == "semrecall":
        from agent_memory.features.semantic.command import cmd_recall

        return cmd_recall(root, args.k, args.query, args.min_score, args.full)
    if cmd == "coord":
        return coord_cleanup(root) if args.cleanup else coord_status(root)
    if cmd == "auto-maintain":
        result = run_auto_maintain(root, check_only=False)
        print(json.dumps(result))
        return 0
    if cmd == "auto-maintain-check":
        result = run_auto_maintain(root, check_only=True)
        if args.json:
            print(json.dumps(result))
        return 0
    if cmd == "graph":
        return _dispatch_graph(args, root)
    if cmd == "doctor":
        return doctor(root, json_out=args.json)
    return 0


def _dispatch_graph(args: argparse.Namespace, root: Path) -> int:
    """Dispatch a ``graph`` sub-subcommand."""
    g = args.graph_cmd
    if g == "add":
        meta = {
            "src": args.src,
            "aliases": split_csv(args.aliases) or [],
            "supersedes": split_csv(args.supersedes) or [],
        }
        return graph_add(root, args.g_s, args.g_p, args.g_o, meta)
    if g == "query":
        return graph_query(root, args.subject, args.g_p)
    if g == "join":
        return graph_join(root, args.start, args.pred1, args.pred2)
    if g == "show":
        return graph_show(root)
    if g == "supersede":
        return graph_supersede(root, args.new_id, args.old_id)
    if g == "stale":
        return graph_stale(root)
    return 0


if __name__ == "__main__":
    sys.exit(main())
