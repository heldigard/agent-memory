# Current Task
> Updated: 2026-07-12

## Goal
- Harden automatic memory ingestion and decision-graph corruption handling.

## Scope
- Canonical secret redaction for automatic hooks; shared graph JSONL schema validation; doctor and
  documentation parity.

## Acceptance Criteria
- Hooks never persist matched credential values.
- Mixed valid/corrupt graph files degrade gracefully and remain diagnosable.
- Full tests, type/lint/format, package build, architecture guard, and security sensors pass.

## Related
- `docs/plans/2026-07-12-001-fix-memory-ingestion-integrity-plan.md`

## Status
- [x] 2026-07-12: Completed locally; 206 tests pass, 83% coverage, no sensor findings.
- [x] 2026-07-19: Autonomous Ubuntu-native + coverage sweep. Added semwatch (debounced
  polling auto-reindex + systemd user unit with %I specifier fix) and semstatus --json;
  raised logic-module coverage to 98-100% across 9 modules (+137 tests, 233->370 total).
  ruff/format/mypy clean. Commits e1779ac, 63aee21, c15ee2e, c428027, a158aeb on main.
