# healthcheck.ps1 — Phase-74 (2026-05-18)
#
# User: "jede halbe stunde die existenz des live trading bots prüfen
# und bei Fehlern wiederherstellen, alles im powershell"
#
# Runs every 30 min (via Windows Scheduled Task). Native PowerShell so
# it doesn't depend on a Claude session being open or Python supervisor
# being alive.
#
# What it checks + repairs:
#   1. bot.py --daemon alive?  -> if dead, kill any stale lockfile +
#      spawn watchdog (which respawns bot).
#   2. watchdog.py alive?       -> if dead, spawn it.
#   3. fetch_loop.py alive?     -> if dead, kill stale lockfile + spawn.
#   4. supervisor.py alive?     -> if dead, spawn (auto-correct watcher).
#   5. daemon.log mtime < 30min? -> warn if older (bot hung).
#
# Differences vs supervisor.py (Phase-67):
#   - Native PowerShell, no Python dependency
#   - Runs from Windows Task Scheduler -> survives reboots
#   - Simpler / less feature-rich (no JSONL, no debounce push)
#   - Last-resort backstop: if Python supervisor itself died, this
#     ps1 still runs and brings it back.
#
# Output: appends 1 line per check to 06_live_bot\healthcheck.log
# Exit: 0 always (so scheduled task doesn't show "failed")

param(
    [switch]$DryRun  # show what WOULD be done without spawning
)

$ErrorActionPreference = "Continue"
$root = "C:\Users\Szymon\ross-cameron"
$bot  = "$root\06_live_bot"
$venv = "$root\.venv\Scripts\python.exe"
$logFile = "$bot\healthcheck.log"

function Write-Log {
    param([string]$msg, [string]$level = "INFO")
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "$ts [$level] $msg"
    Add-Content -Path $logFile -Value $line -Encoding utf8
    Write-Host $line
}

function Get-DaemonProc {
    param([string]$pattern)
    # Only "real interpreter" processes — skip venv launcher PIDs to
    # avoid double-counting the launcher+interpreter pair.
    Get-WmiObject Win32_Process -Filter "Name='python.exe'" |
        Where-Object {
            $_.CommandLine -match $pattern -and
            -not ($_.CommandLine -like '*\.venv\*')
        }
}

function Test-Lockfile {
    param([string]$file)
    $path = "$bot\$file"
    if (-not (Test-Path $path)) { return $null }
    $pidStr = (Get-Content $path -ErrorAction SilentlyContinue).Trim()
    if ($pidStr -match '^\d+$') {
        $procPid = [int]$pidStr
        $proc = Get-Process -Id $procPid -ErrorAction SilentlyContinue
        if ($null -ne $proc) { return @{ alive = $true; pid = $procPid } }
        return @{ alive = $false; pid = $procPid }
    }
    return $null
}

function Remove-StaleLockfile {
    param([string]$file, [string]$role)
    $status = Test-Lockfile -file $file
    if ($null -ne $status -and -not $status.alive) {
        Write-Log "$role lockfile $file points to dead PID $($status.pid) -- removing" "WARN"
        if (-not $DryRun) {
            Remove-Item "$bot\$file" -Force -ErrorAction SilentlyContinue
        }
        return $true
    }
    return $false
}

function Start-Daemon {
    param(
        [string]$role,
        [string]$script,
        [string]$logRedirect,
        [string[]]$args = @()
    )
    Write-Log "Starting $role -- $script $($args -join ' ')" "INFO"
    if ($DryRun) { return $null }

    # Carry over operator env so strategy variant + skip_hard_flat
    # survive auto-spawns just like manual restarts do.
    $envFile = "$bot\.env"
    if (Test-Path $envFile) {
        Get-Content $envFile -Encoding utf8 | ForEach-Object {
            if ($_ -match '^([A-Z_][A-Z0-9_]*)=(.+)$') {
                $envName = $Matches[1]
                $envVal = $Matches[2].Trim('"').Trim("'")
                # SECURITY: don't override APCA keys if shell already has them
                if (-not [Environment]::GetEnvironmentVariable($envName, 'Process')) {
                    [Environment]::SetEnvironmentVariable($envName, $envVal, 'Process')
                }
            }
        }
    }

    $procArgs = @($script) + $args
    if ($logRedirect) {
        $proc = Start-Process -FilePath $venv -ArgumentList $procArgs `
            -WorkingDirectory $root -WindowStyle Hidden -PassThru `
            -RedirectStandardOutput $logRedirect `
            -RedirectStandardError "$logRedirect.err"
    } else {
        $proc = Start-Process -FilePath $venv -ArgumentList $procArgs `
            -WorkingDirectory $root -WindowStyle Hidden -PassThru
    }
    Write-Log "  spawned $role PID=$($proc.Id)" "INFO"
    return $proc.Id
}

# ─── Run cycle ──────────────────────────────────────────────────────────

Write-Log "=== healthcheck cycle start (DryRun=$DryRun) ==="

# 1. bot.py --daemon
$botProc = Get-DaemonProc -pattern 'bot\.py.*--daemon'
if ($null -eq $botProc) {
    Write-Log "bot.py NOT running" "WARN"
    Remove-StaleLockfile -file "bot.pid" -role "bot" | Out-Null
    # bot.py is started by watchdog -- not directly here. Watchdog
    # will respawn it within its own 5-min cycle once we ensure
    # watchdog is alive (next check).
} else {
    Write-Log "bot.py alive PID=$($botProc.ProcessId)" "OK"
}

# 2. watchdog.py
$wdProc = Get-DaemonProc -pattern 'watchdog\.py'
if ($null -eq $wdProc) {
    Write-Log "watchdog.py NOT running -- spawning" "WARN"
    Start-Daemon -role "watchdog" -script "06_live_bot\watchdog.py" `
        -logRedirect "$bot\watchdog.out" | Out-Null
} else {
    Write-Log "watchdog.py alive PID=$($wdProc.ProcessId)" "OK"
}

# 3. fetch_loop.py
$flProc = Get-DaemonProc -pattern 'fetch_loop\.py'
if ($null -eq $flProc) {
    Write-Log "fetch_loop.py NOT running" "WARN"
    Remove-StaleLockfile -file "fetch_loop.pid" -role "fetch_loop" | Out-Null
    Start-Daemon -role "fetch_loop" -script "06_live_bot\fetch_loop.py" `
        -logRedirect "$bot\fetch_loop.out" | Out-Null
} else {
    Write-Log "fetch_loop.py alive PID=$($flProc.ProcessId)" "OK"
}

# 4. supervisor.py
$spProc = Get-DaemonProc -pattern 'supervisor\.py'
if ($null -eq $spProc) {
    Write-Log "supervisor.py NOT running" "WARN"
    Remove-StaleLockfile -file "supervisor.pid" -role "supervisor" | Out-Null
    Start-Daemon -role "supervisor" -script "06_live_bot\supervisor.py" `
        -logRedirect "$bot\supervisor.out" -args @("--auto") | Out-Null
} else {
    Write-Log "supervisor.py alive PID=$($spProc.ProcessId)" "OK"
}

# 5. daemon.log freshness (only meaningful during trading window)
$dlog = "$bot\daemon.log"
if (Test-Path $dlog) {
    $age = (Get-Date) - (Get-Item $dlog).LastWriteTime
    if ($age.TotalMinutes -gt 30) {
        Write-Log "daemon.log STALE -- last write $([math]::Round($age.TotalMinutes,1))min ago" "WARN"
        # Note: don't kill bot for stale log alone -- might be sleeping.
        # Just warn.
    } else {
        Write-Log "daemon.log fresh -- $([math]::Round($age.TotalMinutes,1))min" "OK"
    }
}

Write-Log "=== healthcheck cycle end ==="
exit 0
