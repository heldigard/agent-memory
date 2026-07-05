# Context
> Current state of agent-memory

- Vertical-slice package: 8 features (bank/entries/compact/search/semantic/graph/maintain/coord) + shared infra.
- CLI entry: `agent-memory` (or `project-memory` symlink). Python 3.11+.
- Hybrid search: BM25 + dense RRF via Ollama embeddings (nomic-embed-text, 768-d).
- Graph: triple store (`s,p,o`) with join queries via `agent-memory graph`.
- `maintain`: LLM-assisted memory bank audit (local Ollama proposes, big model decides).
- Tests: 38 tests, ruff+mypy clean. Editable install: `pip install -e ~/agent-memory`.
- Shim contract: `~/.claude/scripts/project-memory.py` (19 lines) delegates to `~/.local/bin/agent-memory`.
