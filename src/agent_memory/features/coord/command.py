"""Cross-CLI agent coordination bridge.

Delegates to the standalone ``agent-coordination-status`` binary (the parallel
``heldigard/agent-coordination`` project) for registry inspection and cleanup.
Fails gracefully when that project is not installed on the host — memory ops
never block on coordination tooling.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

COORD_BIN = "agent-coordination-status"


def _run(root: Path, extra: list[str]) -> int:
    """Invoke the coordination binary with ``--project <root>`` + extra args."""
    bin_path = shutil.which(COORD_BIN)
    if not bin_path:
        print(
            f"'{COORD_BIN}' not installed (heldigard/agent-coordination project). "
            "Install it to use cross-CLI registry status/cleanup.",
            file=sys.stderr,
        )
        return 1
    cmd = [bin_path, "--project", str(root), *extra]
    try:
        result = subprocess.run(cmd, check=False)
    except OSError as exc:
        print(f"coord failed to launch: {exc}", file=sys.stderr)
        return 1
    return result.returncode


def coord_status(root: Path) -> int:
    """Print a human-readable coordination registry report for the project."""
    return _run(root, [])


def coord_cleanup(root: Path) -> int:
    """Remove stale coordination entries and compact the registry."""
    return _run(root, ["--cleanup"])
