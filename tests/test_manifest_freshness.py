"""Phase-20 (ChatGPT-09:02 Task 2): the auto-generated TEST_MANIFEST.md
must stay in sync with the actual test suite. This test runs
`build_test_manifest.py --check --no-collect` and fails if the manifest
is stale — forcing the author to regenerate before committing.

Marked smoke so it runs on every loop tick.
"""
from __future__ import annotations
import subprocess
import sys
from pathlib import Path

import pytest


pytestmark = pytest.mark.smoke

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tests" / "build_test_manifest.py"
MANIFEST = ROOT / "docs" / "TEST_MANIFEST.md"


@pytest.mark.critical
def test_manifest_exists():
    assert MANIFEST.exists(), \
        f"docs/TEST_MANIFEST.md missing — run python tests/build_test_manifest.py"


@pytest.mark.critical
def test_manifest_is_in_sync_with_test_suite():
    """Regenerate manifest in-memory and compare against committed version
    (ignoring the volatile pytest --collect-only output block)."""
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "--check"],
        capture_output=True, text=True, cwd=ROOT, timeout=60,
    )
    if r.returncode != 0:
        pytest.fail(
            f"TEST_MANIFEST.md is stale:\n{r.stdout}\n{r.stderr}\n"
            "Run: python tests/build_test_manifest.py"
        )
