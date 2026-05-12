"""manage_position Audit-Iteration 4 — State-Machine-Bugs."""
from __future__ import annotations
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "06_live_bot"))


# ─── Bug K: 1-Share-Trade darf bei T1 nicht stuck bleiben ────────────────────
def test_t1_skipped_for_one_share_position():
    """Bei ts.shares == 1: T1 darf nicht versuchen, half=1 zu verkaufen
    weil ts.shares -= 1 = 0 → position stuck."""
    src = (ROOT / "06_live_bot" / "bot.py").read_text(encoding="utf-8")
    # T1-Check sollte mind. 2 Shares verlangen
    assert "ts.shares >= 2" in src, "T1 muss mind. 2 Shares fordern"


def test_t2_works_for_one_share_without_t1():
    """1-Share-Trade muss T2 ohne vorheriges T1 erreichen können."""
    src = (ROOT / "06_live_bot" / "bot.py").read_text(encoding="utf-8")
    # Locate T2-block via comment markers
    t2_start = src.find("# T2 — Audit-Iter 4")
    assert t2_start > 0, "T2-block marker fehlt"
    t2_block = src[t2_start:t2_start + 800]
    # T2-Trigger soll nicht mehr nur via half_filled gehen
    assert "ts.shares > 0" in t2_block, \
        "T2 muss auch ohne half_filled triggern können"


# ─── Bug P: MACD-Exit muss consecutive_losses bei Win resetten ───────────────
def test_macd_exit_win_resets_consecutive_losses():
    """Wenn MACD-Bear-Cross mit pnl > 0 → losses-Counter auf 0."""
    src = (ROOT / "06_live_bot" / "bot.py").read_text(encoding="utf-8")
    # Find MACD-Exit block (from comment to next blank-line-ish)
    macd_start = src.find("Cameron MACD-Exit: bei bear-cross")
    assert macd_start > 0
    macd_end = src.find("# #2 30¢-Quick-Exit", macd_start)
    macd_block = src[macd_start:macd_end]
    # Reset-Pfad mit else-Branch
    assert "self.day.consecutive_losses = 0" in macd_block, \
        f"MACD-Win soll consecutive_losses resetten. Block:\n{macd_block[-400:]}"


# ─── Smoke: bot.py importiert + DayState struktur stimmt ─────────────────────
# ─── Bug T: Watchdog darf keine hardcoded API-Keys mehr haben ────────────────
def test_watchdog_has_no_hardcoded_secrets():
    src = (ROOT / "06_live_bot" / "watchdog.py").read_text(encoding="utf-8")
    assert "PKBERNOMU" not in src, "Watchdog hat noch hardcoded API-Key"
    assert "FZBBx9v8" not in src, "Watchdog hat noch hardcoded Secret"
    assert "secrets_loader" in src, "Watchdog soll secrets_loader benutzen"


# ─── Bug U: Watchdog Trade-Lock-Check ────────────────────────────────────────
def test_watchdog_has_trade_lock_check():
    """Watchdog darf nicht blind restarten wenn offene Positions sind."""
    src = (ROOT / "06_live_bot" / "watchdog.py").read_text(encoding="utf-8")
    assert "get_all_positions" in src
    assert "BLOCKED restart" in src or "positions open" in src.lower()


def test_bot_imports_after_fixes():
    import bot
    assert hasattr(bot, "compute_position_size")
    assert hasattr(bot, "detect_bull_flag")
    assert hasattr(bot, "Bot")
    d = bot.DayState()
    assert d.consecutive_losses == 0
    assert d.spiral_locked is False
