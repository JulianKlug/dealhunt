#!/usr/bin/env bash
# Install dealhunt for the current user.
#
#   deploy/install.sh              venv + all extras + systemd user timer
#   deploy/install.sh --no-timer   everything but the timer (try it by hand first)
#
# Environment:
#   PYTHON   interpreter to build the venv from (default: python3, >= 3.9)
#   EXTRAS   pip extras (default: impersonation,browser)
#
# Idempotent: rerun it after pulling changes.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$ROOT/.venv"
PYTHON="${PYTHON:-python3}"
EXTRAS="${EXTRAS:-impersonation,browser}"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
ENABLE_TIMER=1

[[ "${1:-}" == "--no-timer" ]] && ENABLE_TIMER=0

step() { printf '\n==> %s\n' "$*"; }

# --- 1. an isolated environment ----------------------------------------------
step "venv in $VENV (from $("$PYTHON" --version 2>&1))"
"$PYTHON" -m venv "$VENV"
"$VENV/bin/pip" install --quiet --upgrade pip setuptools
"$VENV/bin/pip" install --quiet --editable "$ROOT[$EXTRAS]"

# Sources that render in JavaScript need a real browser binary.
if [[ ",$EXTRAS," == *",browser,"* ]]; then
    step "headless Chromium for JavaScript-rendered sources"
    "$VENV/bin/python" -m playwright install chromium
fi

# --- 2. a personal config, never overwritten ---------------------------------
if [[ ! -f "$ROOT/dealhunt.toml" ]]; then
    step "creating dealhunt.toml from the example — edit it before enabling alerts"
    cp "$ROOT/dealhunt.example.toml" "$ROOT/dealhunt.toml"
fi
mkdir -p "$ROOT/data"

# --- 3. systemd user units, rendered for this checkout -----------------------
step "systemd user units in $UNIT_DIR"
mkdir -p "$UNIT_DIR"
for template in "$ROOT"/deploy/systemd/*; do
    name="$(basename "$template" .in)"
    sed -e "s|@ROOT@|$ROOT|g" -e "s|@VENV@|$VENV|g" "$template" > "$UNIT_DIR/$name"
done
systemctl --user daemon-reload

if (( ENABLE_TIMER )); then
    systemctl --user enable --now dealhunt.timer
    step "timer enabled — next run:"
    systemctl --user list-timers dealhunt.timer --no-pager | sed -n 2p
else
    step "timer not enabled; start it with: systemctl --user enable --now dealhunt.timer"
fi

cat <<'NEXT'

Next:
  .venv/bin/dealhunt --notify-test      does the alert reach your phone?
  .venv/bin/dealhunt --dry-run          what would it find right now?
NEXT
