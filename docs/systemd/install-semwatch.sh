#!/usr/bin/env bash
# Install (or update) a per-project agent-memory semwatch user unit.
#
# Usage:
#   bash docs/systemd/install-semwatch.sh [/path/to/project]
#   bash docs/systemd/install-semwatch.sh            # defaults to $PWD
#
# Requires: systemd --user. Prefers <project>/.venv/bin/agent-memory, then PATH.
set -euo pipefail

ROOT="$(cd "${1:-.}" && pwd)"
if [[ ! -d "$ROOT/.memory-bank" ]]; then
    echo "error: no .memory-bank at $ROOT — run: agent-memory --root \"$ROOT\" init" >&2
    exit 1
fi
if ! command -v systemctl >/dev/null 2>&1; then
    echo "error: systemctl not found (need systemd user session)" >&2
    exit 1
fi

# Prefer the project's editable venv (always current during development), then PATH.
AM_BIN=""
if [[ -x "$ROOT/.venv/bin/agent-memory" ]]; then
    AM_BIN="$ROOT/.venv/bin/agent-memory"
elif command -v agent-memory >/dev/null 2>&1; then
    AM_BIN="$(command -v agent-memory)"
    if command -v readlink >/dev/null 2>&1; then
        AM_BIN="$(readlink -f "$AM_BIN" 2>/dev/null || echo "$AM_BIN")"
    fi
else
    echo "error: agent-memory not found (install: uv tool install -e .  or  uv pip install -e .)" >&2
    exit 1
fi

# Refuse stale binaries that predate the semwatch subcommand.
if ! "$AM_BIN" semwatch --help >/dev/null 2>&1; then
    echo "error: $AM_BIN has no 'semwatch' (stale install). Reinstall from this repo:" >&2
    echo "  cd $ROOT && uv tool install -e . --force" >&2
    exit 1
fi

UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
UNIT_SRC="$(cd "$(dirname "$0")" && pwd)/agent-memory-semwatch.service"
UNIT_DST="$UNIT_DIR/agent-memory-semwatch@.service"
INSTANCE="$(systemd-escape "$ROOT")"
DROPIN_DIR="$UNIT_DIR/agent-memory-semwatch@${INSTANCE}.service.d"

mkdir -p "$UNIT_DIR" "$DROPIN_DIR"
cp "$UNIT_SRC" "$UNIT_DST"

# Absolute ExecStart: user units often lack ~/.local/bin even with Environment=PATH.
cat >"$DROPIN_DIR/override.conf" <<EOF
[Service]
# Written by install-semwatch.sh — absolute path so PATH cannot hide the binary.
ExecStart=
ExecStart=${AM_BIN} --root %I semwatch --interval 2 --debounce 1
EOF

systemctl --user daemon-reload
systemctl --user enable --now "agent-memory-semwatch@${INSTANCE}.service"

# Brief settle so auto-restart failures surface here instead of only in journal.
sleep 2
STATE="$(systemctl --user is-active "agent-memory-semwatch@${INSTANCE}.service" || true)"
if [[ "$STATE" != "active" ]]; then
    echo "warning: unit state is '$STATE' (expected active). Last log lines:" >&2
    journalctl --user -u "agent-memory-semwatch@${INSTANCE}.service" -n 12 --no-pager >&2 || true
    exit 1
fi

echo "installed: agent-memory-semwatch@${INSTANCE}.service ($STATE)"
echo "  root:    $ROOT"
echo "  binary:  $AM_BIN"
echo "  logs:    journalctl --user -u agent-memory-semwatch@${INSTANCE} -f"
echo "  stop:    systemctl --user disable --now 'agent-memory-semwatch@${INSTANCE}.service'"
