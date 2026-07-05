# Changelog

All notable changes to this project are documented here.
Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed
- `_archive_with_summary` no longer grows a file when `tail_count == 0`
  (`lines[-0:]` slicing gotcha). Mirrors the guard already in `compact.archive_old_lines`.
- `build_index` is now serialized via `fcntl.flock` on `.index/.build.lock` so two
  concurrent `semindex` runs (CLI + auto-maintain) can't interleave.
- `tests/test_decision_tracker.py` is now hermetic — `WORKER_ENV_VARS`
  (`CLAUDE_CODE_SUBAGENT_MODEL`, …) leaking from a host proxy shell no longer
  short-circuits the hook under test.

### Added
- `agent-memory doctor` command — proactive health check (over-budget files, broken
  `[[slug]]`/`(slug.md)` topic refs, dead-PID active entries, index shape/orphan/collision
  checks). `--json` emits machine-readable findings; exit 1 on errors.
- `agent-memory status --json` — bank snapshot for hooks/quota tooling (parity with
  `auto-maintain-check --json`). Backed by a new `status_data()` so the human and JSON
  outputs never drift.
- `agent-memory --version` flag (reads `importlib.metadata`).
- Parallel embedding for `semindex` (`ThreadPoolExecutor`, `AGENT_MEMORY_EMBED_WORKERS=4`,
  order-stable, falls back to serial for ≤1 chunk or `workers=1`).
- BM25 tokenization memoized (`lru_cache(4096)`), `Counter(d)` per doc (was
  `d.count(w)` per query term), `np.argpartition` top-k (was full `argsort`).
- Ollama generate-cache prune amortized (`CACHE_PRUNE_EVERY=50` stores).
- Tests: `_archive_with_summary` tail-0 regression, parallel-embed order stability,
  `--version` flag, decision-tracker env isolation.

### Changed
- `build_index` `current` map simplified from `dict[str, float]` (mtime values
  unused) to a `set[str]` of rels.
- Project `CLAUDE.md` filled in (was placeholder template).

### Performance
- BM25 scoring: `O(D·Q·L)` → `O(D·L)` per query (Counter instead of repeated `list.count`).
- Dense top-k: `O(N log N)` → `O(N)` (`argpartition`).
- `semindex` on a large bank: ~N× faster via parallel embedding threads.

## [0.1.0] — initial standalone release

### Added
- Vertical-slice package extracted from the `~/.claude/scripts/project-memory.py`
  monolith (1727 lines): 8 features (`bank`, `entries`, `compact`, `search`,
  `semantic`, `graph`, `maintain`, `coord`) + `shared/` infra.
- Hybrid retrieval (BM25 + dense, RRF fusion) over a local-Ollama embedding index
  (`embeddinggemma`, 768-d). Degrades gracefully to keyword when the daemon is down.
- Per-project index isolation (`.memory-bank/.index/`, never global), chunk-level
  sha256 dedup + model/version sidecars that force one clean re-embed on change.
- Context-graph triple store (`decisions.graph.jsonl`) with alias-aware query and
  two-hop join traversal.
- LLM-assisted `maintain` (propose-only) + `--apply-safe` additive compaction.
- Status taxonomy (`active|wip|blocked|live|completed`) protecting in-flight work
  from archival (the "prompt improver" incident).
- Hooks migrated into the package: `decision_tracker` (Stop), `budget_guard`
  (Stop), `recuerda_auto_append` (Stop).
- Compat shim: `~/.claude/scripts/project-memory.py` delegates to the installed
  `agent-memory` binary so legacy wired paths keep working.

[Unreleased]: https://github.com/heldigard/agent-memory/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/heldigard/agent-memory/releases/tag/v0.1.0
