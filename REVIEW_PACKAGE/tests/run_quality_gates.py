"""Run all quality gates. Exits non-zero on any failure.

Usage:
  python tests/run_quality_gates.py            # full suite
  python tests/run_quality_gates.py --fast     # skip slow regression tests
"""
import sys, io, subprocess, argparse
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--fast", action="store_true", help="Skip slow regression tests")
    args = p.parse_args()

    cmd = ["python", "-m", "pytest", str(ROOT / "tests"), "-v", "--tb=short"]
    if args.fast:
        cmd += ["-m", "not slow"]

    print("=" * 60)
    print("CAMERON-BOT QUALITY GATES")
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
