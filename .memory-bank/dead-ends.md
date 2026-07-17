# Dead-End Log

## Format
- [YYYY-MM-DD]: Approach tried -> Why it failed -> What worked instead

## Failed Approaches
- None yet.
- 2026-07-06T22:58:02Z | status:completed | 2026-07-06: Investigated memory-bank noise source. agent-memory handoff/maintain reads and appends durable memory but did not inject POST-COMPACT RULES or worker wrapper constraints; root cause was smart-trim PreCompact persistence and grounding extraction.
- 2026-07-17: `semindex` warned "Ollama unavailable" while daemon answered `/api/tags` → partial extraction left `/usr/local/lib/ollama` with 2 CUDA libs and no `llama-server` binary (0.32.1) → repaired via official install script (`curl -fsSL https://ollama.com/install.sh`); drop-in override (`User=eldi`, `OLLAMA_MODELS`) survived; 74 chunks re-embedded. Lesson: tags endpoint up ≠ inference up; always probe `/api/embed` before trusting "daemon up".
