"""Check ob PDT-Flag wirklich Trading blockiert."""
import sys, io
from pathlib import Path
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent))
from secrets_loader import get_alpaca_keys
from alpaca.trading.client import TradingClient

k, s = get_alpaca_keys()
c = TradingClient(k, s, paper=True)
a = c.get_account()
print(f"Status:                  {a.status}")
print(f"Trading-Blocked:         {a.trading_blocked}")
print(f"Account-Blocked:         {a.account_blocked}")
print(f"Trade-Suspended-by-user: {getattr(a, 'trade_suspended_by_user', '?')}")
print(f"Pattern-day-trade:       {a.pattern_day_trader}")
print(f"Daytrade-count:          {a.daytrade_count}")
print(f"Daytrading-BP:           ${float(getattr(a, 'daytrading_buying_power', 0) or 0):,.2f}")
print(f"Regular-BP:              ${float(a.buying_power):,.2f}")
print(f"Equity:                  ${float(a.equity):,.2f}")
print(f"Last-Equity:             ${float(a.last_equity):,.2f}")
print()
print("=" * 50)
print("INTERPRETATION:")
if a.trading_blocked:
    print("  ✗ TRADING BLOCKED — bot kann morgen nicht traden")
elif a.pattern_day_trader and float(a.equity) < 25000:
    print("  ✗ PDT-Flag + Equity < $25k — gesperrt für day-trading")
elif a.pattern_day_trader and float(a.equity) >= 25000:
    print("  ✓ PDT-Flag aktiv, ABER Equity ≥ $25k → unrestricted day-trading erlaubt")
else:
    print("  ✓ kein Block, alles offen")
