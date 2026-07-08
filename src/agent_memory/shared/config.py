"""Central configuration for agent-memory.

Single source of truth for file budgets, aliases, status taxonomy, window
hours, graph predicates, and semantic-retrieval constants. Features import
from here instead of duplicating literals (DRY).
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Memory-bank file table
# ---------------------------------------------------------------------------
# Ceilings MUST stay in sync with the documented budgets in the Memory System
# v5 rule. Each entry: (human description, line budget).
FILES: dict[str, tuple[str, int]] = {
    "MEMORY.md": ("Compact project index", 80),
    "CONTEXT.md": ("Current state", 150),
    "REFERENCE.md": ("Stable facts", 200),
    "agent-sessions.md": ("Active agent registry", 100),
    "currentTask.md": ("Active task", 80),
    "activeContext.md": ("Session handoff", 200),
    "progress.md": ("Completed work", 300),
    "systemPatterns.md": ("Decisions and patterns", 500),
    "dead-ends.md": ("Failed approaches", 300),
}

TOPICS_DIR = "topics"
TOPIC_SOFT_LIMIT = 800
TOPIC_INDEX_LIMIT = 80  # _index.md is a soft map, not deep content

READ_ORDER: list[str] = [
    "MEMORY.md",
    "CONTEXT.md",
    "REFERENCE.md",
    "agent-sessions.md",
    f"{TOPICS_DIR}/_index.md",
    "currentTask.md",
    "activeContext.md",
    "progress.md",
    "systemPatterns.md",
    "dead-ends.md",
]

FILE_ALIASES: dict[str, str] = {
    "memory": "MEMORY.md",
    "context": "CONTEXT.md",
    "reference": "REFERENCE.md",
    "agentsessions": "agent-sessions.md",
    "agent-sessions": "agent-sessions.md",
    "task": "currentTask.md",
    "currenttask": "currentTask.md",
    "active": "activeContext.md",
    "activecontext": "activeContext.md",
    "progress": "progress.md",
    "patterns": "systemPatterns.md",
    "systempatterns": "systemPatterns.md",
    "deadends": "dead-ends.md",
    "dead-ends": "dead-ends.md",
}

SECRET_RE = re.compile(
    r"(api[_-]?key|access[_-]?token|refresh[_-]?token|password|passwd|secret|"
    r"private[_-]?key|BEGIN [A-Z ]*PRIVATE KEY|sk-[A-Za-z0-9_-]{20,})",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Entry timestamp + status taxonomy (guards against the "prompt improver"
# incident: an agent compacted memory mid-deploy and lost completed work).
# ---------------------------------------------------------------------------
# Valid status values for an entry's `| status:X` segment.
VALID_STATUS: frozenset[str] = frozenset({"active", "wip", "blocked", "live", "completed"})

# Status values that NEVER get archived (work in flight or permanent reference).
NEVER_ARCHIVED: frozenset[str] = frozenset({"active", "wip", "blocked", "live"})

# Defaults for the archive / injection freshness windows (hours). Overridable
# via env so a long deploy or a stricter injection policy can tune without
# code changes.
DEFAULT_ARCHIVE_WINDOW_HOURS = 6.0
DEFAULT_INJECTION_WINDOW_HOURS = 12.0

# Staleness threshold in days. Overridable via MEMORY_STALENESS_DAYS env var.
STALENESS_DAYS = 14

# ---------------------------------------------------------------------------
# Context-graph (decisions.graph.jsonl)
# ---------------------------------------------------------------------------
GRAPH_FILE = "decisions.graph.jsonl"
GRAPH_PREDICATES: frozenset[str] = frozenset(
    {
        "DECIDED",
        "DEPENDS_ON",
        "ASSIGNED_TO",
        "OWNS",
        "BLOCKED_BY",
        "DELEGATED_TO",
        "SUPERSEDES",
        "USES",
        "REPLACES",
        "SYNCED_FROM",
        "COMPLEMENTS",
        "REJECTED_AS",
    }
)

# ---------------------------------------------------------------------------
# Semantic retrieval (BM25 + dense, fused via Reciprocal Rank Fusion)
# ---------------------------------------------------------------------------
INDEX_DIRNAME = ".index"
VECTORS_FILE = "vectors.npz"
MANIFEST_FILE = "manifest.json"
EMBED_MODEL_FILE = "embed_model.txt"
# Bump forces a full re-embed of every chunk. v1 = raw vectors; v2 = L2-normalized
# at index time (so dense cosine skips per-query renormalization) + chunk-level
# dedup via sha256 (only changed chunks within a touched file get re-embedded).
INDEX_VERSION = "v2"
VERSION_FILE = "version.txt"
MAX_CHUNK_CHARS = 1200
DEFAULT_K = 5
MIN_SCORE = 0.20

RRF_K = 60
BM25_K1 = 1.5
BM25_B = 0.75
HYBRID_POOL = 20
RERANK_TOPN = 12

# Maintenance LLM model (overrides via env, same var as codeq summary layer).
# Tracks the codeq_sum bench winner — they intentionally share CODEQ_SUMMARY_MODEL.
# 2026-07-05 round-5: SetneufPT took #1 (combined 2.0) after batiai/gemma4-e4b:q4
# collapsed in the tie-break (Ollama 0.31.x sampling drift on hard prompts). See
# ~/ollama-bench/RANKING.md §codeq_sum.
MAINT_MODEL_DEFAULT = "SetneufPT/Qwopus3.5-4B-Coder-MTP_Q4_64k_8GB-GPU:latest"
MAINT_AUDIT_LINE_CAP = 150
MAINT_AUDIT_CHAR_BUDGET = 6000

# Memory-type heuristic markers (episodic vs semantic vs relational).
EPISODIC_MARKERS: tuple[str, ...] = (
    "progress",
    "session-handoffs",
    "agent-sessions",
    "dead-ends",
    "currenttask",
    "activecontext",
    "archive",
    "foreign-sessions",
)
OPERATIONAL_TOPIC_SLUGS: tuple[str, ...] = (
    "agent-sessions",
    "foreign-sessions",
    "session-handoffs",
)
