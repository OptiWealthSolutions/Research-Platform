import time
import subprocess
import sys
import os

# Interval in seconds (default hourly; override with INGEST_INTERVAL env).
INTERVAL = int(os.environ.get("INGEST_INTERVAL", "3600"))
PYTHON_PATH = os.environ.get("PYBIN", "/opt/anaconda3/bin/python3")

def run_worker():
    script_path = os.path.join(os.path.dirname(__file__), "scripts", "ingest.py")
    print(f"[*] Starting Automation Loop. Syncing every {INTERVAL} seconds.")
    
    while True:
        print(f"\n[{time.ctime()}] Triggering data synchronization...")
        try:
            # Run the ingestion script using the same python interpreter
            result = subprocess.run([PYTHON_PATH, script_path], check=True)
        except subprocess.CalledProcessError as e:
            print(f"[!] Error during ingestion: {e}")
        
        print(f"[*] Sleeping for {INTERVAL} seconds...")
        time.sleep(INTERVAL)

if __name__ == "__main__":
    run_worker()
