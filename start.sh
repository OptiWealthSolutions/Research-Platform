#!/usr/bin/env bash
# Start the Macro Research Terminal (backend API + frontend static server).
# Cleanly stops anything already on the ports first, then launches both.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYBIN="${PYBIN:-/opt/anaconda3/bin/python3}"
API_PORT="${API_PORT:-8001}"
WEB_PORT="${WEB_PORT:-3001}"

cd "$ROOT"

# Clean slate
API_PORT="$API_PORT" WEB_PORT="$WEB_PORT" bash "$ROOT/kill.sh"
sleep 1

echo "[*] Starting backend API on :${API_PORT}..."
nohup "$PYBIN" -m uvicorn backend.main:app --host 127.0.0.1 --port "$API_PORT" \
  > "$ROOT/backend_log.txt" 2>&1 &
echo "    pid $!"

echo "[*] Starting frontend on :${WEB_PORT}..."
( cd "$ROOT/frontend" && nohup "$PYBIN" -m http.server "$WEB_PORT" --bind 127.0.0.1 \
  > "$ROOT/frontend_log.txt" 2>&1 & echo "    pid $!" )

# Scheduled auto-ingest loop (set SCHED=0 to disable). Default: every 30 min.
# Prefer the launchd agents (scripts/launchd/install.sh): this block is for manual runs.
if [ "${SCHED:-1}" = "1" ]; then
  echo "[*] Starting auto-ingest scheduler (every ${INGEST_INTERVAL:-1800}s)..."
  INGEST_INTERVAL="${INGEST_INTERVAL:-1800}" PYBIN="$PYBIN" \
    nohup "$PYBIN" "$ROOT/automate_platform.py" > "$ROOT/scheduler_log.txt" 2>&1 &
  echo "    pid $!"
fi

sleep 2
echo
echo "[=] Frontend: http://localhost:${WEB_PORT}"
echo "[=] API docs: http://localhost:${API_PORT}/docs"
echo "[=] Stop with: ./kill.sh"
