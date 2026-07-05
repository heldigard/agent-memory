"""Harness hooks shipped with agent-memory.

These run as standalone scripts invoked by the Claude Code (or sibling CLI)
hook system. Each module exposes a ``main()`` returning an exit code; the
on-disk hook file under ``~/.claude/hooks/`` is a thin shim that imports it.
"""
