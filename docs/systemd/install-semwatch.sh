#!/usr/bin/env bash
# Install (or update) a per-project agent-memory semwatch user unit.
#
# Usage:
#   bash docs/systemd/install-semwatch.sh [/path/to/project]
#   bash docs/systemd/install-semwatch.sh            # defaults to $PWD
#
# Requires: systemd --user, agent-memory on PATH.
set -euo pipefail

ROOT="$(cd "${1:-.}" && pwd)"
if [[ ! -d "$ROOT/.memory-bank" ]]; then
    echo "error: no .memory-bank at $ROOT — run: agent-memory --root \"$ROOT\" init" >&2
    exit 1
fi
if ! command -v agent-memory >/dev/null 2>&1; then
    echo "error: agent-memory not on PATH" >&2
    exit 1
fi
if ! command -v systemctl >/dev/null 2>&1; then
    echo "error: systemctl not found (need systemd user session)" >&2
    exit 1
fi

UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
UNIT_SRC="$(cd "$(dirname "$0")" && pwd)/agent-memory-semwatch.service"
UNIT_DST="$UNIT_DIR/agent-memory-semwatch@.service"
INSTANCE="$(systemd-escape "$ROOT")"

mkdir -p "$UNIT_DIR"
cp "$UNIT_SRC" "$UNIT_DST"
systemctl --user daemon-reload
systemctl --user enable --now "agent-memory-semwatch@${INSTANCE}.service"

echo "installed: agent-memory-semwatch@${INSTANCE}.service"
echo "  root:    $ROOT"
echo "  logs:    journalctl --user -u agent-memory-semwatch@${INSTANCE} -f"
echo "  stop:    systemctl --user disable --now 'agent-memory-semwatch@${INSTANCE}.service'"
