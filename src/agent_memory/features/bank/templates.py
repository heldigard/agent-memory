"""Bootstrap templates for ``agent-memory init``.

Kept in its own module so ``command.py`` stays scannable. Templates are plain
markdown with ``{project}`` / ``{today}`` placeholders filled at init time.
"""

from __future__ import annotations

from datetime import date


def render_templates(project: str) -> dict[str, str]:
    """Return the full set of core memory-bank templates, rendered for the
    given project name and today's date."""
    today = date.today().isoformat()
    return {
        "MEMORY.md": _MEMORY.format(project=project),
        "CONTEXT.md": _CONTEXT.format(today=today),
        "REFERENCE.md": _REFERENCE,
        "agent-sessions.md": _AGENT_SESSIONS,
        "currentTask.md": _CURRENT_TASK.format(today=today),
        "activeContext.md": _ACTIVE_CONTEXT.format(today=today),
        "progress.md": _PROGRESS.format(today=today),
        "systemPatterns.md": _SYSTEM_PATTERNS,
        "dead-ends.md": _DEAD_ENDS,
    }


TOPIC_INDEX_TEMPLATE = (
    "# Topic Index\n"
    "> Deep project memory. Search/read on demand; do not load all topics by default.\n\n"
    "## Topics\n- TBD\n"
)

_MEMORY = """# Memory Index
> Project: {project}

## Read First
- CONTEXT.md: current state
- REFERENCE.md: stable facts
- currentTask.md: active focus
- activeContext.md: recent handoff
- topics/_index.md: deep context map

## Update Rules
- Decision -> systemPatterns.md
- Task done -> progress.md
- Failed approach -> dead-ends.md
- "Recuerda esto" -> activeContext.md or REFERENCE.md
- Deep context -> topics/<slug>.md
"""

_CONTEXT = """# CONTEXT - Current State
> Updated: {today}

## Active Focus
- What are we working on right now?

## Recent Changes
- What changed in the last session?

## Blockers / Risks
- Anything blocking progress?

## Next Steps
- What should happen next?
"""

_REFERENCE = """# REFERENCE - Stable Facts

## Tech Stack
- Language / framework versions
- Key dependencies

## Commands
- How to run tests: `...`
- How to start dev server: `...`
- How to build / deploy: `...`

## Conventions
- Naming patterns
- File organization
- Coding standards

## Links
- Repo URL
- Docs / wiki
- Staging / prod URLs
"""

_AGENT_SESSIONS = """# Agent Sessions
> Auto-generated coordination registry. Do not edit manually.

## Active

## Recently Ended
"""

_CURRENT_TASK = """# Current Task
> Updated: {today}

## Goal
- One-line objective

## Scope
- What is included
- What is NOT included

## Acceptance Criteria
- [ ] Criterion 1
- [ ] Criterion 2

## Related
- Files changed
- PR / issue links
"""

_ACTIVE_CONTEXT = """# Active Context

## {today}
- Memory bank initialized.

## Handoff Format
When ending a session, run `project-memory handoff` and paste the output here.
"""

_PROGRESS = """# Progress

## {today}
- Memory bank initialized.

## Format
- [YYYY-MM-DD]: What was done + verification status
"""

_SYSTEM_PATTERNS = """# System Patterns

## Format
- [YYYY-MM-DD]: Decision -> Reason -> Alternative considered

## Decisions
- None yet.
"""

_DEAD_ENDS = """# Dead-End Log

## Format
- [YYYY-MM-DD]: Approach tried -> Why it failed -> What worked instead

## Failed Approaches
- None yet.
"""
