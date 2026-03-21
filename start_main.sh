#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
export PATH="$HOME/.local/bin:$PATH"

if [[ ! -f ".env" ]]; then
  echo "Error: .env not found in $SCRIPT_DIR" >&2
  exit 1
fi

set -a
source ".env"
set +a

MODEL_NAME_RAW="${MODEL_NAME:-unknown_model}"
MODEL_NAME_SAFE="$(printf '%s' "$MODEL_NAME_RAW" | tr ' /' '__' | tr -cd '[:alnum:]_.-')"
TIMESTAMP="$(date +"%Y%m%d_%H%M%S")"

mkdir -p logs
LOG_FILE="logs/${MODEL_NAME_SAFE}_${TIMESTAMP}.log"

if [[ -f "main.pid" ]]; then
  OLD_PID="$(cat main.pid 2>/dev/null || true)"
  if [[ -n "${OLD_PID}" ]] && kill -0 "${OLD_PID}" 2>/dev/null; then
    echo "Error: main.py is already running (PID: ${OLD_PID})."
    echo "Stop it first: kill ${OLD_PID}"
    exit 1
  fi
fi

nohup python3 main.py >"$LOG_FILE" 2>&1 &
PID=$!
echo "$PID" > main.pid

echo "Started main.py in background."
echo "PID: $PID"
echo "Log: $LOG_FILE"
