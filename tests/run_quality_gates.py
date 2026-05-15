"""Run all quality gates. Exits non-zero on any failure.

Phase-19 (ChatGPT-08:49 #1): tiered test gates.

Usage:
  python tests/run_quality_gates.py            # default: not slow
  python tests/run_quality_gates.py --fast     # smoke or critical (loop tick)
  python tests/run_quality_gates.py --full     # everything incl. slow
  python tests/run_quality_gates.py --gate critical   # explicit marker

Gate strategy:
  --fast      → -m "smoke or critical"  : <30s, run on every Claude-loop tick
  --default   → -m "not slow"           : everything except heavy replay/pilot
  --full      → no marker filter        : release-gate / live-start / refactor
"""
import sys, io, subprocess, argparse
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent


def _check_python_environment() -> bool:
    """Phase-60 (ChatGPT P1 follow-up): warn before running tests if
    we're on system-Python without project deps installed. Operators
    have wasted 10+ minutes debugging fake test fails caused by missing
    alpaca/yfinance/pyarrow on system Python — this preflight short-
    circuits that. Returns True if env looks OK to proceed.

    Phase-61 (re-audit): now BLOCKS on wrong venv, not just warns.
    Rationale: if `.venv` exists but pytest is run from system-Python,
    even when imports happen to work the alpaca/pyarrow versions can
    diverge between system and venv, producing test failures that look
    like real bugs. Forcing the venv eliminates the entire failure mode.
    Set ALLOW_NON_VENV=1 in env to override (e.g. CI runners that
    install deps system-wide intentionally).
    """
    import os
    venv_py = ROOT / ".venv" / "Scripts" / "python.exe"
    if not venv_py.exists():
        venv_py = ROOT / ".venv" / "bin" / "python"  # POSIX
    wrong_venv = (venv_py.exists()
                   and Path(sys.executable).resolve() != venv_py.resolve())
    if wrong_venv and not os.environ.get("ALLOW_NON_VENV"):
        print("=" * 60)
        print("❌ WRONG PYTHON: project venv exists but you're using a different interpreter")
        print(f"   current : {sys.executable}")
        print(f"   expected: {venv_py}")
        print()
        print("   Re-run with the venv:")
        print(f"     {venv_py} {' '.join(sys.argv)}")
        print()
        print("   Or, if you intentionally want a system-Python run "
              "(e.g. on a")
        print("   CI runner with deps installed globally), set:")
        print("     ALLOW_NON_VENV=1")
        print("=" * 60)
        return False
    if wrong_venv:
        # ALLOW_NON_VENV set — still print a non-blocking warning
        print("=" * 60)
        print("⚠️  Running outside project venv (ALLOW_NON_VENV set)")
        print(f"   current: {sys.executable}")
        print(f"   venv   : {venv_py}")
        print("=" * 60)
    # Probe critical imports — fail fast if missing
    missing = []
    for pkg in ("alpaca", "yfinance", "pandas", "pyarrow"):
        try:
            __import__(pkg)
        except ImportError:
            missing.append(pkg)
    if missing:
        print("=" * 60)
        print(f"❌ MISSING DEPENDENCIES: {', '.join(missing)}")
        print(f"   Install with: pip install {' '.join(missing)}")
        print(f"   Or use the project venv (see warning above).")
        print("=" * 60)
        return False
    return True


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--fast", action="store_true",
                   help="smoke/critical gate only (<30s, loop-tick friendly)")
    p.add_argument("--full", action="store_true",
                   help="full release gate including slow tests")
    p.add_argument("--gate", default=None,
                   help="explicit pytest marker expression (e.g. 'smoke or critical')")
    args = p.parse_args()

    cmd = [sys.executable, "-m", "pytest", str(ROOT / "tests"), "-v", "--tb=short"]
    label = "default (not slow)"
    if args.gate:
        cmd += ["-m", args.gate]
        label = f"custom gate: {args.gate}"
    elif args.fast:
        # Phase-19: --fast = smoke or critical. NOT "not slow", because
        # "not slow" still picks up 600+ tests which can run multi-minute.
        cmd += ["-m", "smoke or critical"]
        label = "fast (smoke or critical)"
    elif args.full:
        label = "full (all markers including slow)"
    else:
        cmd += ["-m", "not slow"]

    print("=" * 60)
    print("CAMERON-BOT QUALITY GATES")
    print(f"Gate: {label}")
    print("=" * 60)
    # Phase-60 preflight: warn if wrong Python / missing deps before
    # running 1000 tests that all fail for the same root cause.
    if not _check_python_environment():
        sys.exit(2)
    print(f"Cmd: {' '.join(cmd)}")
    print()

    # Phase-61: bound subprocess so a hung test can't hang the gate forever.
    # 10 min for fast/default, 30 min for full (replay/pilot can be slow).
    timeout_sec = 1800 if args.full else 600
    try:
        r = subprocess.run(cmd, cwd=ROOT, timeout=timeout_sec)
    except subprocess.TimeoutExpired:
        print("\n" + "!" * 60)
        print(f"QUALITY GATE TIMED OUT after {timeout_sec}s — likely a hung test")
        print("Re-run with --full for higher limit, or diagnose the stuck case")
        print("!" * 60)
        sys.exit(124)
    if r.returncode != 0:
        print("\n" + "!" * 60)
        print("QUALITY GATE FAILED — DO NOT COMMIT until fixed")
        print("!" * 60)
        sys.exit(r.returncode)
    print("\n" + "=" * 60)
    print("ALL QUALITY GATES PASSED")
    print("=" * 60)


if __name__ == "__main__":
    main()
