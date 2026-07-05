# REFERENCE — Stable Facts

## Tech Stack
- **Language:** Python ≥ 3.11 (target py311; mypy checked at 3.13)
- **Build:** hatchling (`pyproject.toml`); pkg name `agent-memory-cli`, script `agent-memory`
- **Hard dep:** `numpy>=1.26` (vectors + BM25 math)
- **Optional (LLM):** local Ollama — `embeddinggemma` (768-d embeds), `qwen3.5:4b` (rerank/maintain). Degrades gracefully.
- **Test/dev:** pytest, pytest-cov, ruff (rules `E F I UP B SIM RUF`, line=100), mypy

## Commands
- **Install (dev):** `uv venv && . .venv/bin/activate && uv pip install -e ".[test]"`
- **Run:** `python -m agent_memory <cmd>` or installed `agent-memory`
- **Test (all):** `pytest`
- **Test (one):** `pytest tests/test_hybrid.py -x --tb=short`
- **Coverage:** `pytest --cov=src/agent_memory --cov-report=term-missing`
- **Lint:** `ruff check .`
- **Type:** `mypy src/agent_memory`
- **VS gate:** `python3 ~/.claude/hooks/vertical-slice-guard.py`

## Key Env Vars
| Var | Default | Effect |
|---|---|---|
| `AGENT_MEMORY_OLLAMA_URL` | `http://localhost:11434` | Daemon URL |
| `AGENT_MEMORY_EMBED_WORKERS` | `4` | Parallel embed threads (`1`=serial) |
| `CODEQ_SUMMARY_MODEL` | `batiai/gemma4-e4b:q4` | Maintain/audit model |
| `CODEQ_NO_LLM` / `PROJECT_MEMORY_NO_LLM` | unset | Skip all Ollama (deterministic only) |
| `MEMORY_ACTIVE_WINDOW_HOURS` | `6.0` | Completed-entry archival freshness |
| `MEMORY_STALENESS_DAYS` | `14` | Staleness threshold |

## Conventions
- Vertical-slice: one responsibility per `features/<feature>/command.py`; cross-feature in `shared/`.
- Types mandatory; no `Any` without justification; no bare `except`.
- Atomic writes (tmp + `os.replace`); build serialized (`fcntl.flock`).
- Status taxonomy `active|wip|blocked|live|completed` protects in-flight work from archival.
- CLI + skills, **not** MCP (index pays context only on demand).

## Links
- Repo: https://github.com/heldigard/agent-memory
- Sibling: `heldigard/agent-coordination` (`agent-coordination-status` binary; `coord` command bridges to it)
- License: MIT
