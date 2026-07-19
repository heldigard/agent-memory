# Dead-End Log

## Format
- [YYYY-MM-DD]: Approach tried -> Why it failed -> What worked instead

## Failed Approaches
- None yet.
- 2026-07-06T22:58:02Z | status:completed | 2026-07-06: Investigated memory-bank noise source. agent-memory handoff/maintain reads and appends durable memory but did not inject POST-COMPACT RULES or worker wrapper constraints; root cause was smart-trim PreCompact persistence and grounding extraction.
- 2026-07-17: semindex warned 'Ollama unavailable' while /api/tags answered -> partial extraction left /usr/local/lib/ollama with 2 CUDA libs, no llama-server (0.32.1) -> repaired via install.sh; drop-in (User=eldi, OLLAMA_MODELS) survived; 74 chunks re-embedded. Lesson: tags up != inference up; probe /api/embeddings first. Fixed via embed_ready() + doctor ollama-embed check.
