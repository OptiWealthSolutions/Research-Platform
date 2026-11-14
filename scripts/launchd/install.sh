#!/usr/bin/env bash
# Install / reload the three launchd agents of the Research Platform:
#   io.optiwealth.research.api     uvicorn backend.main:app on 127.0.0.1:8001
#   io.optiwealth.research.web     static UI on 127.0.0.1:3001
#   io.optiwealth.research.ingest  ingestion loop (automate_platform.py, 30 min)
# Uninstall: scripts/launchd/install.sh --remove
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PYBIN="${PYBIN:-/opt/anaconda3/bin/python3}"
LA="$HOME/Library/LaunchAgents"
mkdir -p "$LA" "$ROOT/logs"

plist() {  # label, workdir, log, args...
  local label="$1" wd="$2" log="$3"; shift 3
  local args=""; for a in "$@"; do args+="    <string>${a}</string>"$'\n'; done
  cat > "$LA/$label.plist" <<P
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>${label}</string>
  <key>ProgramArguments</key>
  <array>
${args}  </array>
  <key>WorkingDirectory</key><string>${wd}</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PYBIN</key><string>${PYBIN}</string>
    <key>INGEST_INTERVAL</key><string>1800</string>
    <key>PATH</key><string>/opt/anaconda3/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>15</integer>
  <key>StandardOutPath</key><string>${log}</string>
  <key>StandardErrorPath</key><string>${log}</string>
</dict>
</plist>
P
}

labels=(io.optiwealth.research.api io.optiwealth.research.web io.optiwealth.research.ingest)
for l in "${labels[@]}"; do launchctl bootout "gui/$(id -u)/$l" 2>/dev/null || true; done
if [ "${1:-}" = "--remove" ]; then
  for l in "${labels[@]}"; do rm -f "$LA/$l.plist"; done
  echo "[=] removed"; exit 0
fi

plist io.optiwealth.research.api "$ROOT" "$ROOT/logs/api.log" \
  "$PYBIN" -m uvicorn backend.main:app --host 127.0.0.1 --port 8001 --no-server-header
plist io.optiwealth.research.web "$ROOT/frontend" "$ROOT/logs/web.log" \
  "$PYBIN" -m http.server 3001 --bind 127.0.0.1
plist io.optiwealth.research.ingest "$ROOT" "$ROOT/logs/ingest.log" \
  "$PYBIN" "$ROOT/automate_platform.py"

for l in "${labels[@]}"; do launchctl bootstrap "gui/$(id -u)" "$LA/$l.plist"; done
echo "[=] loaded: ${labels[*]}"
echo "    API  http://127.0.0.1:8001/docs   UI http://127.0.0.1:3001   logs $ROOT/logs/"
