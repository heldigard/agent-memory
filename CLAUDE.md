# Project: agent-memory

Persistent per-project memory (`.memory-bank/`) CLI for LLM coding agents —
semantic recall (BM25 + dense RRF), decision graph, bounded maintenance. Cross-CLI.
See `README.md` for the user-facing overview.

## Commands
- **Install (dev):** `uv venv && . .venv/bin/activate && uv pip install -e ".[test]"`
- **Run CLI:** `python -m agent_memory <command>` (installed binary: `agent-memory`)
- **Test (all):** `pytest` (bounded, fast — pure-logic + mocked Ollama; no daemon needed)
- **Test (one):** `pytest tests/test_hybrid.py -x --tb=short`
- **Coverage:** `pytest --cov=src/agent_memory --cov-report=term-missing`
- **Lint:** `ruff check .`
- **Format check:** `ruff format --check .` (hook auto-formats edited `.py` per-file)
- **Type check:** `mypy src/agent_memory`
- **Vertical-slice gate:** `python3 ~/.claude/hooks/vertical-slice-guard.py`

## Stack
- **Language:** Python ≥ 3.11 (target py311; mypy checked at 3.13)
- **Build:** hatchling (`pyproject.toml`)
- **Runtime dep:** `numpy>=1.26` (only hard dep — vectors + BM25 math)
- **Optional (LLM):** local Ollama (`embeddinggemma` for embeddings, `batiai/gemma4-e4b:q4` for maintain, functiongemma for semantic rerank). Degrades gracefully when down.
- **Test/dev:** pytest, pytest-cov, ruff, mypy

## Entry Points
- **CLI:** `src/agent_memory/cli.py::main` → console script `agent-memory` (pyproject `[project.scripts]`).
- **Module:** `python -m agent_memory` → `src/agent_memory/__main__.py`.
- **Compat shim:** `~/.claude/scripts/project-memory.py` (in the ecosystem, not this repo) delegates to the installed `agent-memory` binary so legacy `project-memory` paths keep working.

## Architecture (vertical-slice)
`src/agent_memory/features/<feature>/command.py` — one responsibility per folder:
- `bank` — init/read/status/add/topic (CRUD over memory-bank files)
- `entries` — parse/format/timestamp/status guards
- `compact` — line-budget enforcement + whole-topic archive
- `search` — keyword (grep-style) search
- `semantic` — `index`/`search`/`hybrid`/`recall`/`chunking`/`status`/`watch` (BM25+dense RRF, local embeddings; `semwatch` = debounced polling auto-reindex, systemd unit in `docs/systemd/`)
- `graph` — `(s,p,o)` triple store + join queries
- `maintain` — LLM-assisted audit (propose-only) + `--apply-safe` additive compaction
- `coord` — bridge to `agent-coordination-status` (sibling project)
- `doctor` — read-only bank, index, coordination, and decision-graph diagnostics
- `hooks/` — Stop/PostToolUse hooks (`decision_tracker`, `recuerda_auto_append`, `budget_guard`) wired into the harness

Cross-feature infra in `shared/` (`paths`, `entries`, `graph`, `ollama`, `text`, `config`, `task_lines`).

CLI + skills, **not** MCP: the index spends context only when called.

## Conventions
- **Types mandatory** — params + returns annotated; no `Any` without justification.
- **ruff rules:** `E F I UP B SIM RUF` (`line-length=100`). Import order enforced.
- **Graceful degradation** — every Ollama call returns `None`/`False` on failure; memory ops never hard-fail on an optional LLM. No bare `except:`; catch specific.
- **Status taxonomy** — `active|wip|blocked|live|completed` protects in-flight work from archival (the "prompt improver" incident).
- **Atomic writes** — index `save_index` uses tmp + `os.replace`; build serialized via `fcntl.flock` on `.index/.build.lock`.
- **Comments WHY not WHAT** — mark non-obvious guards with a one-line reason.

## Key Decisions
- **CLI + skill, not MCP** — index pays context only on demand (vs. always-loaded MCP).
- **Per-project index isolation** — `.memory-bank/.index/` lives in the project, never global; regenerable binary (`gitignore`d).
- **Chunk-level dedup (sha256)** — only changed chunks within a touched file re-embed; coarse `mtime` was the old gate.
- **Vertical-slice layout** — `vs-soft-allow` only when cohesion overrides shape; tests skipped by the guard.

## Things That Look Wrong But Aren't
- `.migration/` carries the pre-extraction monolith + tests (3.4K lines). NOT git-tracked. Kept locally for考古 reference; source of truth is `src/`.
- `--embed halving` (`shared/ollama.py`) drops the 2nd half on embed failure — intentional partial-vector fallback, not a bug (test encodes the contract).
- `currentTask.md` / `activeContext.md` ship as templates with placeholder text — `agent-memory init` writes them only if missing.

## Workflow
- New feature → vertical slice under `features/<name>/`; add `command.py` + register in `cli.py::main`.
- Bug fix → add regression test in matching `tests/test_<area>.py`.
- Before commit → `ruff check . && mypy src/agent_memory && pytest`.
- Semantic-search change → touch `INDEX_VERSION` in `config.py` if the on-disk vector format changes (forces one clean re-embed).
