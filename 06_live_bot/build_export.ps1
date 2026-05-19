# build_export.ps1 -- Phase-77 (2026-05-19)
#
# Phase-73 introduced .env-leak fixes; Phase-77 adds ATOMIC WRITE to fix
# the truncation race ChatGPT reported 4× in a row (20260518_2108/2118/
# 2138/2151): readers saw 6-8 files because Compress-Archive writes
# incrementally and the reader hit the file mid-write.
#
# Atomic-write pattern:
#   1. Compress-Archive into a TEMP path (not in 99_Claude_Chatgpt yet)
#   2. Validate the temp zip has required files (refuse if missing)
#   3. Move-Item to final 99_Claude_Chatgpt/yyyyMMdd_HHmm_export_claude.zip
#   4. ChatGPT/reader only ever sees the COMPLETE, VALIDATED zip
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
# What MUST be present (Phase-77 validation refuses to publish if missing):
#   06_live_bot/bot.py
#   06_live_bot/guarded_alpaca.py
#   tests/conftest.py
#   docs/TEST_MANIFEST.md
#   docs/CHATGPT_OPEN_ITEMS.md
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
$finalZip = "$OutDir\${timestamp}_export_claude.zip"
# Phase-77: write to temp first, rename to finalZip ONLY after validation.
# Use a per-PID temp name so concurrent runs cannot stomp each other.
$tempZip = "$env:TEMP\export_${timestamp}_$PID.zip"

Write-Host "build_export.ps1 (Phase-77 atomic) -- temp=$tempZip"
Write-Host "                                   -- final=$finalZip"

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
              "*.bak" "*.contaminated_bak" "*contaminated*" "*.tmp" `
              "trades_live.jsonl.*" `
        /NFL /NDL /NJH /NJS /NC /NS /NP | Out-Null
}

# Step 2: defensive -- explicit delete of .env even if /XF missed it.
$envFile = "$pkg\06_live_bot\.env"
if (Test-Path $envFile) {
    Remove-Item $envFile -Force
    Write-Host "  defensive: removed $envFile" -ForegroundColor Yellow
}

# Step 3: top-level config files (these are safe -- no secrets)
foreach ($f in "constraints.yaml", "pytest.ini", "requirements.txt", "README.md") {
    if (Test-Path "$root\$f") { Copy-Item "$root\$f" "$pkg\$f" -Force }
}

# Step 4: zip to TEMP (NOT to final destination yet).
# Compress-Archive's array-of-paths form avoids the wildcard-truncation
# bug from Phase-73 (saw 8-entry zips with "$pkg\*").
if (Test-Path $tempZip) { Remove-Item $tempZip -Force }
$items = Get-ChildItem -Path $pkg -Force | Where-Object {
    # Drop hidden dotfiles at the package root (only top-level)
    $_.Name -notmatch '^\.env'
}
Compress-Archive -Path $items.FullName -DestinationPath $tempZip -CompressionLevel Optimal

# Step 5: VALIDATION GATE -- refuse to publish if essential files missing
# or any secret-like content slipped through.
Add-Type -AssemblyName System.IO.Compression.FileSystem
$z = [System.IO.Compression.ZipFile]::OpenRead($tempZip)
# Phase-77.1: Compress-Archive on Windows stores entries with backslashes
# in FullName. Normalize to forward-slash for cross-platform matching.
$names = $z.Entries | ForEach-Object { $_.FullName -replace '\\', '/' }

# 5a: required-file presence check (Phase-77 ChatGPT 4x ask)
$required = @(
    "06_live_bot/bot.py",
    "06_live_bot/guarded_alpaca.py",
    "tests/conftest.py",
    "docs/TEST_MANIFEST.md",
    "docs/CHATGPT_OPEN_ITEMS.md"
)
$missing = @()
foreach ($r in $required) {
    if (-not ($names -contains $r)) { $missing += $r }
}

# 5b: leak detection
$leaks = $z.Entries | Where-Object {
    $_.Name -match "^\.env" -or
    $_.Name -match "\.(pem|key)$" -or
    $_.FullName -match "trades_live\.jsonl" -or
    $_.FullName -match "trades_live\.jsonl\.[a-z]+_bak" -or
    $_.FullName -match "alpaca_api_calls\.jsonl"
}

# 5c: sanity-count check -- a real export has >= 100 entries
$entryCount = $z.Entries.Count
$z.Dispose()

# 5d: enforce all gates
$abort = $false
if ($missing.Count -gt 0) {
    Write-Host "" -ForegroundColor Red
    Write-Host "INCOMPLETE EXPORT -- missing required files:" -ForegroundColor Red
    foreach ($m in $missing) { Write-Host "  $m" -ForegroundColor Red }
    $abort = $true
}
if ($leaks) {
    Write-Host "" -ForegroundColor Red
    Write-Host "SECURITY LEAK DETECTED -- secrets in zip:" -ForegroundColor Red
    foreach ($l in $leaks) { Write-Host "  $($l.FullName)" -ForegroundColor Red }
    $abort = $true
}
if ($entryCount -lt 100) {
    Write-Host "" -ForegroundColor Red
    Write-Host "TRUNCATED EXPORT -- only $entryCount entries (expected >= 100)" -ForegroundColor Red
    $abort = $true
}

if ($abort) {
    Remove-Item $tempZip -Force
    throw "Export aborted -- validation failed. Fix issues and retry."
}

# Step 6: ATOMIC RENAME -- only now does the file appear at the final path.
# Move-Item is atomic on the same volume; readers either see no file or
# the complete validated file -- never a partial.
if (Test-Path $finalZip) { Remove-Item $finalZip -Force }
Move-Item -Path $tempZip -Destination $finalZip -Force

$size = (Get-Item $finalZip).Length / 1MB
"EXPORTED: $finalZip ({0:N1} MB, $entryCount entries) -- atomic, validated, no secrets" -f $size
