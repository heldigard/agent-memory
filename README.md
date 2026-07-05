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
agent-memory init                     # bootstrap .memory-bank/ in this project
agent-memory status                   # file counts vs budgets + staleness
agent-memory read                     # bounded startup context
agent-memory add --file progress --text "shipped X" --status completed
agent-memory topic --name auth-flow --text "..."
agent-memory search "auth token"
agent-memory semindex                 # build/update the embedding index
agent-memory semrecall                # SessionStart recall from currentTask.md
agent-memory semrecall --query "cross-cli handoff" --min-score 0.35
agent-memory graph add --s AuthModule --p DEPENDS_ON --o RateLimiter
agent-memory graph join AuthModule DEPENDS_ON OWNS    # two-hop traversal
agent-memory maintain                 # local-LLM audit (propose-only)
agent-memory maintain --apply-safe    # additive compaction with summary
agent-memory handoff                  # session handoff summary
```

## Architecture

Vertical-slice layout under `src/agent_memory/features/` — one responsibility
per folder (`bank`, `entries`, `compact`, `search`, `semantic`, `graph`,
`maintain`, `coord`); cross-feature infra in `shared/`. CLI + skills, **not**
MCP: the index spends context only when called.

## Compatibility

`DEFAULT_EMBED_MODEL = "embeddinggemma"` (768-dim) matches the ecosystem the
project was extracted from, so existing indices stay valid. A model sidecar
forces a full re-embed if the default ever changes.

## License

MIT
