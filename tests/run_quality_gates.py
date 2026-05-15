"""Run all quality gates. Exits non-zero on any failure.

Phase-19 (ChatGPT-08:49 #1): tiered test gates.

Usage:
  python tests/run_quality_gates.py            # default: not slow
  python tests/run_quality_gates.py --fast     # critical only (loop tick)
  python tests/run_quality_gates.py --full     # everything incl. slow
  python tests/run_quality_gates.py --gate critical   # explicit marker

Gate strategy:
  --fast      → -m "critical"           : <30s, run on every Claude-loop tick
  --default   → -m "not slow"           : everything except heavy replay/pilot
  --full      → no marker filter        : release-gate / live-start / refactor
"""
import sys, io, subprocess, argparse
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--fast", action="store_true",
                   help="critical gate only (<30s, loop-tick friendly)")
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
        # Phase-19: --fast = critical marker only. NOT "not slow", because
        # "not slow" still picks up 600+ tests which can run multi-minute.
        cmd += ["-m", "critical"]
        label = "fast (critical only)"
    elif args.full:
        label = "full (all markers including slow)"
    else:
        cmd += ["-m", "not slow"]

    print("=" * 60)
    print("CAMERON-BOT QUALITY GATES")
    print(f"Gate: {label}")
    print("=" * 60)
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
