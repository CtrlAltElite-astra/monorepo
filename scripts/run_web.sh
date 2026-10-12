#!/usr/bin/env bash
# Starts the web console on http://localhost:5173 (installs packages the first time).
#   mock mode needs nothing else; add ?mode=live to the URL after  python scripts/run_regions.py mum pun del
cd "$(dirname "$0")/../web" || exit 1
[ -d node_modules ] || npm install
exec npm run dev -- "$@"
