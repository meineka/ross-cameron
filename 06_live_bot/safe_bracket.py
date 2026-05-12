"""Safe-Bracket-Buy mit Post-Fill-Validation.

Bug-Fix für 2026-05-12 14:00 ET:
  Manuelle BUY-Orders mit BRACKET wurden bei illiquiden Stocks (HSPT, ATRA)
  weit unter Limit gefilled. Stop war relativ zum Limit berechnet → landete
  ÜBER dem Fill → Alpaca rejected den Stop-Child → Position ungeschützt.

Lösung: nach Fill prüfen ob Stop < Fill. Wenn nicht: Bracket-Children
canceln und neue OCO-Protection mit Stop relativ zum Fill submitten.
"""
from __future__ import annotations
import time
import logging
from typing import Optional

log = logging.getLogger("safe_bracket")


def safe_bracket_buy(
    tc, symbol: str, shares: int,
    entry_limit: float, stop: float, take_profit: float,
    *, wait_seconds: int = 20,
) -> dict:
    """Submit Bracket + Post-Fill-Check + Auto-Repair wenn Stop invalid.

    Returns:
        {"order_id", "fill_price", "shares", "stop", "take_profit",
         "repaired": bool, "status": "filled"/"failed"/"timeout"}
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
    fill_price = None
    for _ in range(wait_seconds):
        time.sleep(1)
        try:
            o = tc.get_order_by_id(o.id)
            if str(o.status) in ("OrderStatus.FILLED", "filled"):
                fill_price = float(o.filled_avg_price)
                break
            if str(o.status) in ("OrderStatus.CANCELED", "OrderStatus.REJECTED",
                                 "canceled", "rejected"):
                return {"status": "failed", "reason": str(o.status), "order_id": str(o.id)}
        except Exception:
            pass
    if fill_price is None:
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
