# Starts the web console on http://localhost:5173 (installs packages the first time).
#   mock mode needs nothing else; add ?mode=live to the URL after  python scripts/run_regions.py mum pun del
Set-Location (Join-Path $PSScriptRoot '..\web')
if (-not (Test-Path node_modules)) { npm install }
npm run dev -- @args
