"""Backward-compatible shim. Canonical logic now lives in scripts/ingest.py."""
import sys
import os

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from scripts.ingest import run  # noqa: E402

if __name__ == "__main__":
    run(do_analyze=True, days=7)
