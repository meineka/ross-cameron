# run_watchdog.ps1 — Phase-12 (ChatGPT-19:05 P0.1) operator entrypoint.
# Resolves a Python with bot dependencies and starts watchdog.py.
#
# Order of resolution:
#   1. $env:BOT_PYTHON (if set and exists) — explicit operator override.
#   2. <repo>\.venv\Scripts\python.exe — preferred local venv.
#   3. <repo>\06_live_bot\.venv\Scripts\python.exe — alt local venv.
#   4. python on PATH (fallback) — watchdog will then preflight and
#      exit cleanly if deps are missing.
#
# Usage:
#   .\06_live_bot\run_watchdog.ps1
#   $env:BOT_PYTHON = "C:\path\to\python.exe"; .\06_live_bot\run_watchdog.ps1

$ErrorActionPreference = "Stop"

$here    = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $here

function Resolve-BotPython {
    if ($env:BOT_PYTHON -and (Test-Path $env:BOT_PYTHON)) {
        return $env:BOT_PYTHON
    }
    $cand = Join-Path $repoRoot ".venv\Scripts\python.exe"
    if (Test-Path $cand) { return $cand }
    $cand = Join-Path $here ".venv\Scripts\python.exe"
    if (Test-Path $cand) { return $cand }
    return "python"
}

$botPy = Resolve-BotPython
Write-Host "[run_watchdog] Using bot-Python: $botPy"
$env:BOT_PYTHON = $botPy
Set-Location $here
& $botPy "watchdog.py"
