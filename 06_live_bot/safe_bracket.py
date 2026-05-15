"""Safe-Bracket-Buy mit Liquidity-Check + Post-Fill-Validation.

Bug-Fix-V2 für 2026-05-12 14:00 ET (HSPT/ATRA-Incident):
  Eigentlicher Root-Cause: snapshot.latest_trade.price ($10.55) war ein
  Stale-Print von vor Stunden. Real-Quote war bid $7.50 / ask $0.00.
  Mein Code hat $10.55 blind vertraut, Limit $10.60 platziert, gefilled
  bei $8.11 (tatsächlicher Ask). Stop $10.50 relativ zu Plan war über
  Fill → invalid.

Lösung-V2: vor jedem Order
  1. Quote prüfen (bid+ask vorhanden, spread <5%)
  2. Daily-Volume prüfen (>10 000 ist OK, <1 000 = illiquid)
  3. Limit = ask + 2 cents (NICHT last+5c)
  4. Stop = bei real-ask × 0.95
  5. Post-Fill: verify and repair wie V1
"""
from __future__ import annotations
import time
import logging
from typing import Optional

log = logging.getLogger("safe_bracket")

MIN_DAILY_VOLUME = 10_000
MAX_SPREAD_PCT = 5.0  # max spread/mid in %


def check_liquidity(snap) -> tuple[bool, str]:
    """Returns (ok, reason). Lehnt thin-Stocks ab BEFORE submit."""
    try:
        b = snap.daily_bar
        q = snap.latest_quote
        if not (b and q):
            return False, "no quote/bar"
        if b.volume < MIN_DAILY_VOLUME:
            return False, f"daily_volume={b.volume:,} < {MIN_DAILY_VOLUME:,}"
        bid = q.bid_price
        ask = q.ask_price
        if not (bid and ask) or bid <= 0 or ask <= 0:
            return False, f"no two-sided quote (bid={bid} ask={ask})"
        spread_pct = (ask - bid) / ((ask + bid) / 2) * 100
        if spread_pct > MAX_SPREAD_PCT:
            return False, f"spread {spread_pct:.1f}% > {MAX_SPREAD_PCT}%"
        return True, f"OK (vol={b.volume:,}, spread={spread_pct:.2f}%)"
    except Exception as e:
        return False, f"liquidity-check err: {e}"


def quote_based_entry(snap, slippage_cents: float = 0.02) -> dict:
    """Sinnvolle Entry/Stop/TP basierend auf REAL ASK (nicht latest_trade)."""
    ask = snap.latest_quote.ask_price
    bid = snap.latest_quote.bid_price
    entry = round(ask + slippage_cents, 2)
    stop = round(ask * 0.95, 2)          # 5 % unter ask
    tp = round(ask + 2 * (ask - stop), 2)  # 1:2 R:R
    return {
        "entry": entry, "stop": stop, "tp": tp,
        "ask": ask, "bid": bid,
        "spread": ask - bid,
    }


def safe_bracket_buy(
    tc, symbol: str, shares: int,
    entry_limit: float, stop: float, take_profit: float,
    *, wait_seconds: int = 20,
    assume_queued: bool = False,
) -> dict:
    """Submit Bracket + Post-Fill-Check + Auto-Repair wenn Stop invalid.

    Returns:
        {"order_id", "fill_price", "shares", "stop", "take_profit",
         "repaired": bool, "status": "filled"/"failed"/"timeout"/"queued"}

    Phase-24: `assume_queued=True` means the caller knows this order will
    sit on the books (market closed, or pre-RTH submission). In that
    case the function does NOT cancel the order on timeout — it returns
    status="queued" instead so the order is preserved for the eventual
    fill at the next session open.
    """
    from alpaca.trading.requests import (
        LimitOrderRequest, TakeProfitRequest, StopLossRequest,
        GetOrdersRequest,
    )
    from alpaca.trading.enums import OrderSide, TimeInForce, OrderClass, QueryOrderStatus

    # Sanity-Check: Stop muss unter Limit sein
    if stop >= entry_limit:
        log.error("invalid: stop %.2f >= entry %.2f", stop, entry_limit)
        return {"status": "failed", "reason": "stop_above_entry"}
    if take_profit <= entry_limit:
        log.warning("TP %.2f <= entry %.2f — fill may close immediately", take_profit, entry_limit)

    # 1. Submit Bracket
    try:
        o = tc.submit_order(LimitOrderRequest(
            symbol=symbol, qty=shares, side=OrderSide.BUY,
            time_in_force=TimeInForce.DAY,
            limit_price=round(entry_limit, 2),
            order_class=OrderClass.BRACKET,
            take_profit=TakeProfitRequest(limit_price=round(take_profit, 2)),
            stop_loss=StopLossRequest(stop_price=round(stop, 2)),
        ))
    except Exception as e:
        return {"status": "failed", "reason": str(e)}

    # 2. Wait for fill
    # Audit-Iter 27 (Bug SB-2/SB-6): robuste Status-Comparison + None-Safety
    def _status_is(status, target_name: str) -> bool:
        """Tolerant gegen Enum-Repr-Änderungen: prüft .value/.name/str()."""
        if status is None:
            return False
        for accessor in (
            getattr(status, "value", None),
            getattr(status, "name", None),
            str(status),
            str(status).rsplit(".", 1)[-1] if "." in str(status) else None,
        ):
            if accessor is None:
                continue
            if str(accessor).strip().upper() == target_name.upper():
                return True
        return False

    fill_price = None
    for _ in range(wait_seconds):
        time.sleep(1)
        try:
            o = tc.get_order_by_id(o.id)
            if _status_is(o.status, "FILLED"):
                fp = o.filled_avg_price
                if fp is None or float(fp) <= 0:
                    log.warning("safe_bracket: status=FILLED but filled_avg_price=%r — retry", fp)
                    continue  # API-Quirk, retry next iteration
                fill_price = float(fp)
                break
            if _status_is(o.status, "CANCELED") or _status_is(o.status, "REJECTED"):
                return {"status": "failed", "reason": str(o.status), "order_id": str(o.id)}
        except Exception as e:
            log.debug("safe_bracket poll err: %s", e)
    if fill_price is None:
        # Phase-24 (ChatGPT no-trade-day testing): if caller said
        # assume_queued=True (e.g. submitting during pre-market for RTH
        # open), do NOT cancel — leave the order in place to fill at the
        # next session open. Return status="queued" so the caller knows
        # it's intentional, not a failure.
        if assume_queued:
            log.info("safe_bracket: order %s queued for next session open",
                     o.id)
            return {"status": "queued", "order_id": str(o.id),
                    "entry_limit": round(entry_limit, 2),
                    "stop": round(stop, 2),
                    "take_profit": round(take_profit, 2),
                    "shares": shares}
        # Audit-Iter 27 (SB-6 follow-up): wenn wir auf timeout gehen UND der
        # Order noch live ist, könnte er später noch fillen → cancel attempt
        # damit kein stranded order übrig bleibt.
        try:
            tc.cancel_order_by_id(o.id)
            log.warning("safe_bracket: timeout — cancelled stale order %s", o.id)
        except Exception:
            pass
        return {"status": "timeout", "order_id": str(o.id)}

    # 3. Post-Fill-Validation: Stop < Fill?
    repaired = False
    if stop >= fill_price:
        log.warning("FILL %.4f below planned stop %.2f — repairing OCO",
                    fill_price, stop)
        repaired = True
        # Cancel any leftover Bracket-Children
        try:
            opens = tc.get_orders(filter=GetOrdersRequest(
                status=QueryOrderStatus.OPEN, symbols=[symbol], limit=20,
            ))
            for child in opens:
                try: tc.cancel_order_by_id(child.id)
                except Exception: pass
            time.sleep(2)
        except Exception:
            pass
        # Neue Stop+TP relativ zum FILL
        new_stop = round(fill_price * 0.95, 2)        # 5% under fill
        new_tp   = round(fill_price + 2 * (fill_price - new_stop), 2)  # 1:2 R:R
        try:
            tc.submit_order(LimitOrderRequest(
                symbol=symbol, qty=shares, side=OrderSide.SELL,
                time_in_force=TimeInForce.DAY,
                limit_price=new_tp,
                order_class=OrderClass.OCO,
                take_profit=TakeProfitRequest(limit_price=new_tp),
                stop_loss=StopLossRequest(stop_price=new_stop),
            ))
            stop = new_stop
            take_profit = new_tp
        except Exception as e:
            log.error("repair OCO failed: %s", e)
            return {"status": "filled_unprotected", "fill_price": fill_price,
                    "order_id": str(o.id), "repair_error": str(e)}

    return {
        "status": "filled",
        "order_id": str(o.id),
        "fill_price": fill_price,
        "shares": shares,
        "stop": stop,
        "take_profit": take_profit,
        "repaired": repaired,
    }
