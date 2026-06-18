#!/usr/bin/env bash
# Stop the Macro Research Terminal (backend API + frontend static server).
set -uo pipefail

API_PORT="${API_PORT:-8001}"
WEB_PORT="${WEB_PORT:-3001}"

echo "[*] Stopping Macro Research Terminal..."
for port in "$API_PORT" "$WEB_PORT"; do
  pids="$(lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)"
  if [ -n "$pids" ]; then
    kill $pids 2>/dev/null || true
    echo "    killed :$port ($pids)"
  else
    echo "    :$port already free"
  fi
done
pkill -f "uvicorn backend.main:app" 2>/dev/null || true
pkill -f "http.server ${WEB_PORT}" 2>/dev/null || true
pkill -f "automate_platform.py" 2>/dev/null || true
echo "[=] Stopped."
