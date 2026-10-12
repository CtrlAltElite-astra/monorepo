#!/usr/bin/env bash
# Usage: scripts/run_one.sh pun   (restart proof: kill it, run this again)
mkdir -p "$(dirname "$0")/../run"
cd "$(dirname "$0")/.." && exec python scripts/run_one.py "$@"
