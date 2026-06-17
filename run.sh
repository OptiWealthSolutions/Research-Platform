#!/usr/bin/env bash
# Clean restart of the Macro Research Terminal (backend API + frontend static).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYBIN="${PYBIN:-/opt/anaconda3/bin/python3}"
API_PORT="${API_PORT:-8000}"
WEB_PORT="${WEB_PORT:-3000}"

cd "$ROOT"

echo "[*] Stopping anything on ports ${API_PORT} / ${WEB_PORT}..."
for port in "$API_PORT" "$WEB_PORT"; do
  pids="$(lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)"
  [ -n "$pids" ] && kill $pids 2>/dev/null || true
done
pkill -f "uvicorn backend.main:app" 2>/dev/null || true
pkill -f "http.server ${WEB_PORT}" 2>/dev/null || true
sleep 1

echo "[*] Starting backend API on :${API_PORT}..."
nohup "$PYBIN" -m uvicorn backend.main:app --host 0.0.0.0 --port "$API_PORT" \
  > "$ROOT/backend_log.txt" 2>&1 &
echo "    pid $!"

echo "[*] Starting frontend on :${WEB_PORT}..."
( cd "$ROOT/frontend" && nohup "$PYBIN" -m http.server "$WEB_PORT" \
  > "$ROOT/frontend_log.txt" 2>&1 & echo "    pid $!" )

sleep 2
echo
echo "[=] Backend : http://localhost:${API_PORT}/docs"
echo "[=] Frontend: http://localhost:${WEB_PORT}"
