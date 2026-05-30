#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"
export PATH="$HOME/.local/bin:$PATH"
export PYTHONPATH="$PROJECT_ROOT/src${PYTHONPATH:+:$PYTHONPATH}"

if [[ ! -f ".env" ]]; then
  echo "Error: .env not found in $PROJECT_ROOT" >&2
  exit 1
fi

set -a
source ".env"
set +a

MODEL_NAME_RAW="${MODEL_NAME:-unknown_model}"
MODEL_NAME_SAFE="$(printf '%s' "$MODEL_NAME_RAW" | tr ' /' '__' | tr -cd '[:alnum:]_.-')"
TIMESTAMP="$(date +"%Y%m%d_%H%M%S")"

mkdir -p runtime/logs runtime/pids
LOG_FILE="runtime/logs/${MODEL_NAME_SAFE}_${TIMESTAMP}.log"
PID_FILE="runtime/pids/main.pid"

if [[ -f "$PID_FILE" ]]; then
  OLD_PID="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [[ -n "${OLD_PID}" ]] && kill -0 "${OLD_PID}" 2>/dev/null; then
    echo "Error: proof-agent run is already active (PID: ${OLD_PID})."
    echo "Stop it first: kill ${OLD_PID}"
    exit 1
  fi
fi

nohup python3 -m proof_agent.cli.main >"$LOG_FILE" 2>&1 &
PID=$!
echo "$PID" > "$PID_FILE"

echo "Started proof-agent research run in background."
echo "PID: $PID"
echo "Log: $LOG_FILE"
