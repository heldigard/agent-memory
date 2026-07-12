---
title: "fix: Harden automatic memory ingestion and graph integrity"
type: fix
status: completed
date: 2026-07-12
---

# fix: Harden automatic memory ingestion and graph integrity

## Overview

Review and harden the two boundaries that can currently bypass the project's stated
durability and safety guarantees: automatic hook writes into `.memory-bank/` and loading
hand-edited or partially corrupted decision-graph JSONL. Keep the change dependency-free,
backward compatible, and fail-safe.

## Enhancement summary

**Deepened on:** 2026-07-12  
**Sections enhanced:** safety policy, graph schema, diagnostics, testing, repository hygiene  
**Research agents used:** none; controller-only local analysis was sufficient for this bounded
Python CLI and avoids coordination overhead.

### Key improvements

1. Redact through the existing canonical detector before any hook truncation or persistence.
2. Put graph parsing in a shared pure module that returns both valid rows and structured issues;
   runtime commands and doctor will consume the same result.
3. Separate corrupt-record handling from duplicate/dangling relationship checks so doctor output
   remains actionable and runtime reads preserve all healthy rows.

### New considerations discovered

- Redaction must replace every match, not merely reject the whole automatically captured note;
  silently dropping a note makes the hook unreliable and storing only `[REDACTED]` may produce a
  too-short entry that should still be discarded.
- Graph ID allocation must consider any valid row with a valid ID, even if optional metadata was
  normalized, so adding a fact never reuses an existing identifier.
- A schema issue needs a bounded line number/reason for diagnostics but must never include the raw
  row, because a corrupted row itself may contain sensitive content.

## Baseline and research

- No matching recent brainstorm or `docs/solutions/` guidance exists.
- The repository is a Python 3.11+ vertical-slice CLI with one hard dependency (`numpy`).
- Baseline validation passes: 195 tests, 83% statement coverage, Ruff and Mypy clean.
- `src/agent_memory/shared/config.py` owns the canonical `SECRET_RE`, and
  `src/agent_memory/shared/text.py::ensure_safe_text` protects explicit CLI writes.
- `src/agent_memory/hooks/recuerda_auto_append.py` writes prompt-derived content directly
  and therefore can persist credential-shaped material.
- `src/agent_memory/hooks/decision_tracker.py` has a second, weaker secret pattern, creating
  policy drift and missing formats already handled by the canonical guard.
- `src/agent_memory/features/graph/command.py::_graph_load` catches invalid JSON only. Valid
  JSON scalars/lists and wrong metadata types can later raise `AttributeError`/`TypeError`.
- The newly added doctor graph check reports syntax errors, duplicate IDs, and dangling
  supersedes, but does not yet validate the record schema used by runtime graph operations.
- `CHANGELOG.md` has two Unreleased headings and does not record the latest graph-integrity
  work; `.gitignore` repeats already-covered coordination paths.

External research is intentionally skipped: these are local data-safety and schema-validation
defects with established project patterns and no unstable external API or framework behavior.

## Problem statement

The CLI promises that memory operations do not store secrets and that malformed durable state
degrades gracefully. Those promises are not consistently enforced at automatic ingestion or
graph-load boundaries. A remembered prompt may leak a credential into a tracked markdown bank,
and a syntactically valid but structurally invalid graph line may crash query/show/stale/add
operations. Duplicate policy logic also makes future fixes likely to diverge.

## Proposed solution

1. Add one shared redaction helper backed by the canonical secret pattern. Broaden that pattern
   only for unambiguous assignment/header forms that should never enter memory.
2. Route prompt-derived hook content through shared redaction before it is truncated or written;
   remove the decision tracker's local secret regex.
3. Define the minimum graph-row schema and normalize/skip structurally invalid rows at load time.
   Reuse the same validation in doctor so runtime behavior and diagnostics cannot drift.
4. Add focused regression tests for every failure mode and clean the changelog/gitignore drift.

## Technical approach

### Phase 1: Safe automatic ingestion

- Add `redact_secrets(text)` beside `ensure_safe_text`.
- Keep rejection semantics for explicit CLI writes; use redaction for hooks so useful context is
  retained without credential values.
- Ensure both hooks import the shared helper and never maintain their own policy regex.
- Test direct helper behavior, remembered-prompt persistence, and decision extraction.

#### Implementation detail

- Extend the canonical detector only with generic `token` and `secret` assignment/header keys;
  harmless prose such as “token refresh behavior” remains unmatched because it has no separator
  and value.
- Implement `redact_secrets(text: str) -> str` as a total, side-effect-free substitution using the
  canonical compiled expression and a stable `[REDACTED]` marker.
- In `recuerda_auto_append`, strip triggers, normalize whitespace, redact, then enforce the minimum
  and maximum lengths. This ordering prevents a long value from being partially preserved by the
  length cap.
- In `decision_tracker`, redact before `_trim`; deleting its local `SECRET_RE` removes the policy
  fork. Keep extraction semantics otherwise unchanged.
- Do not print rejected/redacted source material to stderr.

### Phase 2: Tolerant graph schema handling

- Introduce a small internal validator/normalizer for JSON graph records.
- Require non-empty string `id`, `s`, `p`, and `o`; accept optional string metadata and lists of
  strings only. Skip invalid records with a bounded warning rather than crashing.
- Make doctor count and report structurally invalid rows separately from JSON syntax errors while
  preserving duplicate-ID and dangling-supersedes checks for valid rows.
- Exercise scalar JSON, missing fields, bad aliases/supersedes types, and healthy round trips.

#### Implementation detail

- Add a dependency-free `shared/graph.py` with typed records for parse issues and a function shaped
  like `parse_graph_lines(lines) -> (rows, issues)`.
- A valid core record requires non-empty string values for `id`, `s`, `p`, and `o`; otherwise it is
  skipped. Invalid optional `t`/`src` values are removed, while bad `aliases`/`supersedes` values
  normalize to filtered string lists, preserving the fact and its identifier.
- Preserve unknown keys so forward-compatible metadata is not discarded during
  `graph supersede` rewrites.
- Classify issues as `json` or `schema`, retain only the 1-based line number and a bounded reason,
  and never retain/emit the raw line.
- `_graph_load` prints concise warnings for issues and returns healthy/normalized rows. Doctor
  aggregates the counts into one warning per issue category, then performs duplicate and dangling
  checks only on those usable rows.
- Treat a repeated `supersedes` value within one row as harmless set semantics; duplicate fact IDs
  across rows remain an error.

### Phase 3: Documentation and repository hygiene

- Merge duplicate Unreleased changelog sections and describe the current fixes.
- Remove redundant exact ignore rules already covered by wildcards, preserving runtime-noise
  exclusions.

#### Scope boundary

- Do not release, tag, push, or rewrite the three existing local commits.
- Do not convert all JSONL or markdown appends to a new locking protocol.
- Do not refactor the broader semantic-command test surface solely to raise aggregate coverage.
- Do not repair invalid graph rows automatically; doctor remains read-only and reports remediation.

## System-wide impact

- **Interaction graph:** UserPromptSubmit/Stop payload -> hook parsing -> shared redaction ->
  append-only markdown write -> future startup read/search. Graph command -> `_graph_load` ->
  query/join/show/stale/add; doctor reads the same durable JSONL contract.
- **Error propagation:** Hook parsing and I/O remain best-effort and exit zero. Graph corruption is
  surfaced on stderr and by doctor findings, while valid records remain usable.
- **State lifecycle:** No migration or destructive rewrite is required. Invalid historical graph
  rows remain on disk for repair but are ignored by runtime reads.
- **API parity:** Explicit `add`/`topic`/`graph add` continue to reject secrets; both automatic hooks
  gain equivalent safety through redaction. All graph read interfaces share `_graph_load`.
- **Compatibility:** No CLI flags, output schemas, dependencies, or on-disk valid-record formats
  change.

## SpecFlow edge cases

- A note contains both useful prose and more than one credential-shaped fragment.
- A credential appears before truncation; redaction must happen first so truncation cannot retain a
  partial value.
- A decision contains a bare token value pattern rather than a named assignment.
- A graph line is valid JSON but is `null`, a list, or a number.
- A graph object omits required fields or uses an empty/non-string required field.
- `aliases` or `supersedes` is a string/dict rather than a list of strings.
- A file mixes invalid rows with valid rows; valid facts must still be queryable and IDs must remain
  collision-safe.
- Doctor JSON output must remain machine-readable and severity sorting must remain stable.

## Acceptance criteria

- [x] No automatic hook persists credential values matched by the canonical memory safety policy.
- [x] Explicit writes continue to reject unsafe input, while hook writes retain redacted context.
- [x] There is one secret-pattern source of truth and one shared redaction implementation.
- [x] Every graph command tolerates structurally invalid JSONL records without a traceback.
- [x] Doctor reports syntax/schema corruption, duplicate IDs, and dangling supersedes accurately.
- [x] Existing valid graph files and CLI behavior remain compatible.
- [x] Focused regressions cover the new boundaries.
- [x] Full Pytest, Ruff, Mypy, format, vertical-slice, and appropriate codescan sensors pass.
- [x] The final diff contains no unrelated mutation of the pre-existing memory-session line.

## Risks and mitigation

- **Over-redaction:** Restrict detection to credential-shaped assignments, headers, and known token
  forms; retain tests for harmless operational vocabulary.
- **Schema strictness:** Validate only fields runtime logic depends on; preserve extra metadata.
- **Warning noise:** Emit one bounded warning per skipped runtime row and aggregate counts in doctor.
- **Concurrent state:** Do not expand into a locking redesign; existing append/atomic rewrite behavior
  remains unchanged.

## Validation plan

1. Run focused hook, shared-text, graph, and doctor tests.
2. Run the full suite with coverage, Ruff check/format, and Mypy.
3. Run the vertical-slice guard and the narrowest useful codescan sensors for security/dead code.
4. Review both the working-tree diff and the diff against `origin/main`, separating prior commits.
5. Run `agent-memory doctor --json` against this project and update project memory with durable
   findings only.

### Targeted test matrix

- `shared.text`: multiple matches are redacted; named token/secret assignments are rejected by
  explicit writes; harmless conceptual text still passes.
- `recuerda_auto_append`: a mixed useful note and synthetic credential persists useful text plus the
  marker, never the value; a note reduced below the minimum is skipped.
- `decision_tracker`: explicit and verb-derived decisions use the shared marker for formats the old
  local regex missed.
- `shared.graph`: valid record preservation, JSON scalar/list/null, missing/empty required fields,
  optional scalar types, and non-string list members.
- `graph`: mixed good/bad JSONL remains queryable and `graph add` allocates the next unused ID.
- `doctor`: separate aggregated syntax/schema findings plus existing duplicate and dangling checks;
  JSON mode remains parseable.

### Ship criteria

- No new package dependency or CLI compatibility break.
- No raw synthetic credential literal likely to trip gitleaks is added to fixtures; construct values
  from safe parts in tests.
- Coverage does not regress below the 83% baseline, and every new shared branch is exercised.
- The pre-existing `.memory-bank/progress.md` session line remains byte-for-byte present.

## Post-deploy monitoring and validation

- **Validation command:** run `agent-memory doctor --json` on representative banks and confirm no
  new `check=graph` findings for healthy files.
- **Expected healthy behavior:** remember/decision hooks retain useful prose with `[REDACTED]` in
  place of credential-shaped values; graph queries return healthy facts even beside corrupt rows.
- **Failure signals:** raw credential values in `.memory-bank/`, hook tracebacks, graph query
  tracebacks, or unexpected schema warnings for records written by `graph add`.
- **Mitigation:** disable the affected hook or revert the local change; graph diagnostics are
  read-only and invalid rows remain available for manual repair.
- **Window/owner:** validate during the next normal agent session; project maintainer owns follow-up.

## Internal references

- `src/agent_memory/shared/config.py:66`
- `src/agent_memory/shared/text.py:14`
- `src/agent_memory/hooks/recuerda_auto_append.py:45`
- `src/agent_memory/hooks/decision_tracker.py:36`
- `src/agent_memory/features/graph/command.py:25`
- `src/agent_memory/features/doctor/command.py:325`
- `tests/test_text.py:17`
- `tests/test_recuerda_auto_append.py:32`
- `tests/test_decision_tracker.py:32`
- `tests/test_graph.py:17`
- `tests/test_doctor.py:146`
