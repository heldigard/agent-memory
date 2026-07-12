# Context
> Current state of agent-memory

- Vertical-slice package: 9 features (bank/entries/compact/search/semantic/graph/maintain/coord/**doctor**) + `shared/` + `hooks/`.
- CLI entry: `agent-memory` (or `project-memory` symlink). Python ≥3.11. `--version`, `--root` global flags.
- Hybrid search: BM25 + dense RRF via local Ollama embeddings (`embeddinggemma`, 768-d). Degrades to keyword when daemon down.
- BM25 path is memoized (`lru_cache` tokenize) + `Counter(d)` per doc + `argpartition` top-k; embed parallel via `ThreadPoolExecutor` (`AGENT_MEMORY_EMBED_WORKERS=4`).
- Index build serialized via `fcntl.flock` on `.index/.build.lock`; chunk-level sha256 dedup + model/version sidecars.
- Graph: triple store (`s,p,o`) with alias-aware query + 2-hop join via `agent-memory graph`.
- `maintain`: LLM-assisted audit (propose-only); `--apply-safe` additive compaction. `doctor`: proactive health check (budgets/refs/PIDs/index/collisions/graph).
- `status`/`doctor`/`auto-maintain-check` all emit `--json` for hook/quota consumption.
- Automatic remember/decision hooks share canonical credential redaction; graph JSONL parsing skips
  invalid core rows, normalizes optional metadata, and feeds doctor schema diagnostics.
- Tests: 206, ruff+mypy+VS-guard clean. Editable install: `uv pip install -e ".[test]"`.
- Shim contract: `~/.claude/scripts/project-memory.py` delegates to `~/.local/bin/agent-memory`.
