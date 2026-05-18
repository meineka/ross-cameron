"""Phase-20 (ChatGPT-09:02 Task 2): build a machine-readable test
manifest so the reviewer doesn't have to take "all tests green" on
faith.

Walks every tests/test_*.py file and emits:
  - Per-file: test count, category, source-grep flag, purpose hint,
    review-status (default `not_reviewed` unless overridden by
    REVIEW_STATUS_OVERRIDES below).
  - Aggregate counts per category and review-status.
  - List of source-grep-only tests (those that use read_text /
    inspect.getsource / hard string scanning rather than behavior).

Output: docs/TEST_MANIFEST.md (also runnable from CI to detect drift).
Run:    python tests/build_test_manifest.py [--out PATH] [--check]

`--check` mode exits 1 if the regenerated manifest differs from the
committed one — wires into CI to enforce that the manifest stays in
sync with the actual test suite.
"""
from __future__ import annotations
import argparse
import ast
import re
import subprocess
import sys
from pathlib import Path
from collections import defaultdict

ROOT = Path(__file__).resolve().parent.parent
TESTS_DIR = ROOT / "tests"
OUT_DEFAULT = ROOT / "docs" / "TEST_MANIFEST.md"


# Phase-20: explicitly reviewed test files. Keep this list small and
# accurate — `not_reviewed` is the safer default and matches what
# ChatGPT-09:02 said about "600+ tests are not all manually reviewed".
REVIEW_STATUS_OVERRIDES = {
    # Phase-12/13/14/15 watchdog + postmortem suite — reviewed in design
    "test_watchdog_bugs.py": "reviewed",
    "test_no_trade_postmortem.py": "reviewed",
    "test_audit_multi_bot_gate.py": "reviewed",
    # Phase-11 log-separation — reviewed in design
    "test_trade_log_separation.py": "reviewed",
    # Phase-8/9/10 partial-fill semantics + Phase-17 golden scenarios
    "test_replay_executor_parity.py": "partially_reviewed",
    "test_replay_p2x_golden_scenarios.py": "reviewed",
    "test_replay_parity_bugs.py": "partially_reviewed",
    # Phase-16 premarket-scanner — reviewed in design
    "test_premarket_scanner_v2.py": "reviewed",
    # Constraints YAML drift gate — reviewed
    "test_constraints_in_code.py": "reviewed",
    # Risk engine + Cameron compliance — partial review
    "test_risk_engine.py": "partially_reviewed",
    "test_cameron_compliance.py": "partially_reviewed",
    # Phase-19 test-gate infrastructure
    "test_pilot_baseline.py": "partially_reviewed",
    "test_replay_regression.py": "partially_reviewed",
    # Phase-21 smoke gate + manifest freshness
    "test_smoke_imports.py": "reviewed",
    "test_manifest_freshness.py": "reviewed",
    # Phase-21 critical promotions
    "test_safe_bracket_fix.py": "partially_reviewed",
    "test_safe_bracket_status_bugs.py": "partially_reviewed",
    "test_manage_position_pnl_bugs.py": "partially_reviewed",
    # Phase-22 structured logging
    "test_structured_logger.py": "reviewed",
}


# Pattern category hints from filename
CATEGORY_HINTS = [
    (re.compile(r"replay.*regression|replay_regression"), "replay"),
    (re.compile(r"pilot|baseline"), "replay"),
    (re.compile(r"replay.*parity|replay_executor_parity|replay_parity"), "replay"),
    (re.compile(r"replay.*golden|golden.*scenario"), "integration"),
    (re.compile(r"watchdog|preflight"), "critical"),
    (re.compile(r"audit|multi.*bot"), "critical"),
    (re.compile(r"trade_log|log_separation"), "critical"),
    (re.compile(r"constraints_in_code"), "critical"),
    (re.compile(r"risk_engine|review_fixes"), "critical"),
    (re.compile(r"no_trade_postmortem"), "integration"),
    (re.compile(r"premarket"), "integration"),
    (re.compile(r"fake_broker|broker"), "integration"),
    (re.compile(r"ws_init|ws_loop|websocket"), "integration"),
    (re.compile(r"two_source|catalyst"), "integration"),
    (re.compile(r"compliance"), "integration"),
]

# Detect source-grep tests heuristically
SOURCE_GREP_INDICATORS = (
    "read_text(", "Path.read_text", "inspect.getsource",
    "open(", "with open", "\"def \"", "'def '",
)


def detect_category(filename: str, content: str) -> str:
    """Pick a category from filename + content heuristics, then check
    if the file already declares a pytest module marker that overrides."""
    # Module-level marker takes priority
    m = re.search(r"^\s*pytestmark\s*=\s*pytest\.mark\.(\w+)", content, re.M)
    if m:
        return m.group(1)
    # Filename hints
    for rx, cat in CATEGORY_HINTS:
        if rx.search(filename):
            return cat
    return "unit"


def count_tests(content: str) -> int:
    """Count `def test_...` and `async def test_...` functions, plus
    each `@pytest.mark.parametrize` row when statically inspectable."""
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return 0
    count = 0
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name.startswith("test_"):
                count += 1
    return count


def is_source_grep(content: str) -> bool:
    return any(ind in content for ind in SOURCE_GREP_INDICATORS)


def purpose_hint(filename: str, content: str) -> str:
    """Extract a one-sentence purpose from the module docstring."""
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return ""
    doc = ast.get_docstring(tree)
    if not doc:
        return ""
    # First non-empty line, trimmed
    for line in doc.splitlines():
        line = line.strip()
        if line:
            return line[:120]
    return ""


def build_manifest() -> dict:
    files = sorted(TESTS_DIR.glob("test_*.py"))
    rows = []
    by_cat: dict[str, int] = defaultdict(int)
    by_review: dict[str, int] = defaultdict(int)
    source_grep_files = []
    total_tests = 0
    for fp in files:
        try:
            content = fp.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        n = count_tests(content)
        cat = detect_category(fp.name, content)
        is_sg = is_source_grep(content)
        review = REVIEW_STATUS_OVERRIDES.get(fp.name, "not_reviewed")
        purpose = purpose_hint(fp.name, content)
        rows.append({
            "file": fp.name,
            "tests": n,
            "category": cat,
            "source_grep": is_sg,
            "review": review,
            "purpose": purpose,
        })
        by_cat[cat] += n
        by_review[review] += n
        total_tests += n
        if is_sg:
            source_grep_files.append(fp.name)
    return {
        "total_files": len(rows),
        "total_tests": total_tests,
        "rows": rows,
        "by_category": dict(by_cat),
        "by_review_status": dict(by_review),
        "source_grep_files": source_grep_files,
    }


def collect_only_output() -> tuple[str, int]:
    """Run `pytest --collect-only -q` and return (output, count)."""
    try:
        r = subprocess.run(
            [sys.executable, "-m", "pytest", "--collect-only", "-q",
             str(TESTS_DIR)],
            cwd=ROOT, capture_output=True, text=True, timeout=120,
        )
    except Exception as e:
        return f"<collect failed: {e}>", 0
    out = (r.stdout or "") + (r.stderr or "")
    m = re.search(r"(\d+)\s+tests?\s+collected", out)
    n = int(m.group(1)) if m else 0
    return out, n


def render_markdown(manifest: dict,
                     collect_text: str | None,
                     collect_n: int | None) -> str:
    lines = []
    lines.append("# Test Manifest")
    lines.append("")
    lines.append("Phase-20 (ChatGPT-09:02 Task 2): machine-readable inventory "
                 "of every test in `tests/test_*.py`. Generated by "
                 "`tests/build_test_manifest.py`. Regenerate after adding "
                 "or removing tests so the reviewer can audit drift.")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append(f"- **Total files**: {manifest['total_files']}")
    lines.append(f"- **Total `def test_*` functions**: {manifest['total_tests']}")
    if collect_n is not None:
        lines.append(f"- **pytest --collect-only count** (parametrize-expanded): "
                     f"{collect_n}")
    lines.append("")
    lines.append("### Tests per category")
    lines.append("")
    lines.append("| Category | Tests |")
    lines.append("|---|---|")
    for cat in sorted(manifest["by_category"]):
        lines.append(f"| {cat} | {manifest['by_category'][cat]} |")
    lines.append("")
    lines.append("### Tests per review-status")
    lines.append("")
    lines.append("| Status | Tests |")
    lines.append("|---|---|")
    for st in sorted(manifest["by_review_status"]):
        lines.append(f"| {st} | {manifest['by_review_status'][st]} |")
    lines.append("")
    lines.append("Status meaning:")
    lines.append("- `reviewed` — design + every assertion line-by-line audited")
    lines.append("- `partially_reviewed` — author + at least one focused pass")
    lines.append("- `not_reviewed` — exists and passes but no manual line-by-line audit")
    lines.append("")
    lines.append("## Source-grep-only tests")
    lines.append("")
    lines.append("Tests that rely on `read_text` / `inspect.getsource` / "
                 "hard string scanning rather than calling code paths. "
                 "These are easier to write but easier to drift — review needed.")
    lines.append("")
    if manifest["source_grep_files"]:
        for f in manifest["source_grep_files"]:
            lines.append(f"- `{f}`")
    else:
        lines.append("_None detected._")
    lines.append("")
    lines.append("## Per-file inventory")
    lines.append("")
    lines.append("| File | Tests | Category | Source-grep | Review | Purpose |")
    lines.append("|---|---|---|---|---|---|")
    for r in sorted(manifest["rows"], key=lambda x: x["file"]):
        sg = "yes" if r["source_grep"] else "no"
        purpose = r["purpose"].replace("|", "\\|") if r["purpose"] else ""
        lines.append(f"| `{r['file']}` | {r['tests']} | {r['category']} | "
                     f"{sg} | {r['review']} | {purpose} |")
    lines.append("")
    if collect_text is not None:
        lines.append("## pytest --collect-only -q (latest)")
        lines.append("")
        lines.append("```text")
        lines.append(collect_text.strip()[-4000:])  # cap to last 4 KB
        lines.append("```")
        lines.append("")
    lines.append("## Gate semantics (Phase-19 / ChatGPT-08:49 Task 3)")
    lines.append("")
    lines.append("- `tests/run_quality_gates.py --fast`  → `-m \"smoke or critical\"` "
                 "(~37 tests, <30 s) — loop-tick safety gate")
    lines.append("- `tests/run_quality_gates.py`          → `-m \"not slow\"` "
                 "(~624 tests) — default daily check")
    lines.append("- `tests/run_quality_gates.py --full`   → no filter "
                 "(~630 tests) — release / live-start / refactor gate")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--out", default=str(OUT_DEFAULT))
    p.add_argument("--check", action="store_true",
                   help="exit 1 if the manifest is stale (CI mode)")
    p.add_argument("--with-collect", action="store_true",
                   help="also embed pytest --collect-only output (slower; "
                        "non-deterministic — committed manifest should omit)")
    # Back-compat alias (older CI invocation)
    p.add_argument("--no-collect", action="store_true",
                   help="(deprecated; --no-collect is now the default)")
    args = p.parse_args(argv)
    manifest = build_manifest()
    if args.with_collect:
        coll_text, coll_n = collect_only_output()
    else:
        coll_text, coll_n = None, None
    rendered = render_markdown(manifest, coll_text, coll_n)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # Force LF line endings so the freshness check is stable across
    # platforms (Windows write_text defaults to CRLF translation).
    if args.check:
        if not out_path.exists():
            print(f"MANIFEST MISSING: {out_path}", file=sys.stderr)
            return 1
        existing = out_path.read_text(encoding="utf-8")
        # Strip the collect block (volatile) AND normalize line-endings
        # so CRLF/LF differences don't trip the check.
        def _normalize(s):
            # Drop the collect-only section until the next `## ` heading
            s2 = re.sub(
                r"## pytest --collect-only.*?(?=^## )",
                "",
                s, flags=re.S | re.M,
            )
            # If the section was at the end of the file (no next heading),
            # drop everything from the header to EOF.
            s2 = re.sub(r"## pytest --collect-only.*", "", s2, flags=re.S)
            return s2.replace("\r\n", "\n").strip()
        if _normalize(existing) != _normalize(rendered):
            print(f"MANIFEST STALE: rerun `python {Path(__file__).name}` "
                  f"and commit {out_path.relative_to(ROOT)}",
                  file=sys.stderr)
            # Phase-70.1: dump first diff lines for CI debugging.
            # Without this, stale-manifest failures on Linux CI were
            # opaque — no way to tell whether file-discovery, line-
            # endings, or content was the actual diff.
            import difflib
            diff = list(difflib.unified_diff(
                _normalize(existing).splitlines(),
                _normalize(rendered).splitlines(),
                fromfile="committed", tofile="rendered",
                lineterm="", n=2,
            ))
            print("--- first 40 diff lines ---", file=sys.stderr)
            for line in diff[:40]:
                print(line, file=sys.stderr)
            return 1
        print(f"manifest ok: {out_path}")
        return 0
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(rendered)
    print(f"wrote {out_path}")
    print(f"  files: {manifest['total_files']}")
    print(f"  tests (ast): {manifest['total_tests']}")
    print(f"  tests (collect): {coll_n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
