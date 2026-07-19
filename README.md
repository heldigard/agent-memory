# agent-memory

Persistent per-project memory (`.memory-bank/`) for LLM coding agents —
semantic recall, decision graph, and bounded maintenance. Cross-CLI: works the
same from Claude Code, Codex, OpenCode, Gemini, Qwen, Kimi.

Each project keeps its own `.memory-bank/` of markdown files (current task,
active context, progress, decisions, dead-ends, deep topics). `agent-memory`
reads/writes bounded slices of it, runs hybrid (BM25 + dense) recall over a
local-Ollama embedding index, and maintains a structured `(subject, predicate,
object)` decision graph for join queries that flat files + vectors can't
answer.

## Install (dev)

```bash
git clone https://github.com/heldigard/agent-memory
cd agent-memory
uv venv && . .venv/bin/activate
uv pip install -e ".[test]"
```

## Usage

```bash
agent-memory --version                # print version + exit
agent-memory init                     # bootstrap .memory-bank/ in this project
agent-memory status                   # file counts vs budgets + staleness
agent-memory read                     # bounded startup context (add --deep for bigger budgets)
agent-memory read --topic auth-flow   # read one deep-memory topic
agent-memory add --file progress --text "shipped X" --status completed
agent-memory topic --name auth-flow --text "..."
agent-memory search "auth token"      # current keyword results; hides superseded entries
agent-memory search "auth token" --include-inactive  # historical audit
agent-memory search "auth token" --json              # machine-readable matches
agent-memory supersede-entry "old model decision" --file progress.md

# Semantic (local Ollama embeddings; degrades to keyword when daemon down)
agent-memory semindex                 # build/update the embedding index
agent-memory semindex --rebuild       # force full re-embed
agent-memory semstatus                # index health (chunks, dim, model, embed readiness)
agent-memory semstatus --json         # machine-readable health snapshot (hooks/quota)
agent-memory semclean                 # purge orphan embeddings + compact
agent-memory semwatch                 # poll the bank and reindex on change (Ctrl-C stops)
agent-memory semwatch --interval 5 --debounce 2   # slower, quieter polling
agent-memory semsearch "cross-cli handoff" --min-score 0.25
agent-memory semsearch "handoff" --json               # hits as JSON
agent-memory semsearch "old decision" --include-inactive  # include superseded chunks
agent-memory semrecall                # SessionStart recall from currentTask.md
agent-memory semrecall --query "cross-cli handoff" --min-score 0.35  # active re-query

# Context graph (decisions.graph.jsonl) — joins flat files + vectors can't answer
agent-memory graph add --s AuthModule --p DEPENDS_ON --o RateLimiter
agent-memory graph query AuthModule                       # alias-aware lookup
agent-memory graph join AuthModule DEPENDS_ON OWNS        # two-hop traversal
agent-memory graph show | stale | supersede <new> <old>

# Maintenance (local Ollama PROPOSES; the big model DECIDES)
agent-memory maintain                 # LLM audit (propose-only, mutates nothing)
agent-memory maintain --apply-safe    # additive compaction (archive middle + summary)
agent-memory maintain --no-llm        # deterministic budget audit only
agent-memory auto-maintain            # lightweight SessionStart refresh
agent-memory auto-maintain-check --json  # staleness + budget only (fast path)
agent-memory compact                  # enforce line budgets on core files
agent-memory compact --target-ratio 0.8  # proactively resolve 80% budget warnings
agent-memory compact --topics         # also compact topic files
agent-memory archive-topic <slug>     # move whole topic to topics/archive/

agent-memory handoff                  # session handoff summary for activeContext.md
agent-memory coord                    # cross-CLI agent registry status
agent-memory coord --cleanup          # remove stale registry entries

# Diagnostics
agent-memory doctor                   # health check: budgets, broken refs, dead PIDs, index
agent-memory doctor --json            # machine-readable findings (exit 1 on errors)
agent-memory status --json            # bank snapshot for hooks/quota tooling
```

### Environment

| Variable | Default | Effect |
|---|---|---|
| `AGENT_MEMORY_OLLAMA_URL` | `http://localhost:11434` | Daemon URL override |
| `AGENT_MEMORY_EMBED_WORKERS` | `4` | Parallel embed threads for `semindex` (set `1` for serial) |
| `CODEQ_SUMMARY_MODEL` | `hf.co/TeichAI/Qwen3.5-9B-Fable-5-v1-GGUF:Q4_K_M` | Maintain/audit local model (shared with codeq summary) |
| `CODEQ_NO_LLM` / `PROJECT_MEMORY_NO_LLM` | unset | Skip all Ollama calls (deterministic only) |
| `AGENT_MEMORY_CLOUD_FALLBACK` | `1` | `maintain` falls back to the ecosystem `cheap_llm` cascade when Ollama is down (`0` disables) |
| `AGENT_MEMORY_EMBED_READY_TIMEOUT` | `20` | Warm-retry budget (seconds) for the `embed_ready` probe — cold models can exceed the fast 3s probe |
| `MEMORY_ACTIVE_WINDOW_HOURS` | `6.0` | Freshness window for completed-entry archival |
| `MEMORY_STALENESS_DAYS` | `14` | Staleness threshold for `auto-maintain-check` |

### Always-on indexing (Linux / Ubuntu native)

`semwatch` keeps the index hot while agents edit the bank: stdlib mtime polling
(no inotify dependency), debounced, serialized through the same `flock` build
lock as `semindex`. Systemd **user** unit template:
`docs/systemd/agent-memory-semwatch.service`. One-shot installer:

```bash
bash docs/systemd/install-semwatch.sh /path/to/project
# logs: journalctl --user -u 'agent-memory-semwatch@*' -f
```


## Architecture

Vertical-slice layout under `src/agent_memory/features/` — one responsibility
per folder (`bank`, `entries`, `compact`, `search`, `semantic`, `graph`,
`maintain`, `coord`, `doctor`); cross-feature infra in `shared/`. CLI + skills, **not**
MCP: the index spends context only when called.

## Compatibility

`DEFAULT_EMBED_MODEL = "embeddinggemma"` (768-dim) matches the ecosystem the
project was extracted from, so existing indices stay valid. A model sidecar
forces a full re-embed if the default ever changes.

## License

MIT
