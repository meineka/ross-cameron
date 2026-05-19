"""Phase-77 (ChatGPT 20260518_2103/2108/2118/2138/2151): two fixes.

A. Atomic export-write
   ChatGPT reported FOUR consecutive incomplete exports (2108, 2118,
   2138, 2151) — each contained only 6-8 files. Root cause: build_export
   wrote the zip directly to 99_Claude_Chatgpt/ while Compress-Archive
   built it incrementally; ChatGPT's reader hit the file mid-write and
   saw a truncated 6-file partial zip. (Later inspections after the
   write completed showed the full 213 entries — confirming the race.)

   Fix: write to $env:TEMP first, validate REQUIRED files are present
   (06_live_bot/bot.py, tests/conftest.py, docs/TEST_MANIFEST.md, etc),
   THEN Move-Item to the final 99_Claude_Chatgpt path. Readers either
   see no file or the complete, validated file — never a partial.

B. TV-scan-status mid-day-resume bug
   ChatGPT 2103 ask #3: last_tradingview_scan_status/scanner_source/
   fallback_used remain null in status.json despite a populated
   watchlist.

   Root cause: when MID-DAY-RESUME loads the watchlist from disk
   (no scan runs), the old code still read _LAST_TV_SCAN_STATE (init
   values: status=None) and fell into the elif-candidates branch,
   setting scanner_source="yfinance_fallback". WRONG — the source was
   disk-cache, not yfinance.

   Fix: track which path produced the candidates (used_disk_resume vs
   ran_fresh_scan) and report scanner_source="disk_cache_resume" in
   the resume case. Also initialize day fields to "pending" at Bot
   construction so status.json never reports None for these.
"""
from __future__ import annotations
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.critical

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "06_live_bot"))


def _bot_src() -> str:
    return (ROOT / "06_live_bot" / "bot.py").read_text(encoding="utf-8")


def _export_src() -> str:
    return (ROOT / "06_live_bot" / "build_export.ps1").read_text(encoding="utf-8")


# ─── A1. Atomic-write pattern in build_export.ps1 ────────────────────────

def test_export_writes_to_temp_first():
    """The zip must be built in $env:TEMP, not directly in the final
    99_Claude_Chatgpt directory. Otherwise readers race the writer."""
    src = _export_src()
    assert "$env:TEMP" in src, "must use $env:TEMP for staging path"
    # The temp variable must exist and be used as Compress-Archive target
    assert "$tempZip" in src
    assert "DestinationPath $tempZip" in src or "-DestinationPath $tempZip" in src


def test_export_renames_atomically_after_validation():
    """Only after validation passes do we Move-Item to the final path.
    Move-Item on same volume is atomic — readers see either nothing or
    the complete file."""
    src = _export_src()
    assert "Move-Item" in src
    # The move target is the final 99_Claude_Chatgpt path
    assert "$finalZip" in src
    # Must come AFTER the validation gate
    import re
    # Pattern: validation block followed by Move-Item
    m = re.search(r"validation failed[\s\S]{0,500}Move-Item", src)
    assert m, "Move-Item must come after validation gate"


def test_export_uses_pid_suffix_for_temp():
    """Concurrent build_export runs must not stomp each other's temp
    files. Per-PID temp name ensures isolation."""
    src = _export_src()
    assert "$PID" in src


# ─── A2. Required-file validation ────────────────────────────────────────

def test_export_validates_required_files_present():
    """The 4× ChatGPT complaint: zip was missing 06_live_bot, tests/,
    docs/. Build-export must refuse to publish if essential files
    aren't in the zip."""
    src = _export_src()
    # The required-files list must include the core bot + tests + docs
    assert "06_live_bot/bot.py" in src
    assert "tests/conftest.py" in src
    assert "docs/TEST_MANIFEST.md" in src
    # And refuse-to-publish logic
    assert "INCOMPLETE EXPORT" in src or "missing required files" in src


def test_export_refuses_truncated_zips():
    """A real export has 200+ entries. Refuse anything < 100."""
    src = _export_src()
    assert "TRUNCATED EXPORT" in src or "entryCount -lt" in src
    # Threshold value
    import re
    m = re.search(r"entryCount\s*-lt\s*(\d+)", src)
    assert m, "must check minimum entry count"
    threshold = int(m.group(1))
    assert threshold >= 50, f"threshold too lenient: {threshold}"


def test_export_aborts_and_cleans_up_on_failure():
    """If validation fails, the temp file must be deleted and a hard
    error raised. Otherwise stale temp files accumulate."""
    src = _export_src()
    # On abort, the temp zip is removed and an exception is thrown
    assert "Remove-Item $tempZip" in src
    assert "throw" in src
    # Single source of truth: only one final-rename point (allow up to
    # 3 occurrences — the actual call plus header/inline doc references)
    import re
    # Count actual Move-Item INVOCATIONS (lines that aren't comments)
    invocations = [
        ln for ln in src.splitlines()
        if "Move-Item" in ln and not ln.lstrip().startswith("#")
    ]
    assert len(invocations) == 1, (
        f"expected exactly 1 Move-Item call, found {len(invocations)}: "
        f"{invocations}"
    )


# ─── A3. Existing security guarantees preserved ──────────────────────────

def test_export_still_excludes_env_files():
    """Phase-73's .env exclusion must not regress."""
    src = _export_src()
    assert ".env" in src
    assert ".env.local" in src or ".env.*" in src
    # Defensive secondary delete still present
    assert "defensive" in src.lower()


def test_export_still_scans_for_secret_leaks():
    """Phase-73's post-zip leak detector must still fire."""
    src = _export_src()
    assert "SECURITY LEAK DETECTED" in src or "leaks" in src
    assert "pem" in src and "key" in src


# ─── B1. TV-scan-status fields populated in real scan path ──────────────

def test_bot_init_sets_tv_status_to_pending():
    """Bot.__init__ must initialize day.last_tradingview_scan_status to
    "pending" — not None. Otherwise the first status.json write (before
    any scan) reports null, which doesn't help the operator."""
    src = _bot_src()
    import re
    # The init block must set last_tradingview_scan_status to "pending"
    assert re.search(
        r'self\.day\.last_tradingview_scan_status\s*=\s*"pending"',
        src,
    ), "Bot.__init__ must initialize TV-scan status to pending"
    assert re.search(
        r'self\.day\.scanner_source\s*=\s*"pending"',
        src,
    ), "Bot.__init__ must initialize scanner_source to pending"


def test_mid_day_resume_reports_disk_cache_source():
    """When MID-DAY-RESUME loads the watchlist from disk (no scan
    runs), scanner_source must be "disk_cache_resume" — not
    "yfinance_fallback" (the Phase-73 bug)."""
    src = _bot_src()
    assert "disk_cache_resume" in src, (
        "MID-DAY-RESUME must set scanner_source to disk_cache_resume"
    )
    assert "skipped_disk_resume" in src, (
        "last_tradingview_scan_status must reflect skipped scan"
    )


def test_mid_day_resume_tracked_separately_from_fresh_scan():
    """The flag that says 'we did a real scan' vs 'we loaded from disk'
    must be present so status reporting can disambiguate."""
    src = _bot_src()
    assert "used_disk_resume" in src
    assert "ran_fresh_scan" in src


def test_tv_status_branch_only_fires_on_fresh_scan():
    """The old logic always read _LAST_TV_SCAN_STATE and assigned
    fields, even on disk-resume. New logic must only use that state
    when a fresh scan actually ran."""
    src = _bot_src()
    import re
    # The elif/ran_fresh_scan check must gate the TV-state read
    block = re.search(
        r"if used_disk_resume[\s\S]{0,800}?elif ran_fresh_scan",
        src,
    )
    assert block, (
        "TV-state assignment must be gated by ran_fresh_scan "
        "(otherwise stale init values leak into status.json)"
    )


# ─── B2. Phase-77 archaeology comment ───────────────────────────────────

def test_phase_77_explanation_in_bot():
    """Future operator must see WHY this branching exists."""
    src = _bot_src()
    assert "Phase-77" in src
    # Bot must reference ChatGPT 2103 since that's the source
    assert "20260518_2103" in src


def test_phase_77_explanation_in_build_export():
    src = _export_src()
    assert "Phase-77" in src
    # Reference to the atomic-write pattern reason
    assert "atomic" in src.lower()


# ─── C. Sanity: imports still work ──────────────────────────────────────

def test_bot_still_imports_cleanly():
    import bot
    assert hasattr(bot, "Bot")
    assert hasattr(bot, "_LAST_TV_SCAN_STATE")


def test_build_export_script_still_exists():
    p = ROOT / "06_live_bot" / "build_export.ps1"
    assert p.exists()
