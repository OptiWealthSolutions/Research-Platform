"""Ingestion loop (launchd agent io.optiwealth.research.ingest).

One pass every INGEST_INTERVAL seconds (default 1800). scripts/ingest.py holds
a file lock, so a pass started from the UI or the MCP while this loop runs is
skipped instead of racing on SQLite.
"""
import os
import subprocess
import sys
import time
from datetime import datetime, timezone

INTERVAL = int(os.environ.get("INGEST_INTERVAL", "1800"))
PYTHON_PATH = os.environ.get("PYBIN", sys.executable)
ROOT = os.path.dirname(os.path.abspath(__file__))


def run_worker():
    script_path = os.path.join(ROOT, "scripts", "ingest.py")
    print(f"[*] ingestion loop, every {INTERVAL}s", flush=True)
    while True:
        print(f"\n[{datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S}Z] sync", flush=True)
        try:
            subprocess.run([PYTHON_PATH, script_path], check=True, cwd=ROOT, timeout=3 * 3600)
        except subprocess.CalledProcessError as e:
            print(f"[!] ingestion exited {e.returncode}", flush=True)
        except subprocess.TimeoutExpired:
            print("[!] ingestion pass exceeded 3 h, killed", flush=True)
        time.sleep(INTERVAL)


if __name__ == "__main__":
    run_worker()
