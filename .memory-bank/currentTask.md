# Current Task
> Updated: 2026-07-19

## Goal
- Ubuntu-native hardening of agent-memory: embed health, semstatus metadata, hybrid min-score, JSON search, systemd installer.

## Scope
- ollama.embed_ready + doctor ollama-embed; semstatus vector_dim/model/version; hybrid --min-score;
  search/semsearch --json; docs/systemd/install-semwatch.sh; README model default.

## Acceptance Criteria
- tags-up/embed-down surfaces as doctor warn, not false green.
- Hybrid and dense honor --min-score consistently (pure BM25 preserved).
- pytest + ruff + mypy green.

## Status
- [x] 2026-07-19: Shipped; 377 tests, ruff + mypy green.
- [x] 2026-07-19: Autonomous Ubuntu-native + coverage sweep. Added semwatch (debounced
  polling auto-reindex + systemd user unit with %I specifier fix) and semstatus --json;
  raised logic-module coverage to 98-100% across 9 modules (+137 tests, 233->370 total).
  ruff/format/mypy clean. Commits e1779ac, 63aee21, c15ee2e, c428027, a158aeb on main.
