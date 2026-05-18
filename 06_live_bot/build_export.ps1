# build_export.ps1 -- Phase-73 (2026-05-18)
#
# ChatGPT 20260518_2040 P0 SECURITY: .env was leaking into export zips.
# This script is now the SINGLE source of truth for export builds --
# both the manual operator workflow AND the TICK-EXPORT cron call it
# so the exclude list stays in one place.
#
# What's EXCLUDED (must never leave the repo):
#   .env, .env.*, *.pem, *.key       -- secrets
#   __pycache__, .pytest_cache       -- build artifacts
#   data_pilot/                       -- large backtest data
#   *.pyc                             -- compiled bytecode
#   *.jsonl, *.log, *.out, *.err      -- runtime logs
#   *.pid                             -- process lockfiles
#   trades_live.jsonl, slippage.jsonl -- trading history
#
# Usage:
#   .\build_export.ps1            # writes to 99_Claude_Chatgpt/YYYYMMDD_HHMM_export_claude.zip
#   .\build_export.ps1 -OutDir C:\path\to\custom\dir

param(
    [string]$OutDir = "C:\Users\Szymon\ross-cameron\99_Claude_Chatgpt"
)

$ErrorActionPreference = "Stop"

$root = "C:\Users\Szymon\ross-cameron"
$pkg = "$root\AI_HANDOFF_PACKAGE"
$timestamp = Get-Date -Format "yyyyMMdd_HHmm"
$zip = "$OutDir\${timestamp}_export_claude.zip"

Write-Host "build_export.ps1 -- writing to $zip"

# Step 1: sync source dirs into AI_HANDOFF_PACKAGE with strict excludes
$srcDirs = @("06_live_bot", "tests", "docs")
foreach ($src in $srcDirs) {
    # /MIR: mirror -- delete in dest what isn't in source (keeps pkg in sync)
    # /XF: exclude FILES by name pattern
    # /XD: exclude DIRECTORIES by name
    # Phase-73 SECURITY: .env explicitly excluded
    robocopy "$root\$src" "$pkg\$src" /MIR `
        /XD "__pycache__" ".pytest_cache" "data_pilot" `
        /XF ".env" ".env.local" ".env.bak" ".env.*" `
              "*.pem" "*.key" `
              "*.pyc" "*.jsonl" "*.log" "*.pid" "*.out" "*.err" `
              "bot.pid" "fetch_loop.pid" "supervisor.pid" `
              "trades_live.jsonl" "slippage.jsonl" "alerts.log" `
        /NFL /NDL /NJH /NJS /NC /NS /NP | Out-Null
}

# Step 2: defensive -- explicit delete of .env even if /XF missed it.
# Past leaks happened because robocopy /MIR's exclude semantics can
# leave previously-copied files in dest when sources change shape.
$envFile = "$pkg\06_live_bot\.env"
if (Test-Path $envFile) {
    Remove-Item $envFile -Force
    Write-Host "  defensive: removed $envFile" -ForegroundColor Yellow
}

# Step 3: top-level config files (these are safe -- no secrets)
foreach ($f in "constraints.yaml", "pytest.ini", "requirements.txt", "README.md") {
    if (Test-Path "$root\$f") { Copy-Item "$root\$f" "$pkg\$f" -Force }
}

# Step 4: zip — Phase-73 fix: Compress-Archive's "$pkg\*" wildcard was
# truncating mid-export (saw 8-entry zips). Enumerate top-level items
# explicitly and pass as an array so the cmdlet has unambiguous input.
if (Test-Path $zip) { Remove-Item $zip -Force }
$items = Get-ChildItem -Path $pkg -Force | Where-Object {
    # Drop hidden dotfiles at the package root (only top-level — robocopy
    # already filtered inside the subdirs)
    $_.Name -notmatch '^\.env'
}
Compress-Archive -Path $items.FullName -DestinationPath $zip -CompressionLevel Optimal

# Step 5: final verification -- list any secret-like files that slipped in
Add-Type -AssemblyName System.IO.Compression.FileSystem
$z = [System.IO.Compression.ZipFile]::OpenRead($zip)
$leaks = $z.Entries | Where-Object {
    $_.Name -match "^\.env" -or
    $_.Name -match "\.(pem|key)$" -or
    $_.FullName -match "trades_live\.jsonl|alpaca_api_calls\.jsonl"
}
$z.Dispose()
if ($leaks) {
    Write-Host "" -ForegroundColor Red
    Write-Host "SECURITY LEAK DETECTED -- secrets in zip:" -ForegroundColor Red
    foreach ($l in $leaks) { Write-Host "  $($l.FullName)" -ForegroundColor Red }
    Remove-Item $zip -Force
    throw "Export aborted -- leaked files in zip. Fix excludes and retry."
}

$size = (Get-Item $zip).Length / 1MB
"EXPORTED: $zip ({0:N1} MB) -- verified no secrets" -f $size
