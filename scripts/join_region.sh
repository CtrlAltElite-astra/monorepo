#!/usr/bin/env bash
# Usage: scripts/join_region.sh [del]
mkdir -p "$(dirname "$0")/../run"
cd "$(dirname "$0")/.." && exec python scripts/join_region.py "$@"
