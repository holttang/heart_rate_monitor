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
PY_UI="python3"

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

"$PY_UI" "$ROOT/hr_display.py" \
  --file "$STREAM" \
  --hide-seconds 12 \
  --listener-log "$LOG"
