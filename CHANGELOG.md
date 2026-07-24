# Changelog

All notable changes to this project are documented here.
Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- `ollama.embed_ready()` — probes `/api/embeddings` (not just `/api/tags`) so doctor
  and `semstatus` detect partial Ollama installs where tags answer but inference is
  broken (the 2026-07-17 dead-end). Doctor emits `ollama-embed` warn when tags-up/embed-down.
- `semstatus` now reports `vector_dim`, `embed_model`, `index_version`, and
  `ollama_embed_ready` (README dim claim was previously unmet).
- `search --json` and `semsearch --json` for machine-readable hook/automation output.
- Hybrid `semsearch` honors `--min-score` (was ignored on the default non-`--dense` path;
  pure BM25 hits are preserved when dense score is absent).
- `docs/systemd/install-semwatch.sh` — one-shot Ubuntu-native user-unit installer for
  always-on `semwatch`.
- `semwatch` subcommand — debounced stdlib polling of `.memory-bank` that triggers
  incremental reindex on change (same `flock` build lock as `semindex`; Ctrl-C exits
  cleanly). Systemd user unit template at `docs/systemd/agent-memory-semwatch.service`.
- `semstatus --json` — machine-readable index health snapshot, matching the `--json`
  parity of `status`/`doctor`/`auto-maintain-check` for hook and quota tooling.
- `agent-memory doctor` command — proactive health check (over-budget files, broken
  `[[slug]]`/`(slug.md)` topic refs, dead-PID active entries, index shape/orphan/collision,
  and decision-graph integrity checks). `--json` emits machine-readable findings; exit 1 on errors.
- `doctor` injection-window check — warns on bank lines the bounded SessionStart `read`
  elides, so injected startup context can't silently hide entries.
- `agent-memory status --json` — bank snapshot for hooks/quota tooling (parity with
  `auto-maintain-check --json`). Backed by a new `status_data()` so the human and JSON
  outputs never drift.
- `agent-memory --version` flag (reads `importlib.metadata`).
- `compact --target-ratio RATIO` — compact proactively down to a budget fraction
  (0 < ratio ≤ 1), resolving warnings before files hit the hard budget.
- Parallel embedding for `semindex` (`ThreadPoolExecutor`, `AGENT_MEMORY_EMBED_WORKERS=4`,
  order-stable, falls back to serial for ≤1 chunk or `workers=1`).
- BM25 tokenization memoized (`lru_cache(4096)`), `Counter(d)` per doc (was
  `d.count(w)` per query term), `np.argpartition` top-k (was full `argsort`).
- Ollama generate-cache prune amortized (`CACHE_PRUNE_EVERY=50` stores).
- Tests: `_archive_with_summary` tail-0 regression, parallel-embed order stability,
  `--version` flag, decision-tracker env isolation.

### Changed
- README `CODEQ_SUMMARY_MODEL` default aligned with runtime
  `MAINT_MODEL_DEFAULT` (TeichAI Qwen3.5-9B-Fable-5).
- `graph query`/`join`/`show` are supersession-aware: `query`/`show` tag invalidated
  rows `[STALE <id>]` and `join` excludes superseded edges from both hops, so a two-hop
  traversal never reasons over a retracted decision.
- `build_index` `current` map simplified from `dict[str, float]` (mtime values
  unused) to a `set[str]` of rels.
- Project `CLAUDE.md` filled in (was placeholder template).

### Fixed
- Automatic remember/decision hooks now redact credential-shaped values through the canonical
  memory-safety policy instead of bypassing explicit-write safeguards or maintaining a weaker,
  duplicated detector.
- Decision-graph reads now skip invalid core JSONL records and normalize invalid optional metadata
  without losing healthy facts; `doctor` reports syntax, schema, duplicate-ID, and dangling-reference
  problems from the same shared parser.
- Graph supersede rewrites are atomic, so interruption cannot truncate the durable decision graph.
- Session-start staleness now reports only old unresolved operational entries in mutable core state,
  avoiding false warnings from durable references, topics, and archives. Startup injection also
  hides explicitly inactive statuses while preserving their opt-in search visibility.
- `_archive_with_summary` no longer grows a file when `tail_count == 0`
  (`lines[-0:]` slicing gotcha). Mirrors the guard already in `compact.archive_old_lines`.
- `build_index` is now serialized via `fcntl.flock` on `.index/.build.lock` so two
  concurrent `semindex` runs (CLI + auto-maintain) can't interleave.
- Hooks resolve the memory bank via git-root climb from a nested cwd, matching
  `decision_tracker`'s long-standing behavior.
- `project_root` honors `CLAUDE_PROJECT_DIR`, resisting cwd drift in spawned shells.
- `maintain` archives topic files to `<bank>/topics/archive/`, not `topics/topics/archive/`.
- `compact`/`add` archive topic files to `<bank>/topics/archive/` too (same double-nesting
  bug as `maintain`, fixed there in 8099ae4 but missed here); `add --file topics/<slug>.md`
  now compacts at `TOPIC_SOFT_LIMIT` instead of the generic 60-line fallback.
- `semrecall` no longer returns zero hits when Ollama is down — pure-BM25 hits (dense
  score 0.0 by construction) are exempt from `--min-score`, mirroring `hybrid._filter_min_score`.
- `supersede-entry` now actually rewrites legacy date-only entries (`- YYYY-MM-DD text`,
  no `|`/`:` after the date) instead of reporting success on a silent no-op.
- `embed_ready` tolerates a malformed `AGENT_MEMORY_EMBED_READY_TIMEOUT` (falls back to
  20s) instead of crashing `doctor`/`semstatus` with `ValueError`.
- `tests/test_decision_tracker.py` is now hermetic — `WORKER_ENV_VARS`
  (`CLAUDE_CODE_SUBAGENT_MODEL`, …) leaking from a host proxy shell no longer
  short-circuits the hook under test.

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
