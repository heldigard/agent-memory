# System Patterns

## Format
- [YYYY-MM-DD]: Decision -> Reason -> Alternative considered

## Decisions
- 2026-07-05: Staleness date regex is unanchored (`\b(\d{4}-\d{2}-\d{2})\b` + `re.search`) across ALL staleness paths → Reason: real entries are list-items (`- YYYY-MM-DD | status:...`), not heading lines; an anchored `^\d{4}-...` + `re.match` silently matches zero entries and `check_staleness` reports a clean bank even when entries are years old. Alternative considered: pre-strip leading `- ` then use `re.match` anchored → too fragile if entry format ever varies (e.g. `* ` instead of `- `, indent). The bank/command._report_staleness path already used the unanchored form; auto.py now matches.
- 2026-07-05: Memory-bank staleness and over-budget checks are kept in two distinct paths (auto.py for SessionStart hook, command.py for the `maintain` audit) → Reason: auto.py must be cheap + never block (≤10ms typical, no Ollama), command.py is the big-LLM-assisted audit (may call Ollama, may apply compactions). Alternative considered: unify into one module with a "check-only" flag → rejected: the SessionStart path would inherit the maintain path's heavier imports (ollama_generate + large prompts) and could blow the hook budget.
- 2026-07-05T17:07:33Z | Updated the active context handoff template to point to agent-memory handoff, removing the last legacy reference to project-memory.
- 2026-07-05T22:46:34Z | status:live | agent-memory build_index is serialized via fcntl.flock on .index/.build.lock (auto-releases on exit). Embed concurrency tunable: AGENT_MEMORY_EMBED_WORKERS env (default 4, set 1 for serial). BM25 tokenization memoized (lru_cache 4096) + Counter(d) per doc + argpartition top-k. Ollama cache prune amortized every 50 writes (CACHE_PRUNE_EVERY).
- 2026-07-08: Added global harness integration and sidecar file checks to agent-memory doctor -> Reason: diagnosing shim presence and sidecar mismatches prevents silent harness-wiring degradation across CLIs -> Alternative considered: keeping doctor checks workspace-only (rejected as it missed crucial cross-CLI integration failures).
- 2026-07-08: Robustness improvement in load_index to catch EOFError on truncated/empty npz files -> Reason: empty or corrupted numpy files otherwise crash the doctor/search/recall layers -> Alternative considered: ignore and let it raise (rejected as crashes block the main agent workflow).

