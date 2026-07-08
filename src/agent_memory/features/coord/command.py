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

from agent_memory.shared.entries import filter_lines_for_injection
from agent_memory.shared.paths import bank_dir

COORD_BIN = "agent-coordination-status"
ORCH_SCRIPT = Path.home() / ".claude" / "scripts" / "cli-orchestration.py"


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
        result = subprocess.run(cmd, check=False, timeout=30, text=True)
    except subprocess.TimeoutExpired:
        print(f"'{COORD_BIN}' timed out after 30s — registry may be locked", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"coord failed to launch: {exc}", file=sys.stderr)
        return 1
    return result.returncode


def coord_status(root: Path) -> int:
    """Print a human-readable coordination registry report for the project."""
    return _run(root, [])


def _local_registry_cleanup(root: Path) -> bool:
    """Best-effort cleanup for the core registry when the coord binary is absent."""
    registry = bank_dir(root) / "agent-sessions.md"
    try:
        before = registry.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return False
    after = filter_lines_for_injection("agent-sessions.md", before)
    if after == before:
        return False
    registry.write_text("\n".join(after) + "\n", encoding="utf-8")
    return True


def coord_cleanup(root: Path) -> int:
    """Remove stale coordination entries and compact lease-broker state."""
    coord_available = shutil.which(COORD_BIN) is not None
    rc = _run(root, ["--cleanup"])
    _local_registry_cleanup(root)
    if ORCH_SCRIPT.exists():
        try:
            broker = subprocess.run(
                [sys.executable, str(ORCH_SCRIPT), "cleanup", "--project", str(root)],
                check=False,
                timeout=30,
                text=True,
                capture_output=True,
            )
        except subprocess.TimeoutExpired:
            print("'cli-orchestration cleanup' timed out after 30s", file=sys.stderr)
            return 1 if rc == 0 else rc
        except OSError as exc:
            print(f"broker cleanup failed to launch: {exc}", file=sys.stderr)
            return 1 if rc == 0 else rc
        if broker.returncode != 0:
            if broker.stderr:
                print(broker.stderr.strip(), file=sys.stderr)
            return broker.returncode if rc == 0 else rc
    if rc != 0 and coord_available:
        return rc
    return 0
