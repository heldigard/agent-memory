"""``python -m agent_memory`` entry — delegates to the CLI main."""

from agent_memory.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
