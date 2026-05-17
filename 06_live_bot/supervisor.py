"""supervisor.py — Phase-67 (2026-05-17)

Meta-watchdog that runs every 30 minutes (2× per hour) to detect AND
correct system-level issues that the existing per-component watchdogs
can't see:

  - Duplicate logical processes (Phase-65 prevents NEW dups but doesn't
    clean ones that slipped past, e.g. from a Windows scheduled-task
    auto-start firing alongside the local watchdog).
  - Stale lockfiles pointing at dead PIDs (Phase-65 handles this on the
    next start, but a non-started component never triggers cleanup).
  - fetch_loop dead with no respawn (it has no watchdog of its own).
  - Watchdog itself dead (no one watches the watcher otherwise).
  - Daemon-log stale (heartbeat not advancing during expected hours).

What is OUTSIDE its scope:
  - Trade decisions (delegated to bot.py)
  - Open positions (delegated to watchdog's position-recovery)
  - Data correctness (delegated to fetch_loop's idempotent skip-existing)

Design principles:
  CONSERVATIVE-BY-DEFAULT: --dry-run by default — only report what it
                            WOULD do. Operator must pass --auto for
                            actual corrections.
  NEVER kill bot.py with open positions — delegate to watchdog's
                            existing position-recovery logic.
  EVERY action is logged to supervisor.jsonl + ntfy push.
  SELF-LOCKED via Phase-65 supervisor.pid so we can't run twice.

CLI:
    python supervisor.py                # 30-min daemon, dry-run mode
    python supervisor.py --auto         # daemon with auto-correct enabled
    python supervisor.py --once         # one cycle, exit
    python supervisor.py --once --auto  # one cycle, actually fix things
    python supervisor.py --interval-min 15  # custom cadence
"""
from __future__ import annotations
import argparse
import atexit
import json
import logging
import os
import signal
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
PILOT_DIR = ROOT / "04_backtest" / "data_pilot"

sys.path.insert(0, str(HERE))

LOG_FILE = HERE / "supervisor.log"
JSONL_FILE = HERE / "supervisor.jsonl"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("supervisor")

DEFAULT_INTERVAL_MIN = 30  # 2× per hour as user requested
DAEMON_LOG_MAX_AGE_TRADING_HOURS = 30 * 60  # 30 min
DAEMON_LOG_MAX_AGE_OFF_HOURS = 24 * 3600    # 24 hours
DATA_PARQUET_MAX_AGE_TRADING_HOURS = 60 * 60  # 60 min while fetching

# Graceful shutdown
_stop_requested = False


def _signal_handler(signum, frame):  # noqa: ARG001
    global _stop_requested
    log.info("signal %s — finishing current cycle then stopping", signum)
    _stop_requested = True


signal.signal(signal.SIGINT, _signal_handler)
try:
    signal.signal(signal.SIGTERM, _signal_handler)
except AttributeError:
    pass


# ─── Data types ──────────────────────────────────────────────────────────


@dataclass
class ProcInfo:
    """One python.exe process as reported by the OS."""
    pid: int
    parent_pid: int
    role: str  # "watchdog" | "bot" | "fetch_loop" | "fetch_hist" | "supervisor" | "other"
    is_venv_launcher: bool  # True = launcher wrapper; False = real interpreter
    cmdline: str


@dataclass
class LogicalProc:
    """A logical Python invocation (1 launcher + 1 interpreter on Windows
    venvs; or just 1 interpreter on POSIX). We treat the pair as ONE
    instance for duplicate-detection purposes."""
    role: str
    interpreter_pid: int            # the "real" PID — owns the lockfile
    launcher_pid: int | None        # the venv launcher (None on POSIX)


@dataclass
class Issue:
    severity: str  # "info" | "warn" | "error"
    type: str
    description: str
    fix_action: str | None = None   # human-readable; None means no auto-fix


@dataclass
class Action:
    when: str
    type: str
    description: str
    success: bool
    details: dict = field(default_factory=dict)


# ─── Inventory ───────────────────────────────────────────────────────────


def _classify_role(cmdline: str) -> str:
    cl = cmdline.lower()
    if "supervisor.py" in cl:
        return "supervisor"
    if "watchdog.py" in cl:
        return "watchdog"
    if "fetch_loop.py" in cl:
        return "fetch_loop"
    if "fetch_historical_range" in cl:
        return "fetch_hist"
    if "bot.py" in cl and "--daemon" in cl:
        return "bot"
    return "other"


def list_python_processes() -> list[ProcInfo]:
    """Return all python.exe processes via WMI (Windows) or psutil-less
    POSIX fallback. Never raises — returns [] on probe failure."""
    if os.name == "nt":
        try:
            out = subprocess.check_output(
                ["wmic", "process", "where", "name='python.exe'",
                 "get", "ProcessId,ParentProcessId,CommandLine", "/format:csv"],
                text=True, timeout=15, stderr=subprocess.DEVNULL,
            )
        except (subprocess.TimeoutExpired, subprocess.CalledProcessError,
                FileNotFoundError, OSError) as e:
            log.warning("wmic probe failed: %s", e)
            return []
        procs: list[ProcInfo] = []
        for line in out.splitlines():
            if not line.strip() or "Node," in line:
                continue
            # CSV: Node, CommandLine, ParentProcessId, ProcessId
            parts = line.rsplit(",", 2)
            if len(parts) != 3:
                continue
            cmd_part, ppid_str, pid_str = parts
            try:
                pid = int(pid_str.strip())
                ppid = int(ppid_str.strip())
            except (TypeError, ValueError):
                continue
            # Cmdline is everything after "Node," prefix
            cmdline = cmd_part.split(",", 1)[1] if "," in cmd_part else cmd_part
            cmdline = cmdline.strip()
            role = _classify_role(cmdline)
            is_launcher = "\\.venv\\" in cmdline.lower() or "/.venv/" in cmdline.lower()
            procs.append(ProcInfo(
                pid=pid, parent_pid=ppid, role=role,
                is_venv_launcher=is_launcher, cmdline=cmdline,
            ))
        return procs
    # POSIX fallback (good enough for tests; the live bot is Windows)
    return []


def group_into_logical(procs: list[ProcInfo]) -> list[LogicalProc]:
    """Pair venv-launcher → interpreter chains into single logical
    processes. On Windows `.venv\\python.exe` is a launcher that spawns
    the actual system python interpreter; both appear in the process
    list but represent ONE Python invocation."""
    by_pid = {p.pid: p for p in procs}
    interpreters = [p for p in procs if not p.is_venv_launcher]
    logical: list[LogicalProc] = []
    used_launchers: set[int] = set()
    for itp in interpreters:
        # If our parent is a launcher of the same role, pair them
        parent = by_pid.get(itp.parent_pid)
        launcher_pid = None
        if (parent and parent.is_venv_launcher
                and parent.role == itp.role
                and parent.pid not in used_launchers):
            launcher_pid = parent.pid
            used_launchers.add(parent.pid)
        if itp.role == "other":
            continue
        logical.append(LogicalProc(
            role=itp.role,
            interpreter_pid=itp.pid,
            launcher_pid=launcher_pid,
        ))
    # Orphan launchers (no matched interpreter — e.g. interpreter died)
    for lp in procs:
        if (lp.is_venv_launcher and lp.role != "other"
                and lp.pid not in used_launchers):
            logical.append(LogicalProc(
                role=lp.role,
                interpreter_pid=lp.pid,  # treat launcher as the proc
                launcher_pid=None,
            ))
    return logical


# ─── Audit (invariant checks) ────────────────────────────────────────────


def _read_pid_file(path: Path) -> int | None:
    if not path.exists():
        return None
    try:
        text = path.read_text(encoding="utf-8").strip()
        return int(text) if text.lstrip("-").isdigit() else None
    except OSError:
        return None


def audit(logical_procs: list[LogicalProc]) -> list[Issue]:
    """Check system invariants. Returns issues sorted by severity."""
    issues: list[Issue] = []
    by_role = {r: [lp for lp in logical_procs if lp.role == r]
                for r in ("watchdog", "bot", "fetch_loop", "fetch_hist",
                          "supervisor")}

    # 1. Exactly one watchdog
    n_wd = len(by_role["watchdog"])
    if n_wd == 0:
        issues.append(Issue("error", "watchdog_dead",
                              "no watchdog.py running — bot is unmonitored",
                              "start watchdog.py"))
    elif n_wd > 1:
        issues.append(Issue("warn", "watchdog_duplicate",
                              f"{n_wd} watchdog instances — kill extras",
                              "kill oldest watchdog(s)"))

    # 2. Exactly one bot.py + bot.pid matches
    bot_procs = by_role["bot"]
    n_bot = len(bot_procs)
    bot_lock_pid = _read_pid_file(HERE / "bot.pid")
    bot_pids = {lp.interpreter_pid for lp in bot_procs}
    if n_bot == 0:
        issues.append(Issue("warn", "bot_dead",
                              "no bot.py running — watchdog should respawn",
                              None))  # let watchdog handle
        if bot_lock_pid is not None:
            issues.append(Issue("info", "bot_pid_stale",
                                  f"bot.pid={bot_lock_pid} but no bot running",
                                  "delete stale bot.pid"))
    elif n_bot > 1:
        issues.append(Issue("error", "bot_duplicate",
                              f"{n_bot} bot.py instances — Alpaca WS limit risk",
                              "kill duplicate bot.py"))
    else:
        # exactly one bot, verify pid file matches
        if bot_lock_pid is None:
            issues.append(Issue("warn", "bot_pid_missing",
                                  f"bot.py running (PID={list(bot_pids)[0]}) "
                                  f"but no bot.pid — Phase-62/65 wiring broken?",
                                  None))
        elif bot_lock_pid not in bot_pids:
            issues.append(Issue("warn", "bot_pid_mismatch",
                                  f"bot.pid={bot_lock_pid} doesn't match "
                                  f"running PID {bot_pids}",
                                  "rewrite bot.pid to running PID"))

    # 3. Exactly one fetch_loop + lockfile match
    loop_procs = by_role["fetch_loop"]
    n_loop = len(loop_procs)
    loop_lock_pid = _read_pid_file(HERE / "fetch_loop.pid")
    loop_pids = {lp.interpreter_pid for lp in loop_procs}
    if n_loop == 0:
        issues.append(Issue("warn", "fetch_loop_dead",
                              "no fetch_loop running — dataset stops growing",
                              "start fetch_loop.py"))
        if loop_lock_pid is not None:
            issues.append(Issue("info", "fetch_loop_pid_stale",
                                  f"fetch_loop.pid={loop_lock_pid} but no loop",
                                  "delete stale fetch_loop.pid"))
    elif n_loop > 1:
        issues.append(Issue("error", "fetch_loop_duplicate",
                              f"{n_loop} fetch_loops — parquet race risk",
                              "kill duplicate fetch_loop"))

    # 4. daemon.log freshness
    daemon_log = HERE / "daemon.log"
    if daemon_log.exists():
        age = time.time() - daemon_log.stat().st_mtime
        # We don't know if market is open here; use loose 24h limit always
        if age > DAEMON_LOG_MAX_AGE_OFF_HOURS:
            issues.append(Issue("warn", "daemon_log_stale",
                                  f"daemon.log not updated for "
                                  f"{age/3600:.1f}h",
                                  None))

    return issues


# ─── Reconcile (auto-correct) ────────────────────────────────────────────


def _push_alert(level: str, title: str, body: str) -> None:
    """Best-effort ntfy push. Never raises."""
    try:
        from alerter import make_alerter
        a = make_alerter()
        if a is not None:
            a.send(level, title, body, force=True)
    except Exception as e:
        log.debug("alert push failed: %s", e)


def _kill_pid(pid: int) -> bool:
    """OS-portable kill. Returns True on success."""
    if os.name == "nt":
        try:
            subprocess.check_call(
                ["taskkill", "/F", "/PID", str(pid)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=10,
            )
            return True
        except Exception as e:
            log.warning("taskkill PID=%d failed: %s", pid, e)
            return False
    try:
        os.kill(pid, signal.SIGTERM)
        return True
    except Exception:
        return False


def _spawn_detached(cmd: list[str], log_path: Path) -> int | None:
    """Spawn a Python script as a detached background process."""
    try:
        flags = (subprocess.CREATE_NEW_PROCESS_GROUP
                 if os.name == "nt" else 0)
        with open(log_path, "ab") as logf:
            proc = subprocess.Popen(
                cmd, cwd=str(HERE),
                stdout=logf, stderr=subprocess.STDOUT,
                creationflags=flags, close_fds=True,
            )
        log.info("spawned %s → PID %d", " ".join(cmd), proc.pid)
        return proc.pid
    except Exception as e:
        log.error("spawn failed for %s: %s", cmd, e)
        return None


def reconcile(issues: list[Issue], procs: list[ProcInfo],
                logical_procs: list[LogicalProc],
                *, dry_run: bool = True,
                bot_python: str | None = None) -> list[Action]:
    """Execute fixes for actionable issues. Conservative: only known-safe
    auto-corrections; complex cases get logged as info-only.

    Returns list of Action taken (or would-take in dry-run)."""
    actions: list[Action] = []
    if bot_python is None:
        # Prefer .venv python
        cands = [
            ROOT / ".venv" / "Scripts" / "python.exe",
            ROOT / ".venv" / "bin" / "python",
        ]
        bot_python = next((str(c) for c in cands if c.exists()),
                            sys.executable)

    by_role = {r: [lp for lp in logical_procs if lp.role == r]
                for r in ("watchdog", "bot", "fetch_loop", "fetch_hist",
                          "supervisor")}

    for issue in issues:
        if issue.type == "bot_pid_stale":
            actions.append(_do_action(
                "delete_stale_bot_pid",
                f"unlink bot.pid (named dead PID)",
                dry_run, lambda: (HERE / "bot.pid").unlink(missing_ok=True),
            ))
        elif issue.type == "fetch_loop_pid_stale":
            actions.append(_do_action(
                "delete_stale_fetch_loop_pid",
                f"unlink fetch_loop.pid (named dead PID)",
                dry_run,
                lambda: (HERE / "fetch_loop.pid").unlink(missing_ok=True),
            ))
        elif issue.type == "fetch_loop_dead":
            actions.append(_do_action(
                "spawn_fetch_loop",
                f"spawn {bot_python} fetch_loop.py",
                dry_run,
                lambda: _spawn_detached(
                    [bot_python, "fetch_loop.py"],
                    HERE / "fetch_loop.out",
                ),
            ))
        elif issue.type == "watchdog_dead":
            actions.append(_do_action(
                "spawn_watchdog",
                f"spawn {bot_python} watchdog.py",
                dry_run,
                lambda: _spawn_detached(
                    [bot_python, "watchdog.py"],
                    HERE / "watchdog.log",
                ),
            ))
        elif issue.type == "fetch_loop_duplicate":
            # Kill all but the one that holds the lockfile
            loop_lock_pid = _read_pid_file(HERE / "fetch_loop.pid")
            for lp in by_role["fetch_loop"]:
                if lp.interpreter_pid == loop_lock_pid:
                    continue
                actions.append(_do_action(
                    "kill_dup_fetch_loop",
                    f"kill duplicate fetch_loop PID={lp.interpreter_pid}",
                    dry_run, lambda lp=lp: _kill_pid(lp.interpreter_pid),
                ))
        elif issue.type == "bot_duplicate":
            # NEVER kill bot.py with open positions — delegate to watchdog.
            # Just push alert so operator can intervene.
            _push_alert(
                "error", "🔴 bot.py DUPLICATE",
                f"{len(by_role['bot'])} bot.py instances running. "
                f"Manual action needed (positions may be open). "
                f"supervisor refused to auto-kill.",
            )
            actions.append(Action(
                when=datetime.now(timezone.utc).isoformat(),
                type="alert_only_bot_duplicate",
                description=f"alerted operator about {len(by_role['bot'])} "
                             f"bot.py instances; did NOT kill (position-safe)",
                success=True,
            ))
        elif issue.type == "watchdog_duplicate":
            # Kill all but oldest watchdog (first-spawned likely the original)
            wds = sorted(by_role["watchdog"], key=lambda lp: lp.interpreter_pid)
            for lp in wds[1:]:
                actions.append(_do_action(
                    "kill_dup_watchdog",
                    f"kill duplicate watchdog PID={lp.interpreter_pid}",
                    dry_run, lambda lp=lp: _kill_pid(lp.interpreter_pid),
                ))

    # Push summary alert if we took actions
    real_actions = [a for a in actions if a.success and not dry_run]
    if real_actions:
        _push_alert(
            "warn", "🛠️ Supervisor auto-correction",
            f"{len(real_actions)} action(s): " +
            "; ".join(a.type for a in real_actions[:5]),
        )
    return actions


def _do_action(action_type: str, description: str,
                 dry_run: bool, do_fn) -> Action:
    """Wrap an action: dry-run logs intent, --auto actually executes."""
    when = datetime.now(timezone.utc).isoformat()
    if dry_run:
        log.info("[dry-run] would do: %s", description)
        return Action(when=when, type=action_type,
                       description="(dry-run) " + description,
                       success=True)
    try:
        result = do_fn()
        log.info("did: %s → %s", description, result)
        return Action(when=when, type=action_type,
                       description=description, success=True,
                       details={"result": str(result)[:200]})
    except Exception as e:
        log.error("action %s FAILED: %s", action_type, e)
        return Action(when=when, type=action_type,
                       description=description, success=False,
                       details={"error": f"{type(e).__name__}: {e}"})


# ─── Cycle ────────────────────────────────────────────────────────────────


def run_one_cycle(dry_run: bool = True,
                    bot_python: str | None = None) -> dict:
    """One supervisor cycle: inventory → audit → reconcile → JSONL log.
    Returns summary dict suitable for the JSONL append."""
    t_start = time.monotonic()
    procs = list_python_processes()
    logical = group_into_logical(procs)
    issues = audit(logical)
    actions = reconcile(issues, procs, logical,
                          dry_run=dry_run, bot_python=bot_python)
    elapsed_ms = (time.monotonic() - t_start) * 1000
    summary = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "n_procs": len(procs),
        "n_logical": len(logical),
        "roles": {r: sum(1 for lp in logical if lp.role == r)
                    for r in ("watchdog", "bot", "fetch_loop", "fetch_hist",
                              "supervisor")},
        "issues": [{"severity": i.severity, "type": i.type,
                     "description": i.description,
                     "fix_action": i.fix_action}
                    for i in issues],
        "actions": [asdict(a) for a in actions],
        "dry_run": dry_run,
        "elapsed_ms": round(elapsed_ms, 1),
    }
    # JSONL log
    try:
        with open(JSONL_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(summary, ensure_ascii=False) + "\n")
    except OSError as e:
        log.warning("could not write JSONL: %s", e)
    # Human log
    log.info(
        "Cycle: procs=%d logical=%d issues=%d actions=%d "
        "[dry_run=%s] %.0fms",
        len(procs), len(logical), len(issues), len(actions),
        dry_run, elapsed_ms,
    )
    for i in issues:
        log.info("  [%s] %s — %s", i.severity, i.type, i.description)
    return summary


def supervise(*, interval_min: int = DEFAULT_INTERVAL_MIN,
                 dry_run: bool = True,
                 once: bool = False) -> int:
    """Main supervisor loop. Returns exit code."""
    log.info("Supervisor starting — interval=%dmin auto=%s once=%s",
              interval_min, not dry_run, once)
    while not _stop_requested:
        try:
            run_one_cycle(dry_run=dry_run)
        except Exception as e:
            log.error("cycle raised: %s", e, exc_info=True)
        if once:
            return 0
        if _stop_requested:
            break
        _sleep_interruptibly(interval_min * 60)
    log.info("supervisor exiting cleanly")
    return 0


def _sleep_interruptibly(seconds: int) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end and not _stop_requested:
        time.sleep(min(1.0, end - time.monotonic()))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval-min", type=int, default=DEFAULT_INTERVAL_MIN,
                     help=f"minutes between cycles (default "
                          f"{DEFAULT_INTERVAL_MIN} = 2x per hour)")
    ap.add_argument("--auto", action="store_true",
                     help="actually execute fixes (default: dry-run)")
    ap.add_argument("--once", action="store_true",
                     help="one cycle then exit")
    ap.add_argument("--force-lock", action="store_true",
                     help="steal stale supervisor.pid lock")
    args = ap.parse_args()

    # Self-lock (Phase-67 named lock)
    from process_lock import (
        enforce_single_supervisor_or_exit, release_supervisor_lock,
    )
    enforce_single_supervisor_or_exit(force=args.force_lock)
    atexit.register(release_supervisor_lock)

    return supervise(
        interval_min=args.interval_min,
        dry_run=not args.auto,
        once=args.once,
    )


if __name__ == "__main__":
    sys.exit(main())
