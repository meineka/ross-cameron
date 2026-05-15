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
    """
    venv_py = ROOT / ".venv" / "Scripts" / "python.exe"
    if not venv_py.exists():
        venv_py = ROOT / ".venv" / "bin" / "python"  # POSIX
    if venv_py.exists() and Path(sys.executable).resolve() != venv_py.resolve():
        print("=" * 60)
        print("⚠️  WARNING: not running in project venv!")
        print(f"   current: {sys.executable}")
        print(f"   expected: {venv_py}")
        print()
        print("   Project deps (alpaca, yfinance, pyarrow) may be missing.")
        print(f"   Re-run with: {venv_py} {' '.join(sys.argv)}")
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

    r = subprocess.run(cmd, cwd=ROOT)
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
