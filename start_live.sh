#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
DATA="$ROOT/data"
STREAM="$DATA/hr_stream.jsonl"
LOG="$DATA/hr_corebluetooth.log"

mkdir -p "$DATA"
: > "$LOG"

PY_LISTENER="$ROOT/.venv/bin/python"
if [[ ! -x "$PY_LISTENER" ]]; then
  PY_LISTENER="python3"
fi
pick_tk_python() {
  local candidates=(
    "/opt/homebrew/bin/python3.13"
    "/opt/homebrew/bin/python3.12"
    "/opt/homebrew/bin/python3"
    "python3"
    "/usr/bin/python3"
  )
  for bin in "${candidates[@]}"; do
    if command -v "$bin" >/dev/null 2>&1; then
      if "$bin" - <<'PY' >/dev/null 2>&1; then
import tkinter as tk
import sys
sys.exit(0 if float(tk.TkVersion) >= 8.6 else 1)
PY
        echo "$bin"
        return 0
      fi
    fi
  done
  echo "python3"
}

PY_UI="$(pick_tk_python)"

SOURCE_ARGS=()
if [[ -f "$DATA/hr_sources.json" ]]; then
  SOURCE_ARGS=(--sources-file "$DATA/hr_sources.json" --preferred-grace 6)
fi

"$PY_LISTENER" "$ROOT/ble_hr_corebluetooth.py" \
  --scan-all \
  --max-devices 2 \
  --file "$STREAM" \
  --truncate \
  "${SOURCE_ARGS[@]}" \
  >"$LOG" 2>&1 &

LISTENER_PID=$!
cleanup() {
  kill "$LISTENER_PID" 2>/dev/null || true
}
trap cleanup EXIT

TK_SILENCE_DEPRECATION=1 "$PY_UI" "$ROOT/hr_display.py" \
  --file "$STREAM" \
  --hide-seconds 12 \
  --listener-log "$LOG"
