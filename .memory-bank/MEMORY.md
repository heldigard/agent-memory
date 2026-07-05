# Memory Index
> Project: agent-memory

- agent-memory — standalone CLI for cross-CLI project memory banks (graduated from `~/.claude/scripts/project-memory.py` monolith, 1727 lines).
- Backward-compat symlink: `~/.local/bin/project-memory` → `~/.local/bin/agent-memory`. 40+ harness refs resolve via shim.
- Shim at `~/.claude/scripts/project-memory.py` delegates to this binary, preserving wired paths in settings.json/hooks.json.
- Deep context in `topics/`; start with `topics/_index.md` or `agent-memory search`.

## Read First
- CONTEXT.md: architecture + feature map
- REFERENCE.md: stable facts, dependencies
- currentTask.md: active focus
- progress.md: completed milestones

## Update Rules
- Decision → systemPatterns.md
- Task done → progress.md
- Failed approach → dead-ends.md
- Deep context → topics/<slug>.md
