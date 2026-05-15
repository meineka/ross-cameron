"""force_trade_loop.py — Phase-45 user demo mode (2026-05-15).

USER REQUEST: "mach marktschluss filter temporär weg, lass traden alles
was bullflag kriegt, auch reduziertes bullflag, ich will trades sehen.
erlaube trading, jede 5 minuten trade die top 3 in die richtung des
trends. keine sonstigen filter temporär und ich will pushes auf dem
handy dass getradet wurde."

PURPOSE: bypass ALL Cameron-strategy filters (market-hours, pattern
detection, RVOL, gap, MACD, VWAP, etc.) and just BUY the top-3 symbols
from watchlist_today.json every 5 minutes. Sends ntfy push on every
fill via the existing alerter pipeline (Phase-30/36/37/40).

NOT TRADING LOGIC — pure demonstration that the trade-push wiring
works end-to-end. Designed to be run as a SEPARATE process alongside
the main bot daemon (which stays in its normal sleep-until-Monday
mode). When the user wants the real strategy back, just stop this
script — bot.py is untouched.

Usage:
  python force_trade_loop.py
  python force_trade_loop.py --shares 1
  python force_trade_loop.py --interval-sec 300 --shares 1
"""
from __future__ import annotations
import argparse
import json
import logging
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
log = logging.getLogger("force-trade")

WATCHLIST_FILE = HERE / "watchlist_today.json"


def load_top_symbols(n: int = 3) -> list[str]:
    """Pull top-N symbols from watchlist_today.json (sorted by score)."""
    if not WATCHLIST_FILE.exists():
        log.warning("watchlist_today.json missing — nothing to trade")
        return []
    try:
        with open(WATCHLIST_FILE, encoding="utf-8") as f:
            data = json.load(f)
        symbols = data.get("symbols", [])
        scores = data.get("scores", {})
        ranked = sorted(symbols, key=lambda s: scores.get(s, 0.0), reverse=True)
        return ranked[:n]
    except Exception as e:
        log.error("failed to read watchlist: %s", e)
        return []


def get_clients():
    from secrets_loader import get_alpaca_keys
    from alpaca.trading.client import TradingClient
    from alpaca.data.historical import StockHistoricalDataClient
    k, s = get_alpaca_keys()
    return (TradingClient(k, s, paper=True),
            StockHistoricalDataClient(k, s))


def get_snapshot_price(data_client, symbol: str) -> float | None:
    """Latest trade price via REST (works after market hours via cached
    last trade). Returns None on error."""
    try:
        from alpaca.data.requests import StockSnapshotRequest
        snap = data_client.get_stock_snapshot(
            StockSnapshotRequest(symbol_or_symbols=[symbol], feed="iex"))
        sp = snap.get(symbol)
        if sp and sp.latest_trade:
            return float(sp.latest_trade.price)
    except Exception as e:
        log.debug("snapshot %s: %s", symbol, e)
    return None


STOP_LOSS_PCT = 5.0       # -5% from entry → stop
TAKE_PROFIT_PCT = 10.0    # +10% from entry → take profit (R:R 1:2)

# Phase-46: track SL/TP orders so we can push when they fill.
# Maps broker order_id -> (symbol, kind, planned_price, entry_price, shares)
# `kind` is "SL" or "TP". Populated on submit, pruned when filled/cancelled.
_pending_exits: dict[str, dict] = {}


def buy_one(trading_client, alerter, symbol: str, shares: int,
            price_hint: float | None) -> None:
    """Submit BRACKET BUY (entry + SL + TP atomic).

    Phase-46c: Alpaca's "use complex orders" error required when you
    already hold the symbol — submitting plain BUY/SELL alongside an
    existing position is rejected as wash trade. Bracket orders group
    entry+stop+TP atomically so wash-trade detection allows them.
    Bracket orders do NOT support extended_hours; outside RTH they
    queue for next regular session.
    """
    from alpaca.trading.requests import (
        LimitOrderRequest, TakeProfitRequest, StopLossRequest)
    from alpaca.trading.enums import OrderSide, TimeInForce, OrderClass
    try:
        entry = price_hint or 5.0
        # Limit ~1% above snapshot so it fills quickly (paper is generous)
        lp = round(entry * 1.01, 2)
        sl_price = round(entry * (1 - STOP_LOSS_PCT / 100), 2)
        tp_price = round(entry * (1 + TAKE_PROFIT_PCT / 100), 2)
        # Alpaca validates: stop_price must be < entry (lp), tp > entry
        if sl_price >= lp:
            sl_price = round(lp * 0.99, 2)
        if tp_price <= lp:
            tp_price = round(lp * 1.01, 2)
        req = LimitOrderRequest(
            symbol=symbol, qty=shares, side=OrderSide.BUY,
            time_in_force=TimeInForce.DAY,
            limit_price=lp,
            order_class=OrderClass.BRACKET,
            take_profit=TakeProfitRequest(limit_price=tp_price),
            stop_loss=StopLossRequest(stop_price=sl_price),
        )
        log.info("BRACKET-BUY %s qty=%d entry≤$%.2f SL=$%.2f TP=$%.2f",
                  symbol, shares, lp, sl_price, tp_price)
        o = trading_client.submit_order(req)
        log.info("FORCE-BUY %s %d submitted → order_id=%s",
                  symbol, shares, str(o.id)[:8])
        # Best-effort fill confirmation
        import time as _t
        deadline = _t.time() + 15
        fill_price = price_hint
        while _t.time() < deadline:
            _t.sleep(1)
            try:
                refreshed = trading_client.get_order_by_id(o.id)
                status = str(getattr(refreshed.status, "value", refreshed.status)).upper()
                if status.endswith("FILLED"):
                    fp = getattr(refreshed, "filled_avg_price", None)
                    if fp:
                        try:
                            fill_price = float(fp)
                        except Exception:
                            pass
                    break
                if status.endswith("REJECTED") or status.endswith("CANCELED"):
                    log.warning("FORCE-BUY %s %s — broker rejected", symbol, status)
                    if alerter is not None:
                        alerter.send("warn",
                                      f"FORCE-BUY {symbol} REJECTED",
                                      body=f"broker said {status}",
                                      force=True)
                    return
            except Exception:
                pass
        # Bracket parent submitted. Refresh to get child leg IDs +
        # track them in _pending_exits for fill notification.
        actual_price = fill_price or lp
        sl_amount = round((actual_price - sl_price) * shares, 2)
        tp_amount = round((tp_price - actual_price) * shares, 2)
        try:
            parent = trading_client.get_order_by_id(o.id)
            for leg in (getattr(parent, "legs", None) or []):
                leg_type = str(getattr(leg.type, "value", leg.type)).upper()
                leg_id = str(leg.id)
                if "LIMIT" in leg_type:
                    _pending_exits[leg_id] = {
                        "symbol": symbol, "kind": "TP",
                        "planned_price": tp_price, "entry_price": actual_price,
                        "shares": shares,
                    }
                elif "STOP" in leg_type:
                    _pending_exits[leg_id] = {
                        "symbol": symbol, "kind": "SL",
                        "planned_price": sl_price, "entry_price": actual_price,
                        "shares": shares,
                    }
        except Exception as e:
            log.debug("bracket-leg lookup failed for %s: %s", symbol, e)
        # Push with full details
        if alerter is not None:
            try:
                price_str = f"${actual_price:.2f}"
                title = f"BUY {symbol} {shares} @ {price_str}"
                lines = [
                    f"SL: ${sl_price:.2f} (risk -${sl_amount:.2f})",
                    f"TP: ${tp_price:.2f} (reward +${tp_amount:.2f})",
                ]
                if sl_amount and tp_amount:
                    rr = tp_amount / sl_amount
                    lines.append(f"R:R 1:{rr:.1f}")
                body = "\n".join(lines)
                alerter.send("info", title, body=body, force=True)
            except Exception as e:
                log.debug("push failed: %s", e)
    except Exception as e:
        log.error("FORCE-BUY %s err: %s", symbol, e)
        if alerter is not None:
            try:
                alerter.send("error",
                              f"FORCE-BUY {symbol} ERROR",
                              body=str(e)[:200], force=True)
            except Exception:
                pass


def check_exit_fills(trading_client, alerter) -> None:
    """Phase-46: poll Alpaca for any pending SL/TP order that has filled
    since last check. On fill, push a notification with actual fill
    price + P&L vs entry, and prune from _pending_exits.

    Also detects when a sibling order (e.g. TP filled => SL is now
    orphaned for an already-flat position) needs cancelling: we cancel
    the orphan so we don't accidentally re-enter a short.
    """
    if not _pending_exits:
        return
    # Snapshot current set so we can prune during iteration safely
    pending = list(_pending_exits.items())
    by_symbol: dict[str, list[str]] = {}
    for oid, meta in pending:
        by_symbol.setdefault(meta["symbol"], []).append(oid)
    for oid, meta in pending:
        try:
            order = trading_client.get_order_by_id(oid)
        except Exception as e:
            log.debug("get_order %s: %s", oid[:8], e)
            continue
        status = str(getattr(order.status, "value", order.status)).strip().upper()
        if status.endswith("FILLED"):
            try:
                fp = float(getattr(order, "filled_avg_price", None) or
                             meta["planned_price"])
            except Exception:
                fp = meta["planned_price"]
            entry = meta["entry_price"]
            shares = meta["shares"]
            pnl = (fp - entry) * shares
            sign = "+" if pnl >= 0 else ""
            kind = meta["kind"]
            symbol = meta["symbol"]
            log.info("EXIT-FILL %s %s %d @ $%.4f (entry $%.2f, PnL %s$%.2f)",
                      kind, symbol, shares, fp, entry, sign, pnl)
            if alerter is not None:
                try:
                    # Phase-48 (user request 2026-05-15): visual color marker
                    # in the push title so user can glance-distinguish profit
                    # vs loss exits without reading. TP-FILL = green dot +
                    # up-triangle; SL-FILL = orange dot + down-triangle.
                    # (Unicode emoji "green/orange triangle" doesn't exist;
                    # this is the closest standard-render approximation.)
                    if kind == "TP":
                        marker = "🟢▲"
                    elif kind == "SL":
                        marker = "🟠▼"
                    else:
                        marker = ""
                    title = f"{marker} {kind}-FILL {symbol} {shares} @ ${fp:.2f}".lstrip()
                    body = (f"Entry: ${entry:.2f}\n"
                            f"Exit:  ${fp:.2f}\n"
                            f"PnL: {sign}${pnl:.2f}")
                    level = "info" if pnl >= 0 else "warn"
                    alerter.send(level, title, body=body, force=True)
                except Exception as e:
                    log.debug("exit push failed: %s", e)
            # Cancel the sibling (orphan) if any
            for sibling_oid in by_symbol.get(meta["symbol"], []):
                if sibling_oid == oid:
                    continue
                if sibling_oid in _pending_exits:
                    try:
                        trading_client.cancel_order_by_id(sibling_oid)
                        log.info("  cancelled sibling %s id=%s",
                                  _pending_exits[sibling_oid]["kind"],
                                  sibling_oid[:8])
                    except Exception:
                        pass
                    _pending_exits.pop(sibling_oid, None)
            _pending_exits.pop(oid, None)
        elif status in ("CANCELED", "EXPIRED", "REJECTED"):
            log.info("EXIT %s %s gone (status=%s)",
                      meta["kind"], meta["symbol"], status)
            _pending_exits.pop(oid, None)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--shares", type=int, default=1,
                    help="shares per BUY (default 1)")
    ap.add_argument("--top-n", type=int, default=3,
                    help="how many top symbols to buy per tick (default 3)")
    ap.add_argument("--interval-sec", type=int, default=300,
                    help="seconds between ticks (default 300 = 5 min)")
    args = ap.parse_args()

    try:
        trading, data = get_clients()
    except Exception as e:
        log.error("client init failed: %s — aborting", e)
        return 1
    try:
        eq = float(trading.get_account().equity)
        log.info("FORCE-TRADE MODE START — equity $%.2f, shares=%d, "
                 "top-N=%d, interval=%ds",
                 eq, args.shares, args.top_n, args.interval_sec)
    except Exception as e:
        log.warning("account check failed: %s — continuing anyway", e)
    # Phase-46: on startup, recover existing open SELL orders into the
    # _pending_exits dict so wash-trade filtering + fill-tracking carry
    # over across restarts.
    try:
        from alpaca.trading.requests import GetOrdersRequest
        from alpaca.trading.enums import QueryOrderStatus, OrderSide
        opens = trading.get_orders(filter=GetOrdersRequest(
            status=QueryOrderStatus.OPEN, limit=200))
        for o in opens:
            side = str(getattr(o.side, "value", o.side)).strip().upper()
            if not side.endswith("SELL"):
                continue
            # Identify SL vs TP by order type
            otype = str(getattr(o.type, "value", o.type)).strip().upper()
            if "STOP" in otype:
                kind = "SL"
                planned_price = float(o.stop_price or 0)
            elif "LIMIT" in otype:
                kind = "TP"
                planned_price = float(o.limit_price or 0)
            else:
                continue
            qty = int(float(o.qty))
            # Entry price unknown across restart — try to recover from
            # current position; fall back to planned_price.
            entry = planned_price
            try:
                pos = trading.get_open_position(o.symbol)
                entry = float(pos.avg_entry_price)
            except Exception:
                pass
            _pending_exits[str(o.id)] = {
                "symbol": o.symbol, "kind": kind,
                "planned_price": planned_price, "entry_price": entry,
                "shares": qty,
            }
        if _pending_exits:
            log.info("Recovered %d open SL/TP orders into _pending_exits",
                     len(_pending_exits))
    except Exception as e:
        log.warning("could not recover open orders: %s", e)
    try:
        from alerter import make_alerter
        alerter = make_alerter()
        if alerter is not None:
            alerter.send("info", "Force-Trade Mode active",
                          body=f"Top-{args.top_n} of watchlist every "
                                f"{args.interval_sec}s. {args.shares} sh per buy.",
                          force=True)
    except Exception as e:
        log.warning("alerter init failed: %s — no pushes", e)
        alerter = None

    tick = 0
    EXIT_POLL_SUB_INTERVAL_SEC = 20  # check SL/TP fills every 20s within a tick
    try:
        while True:
            tick += 1
            log.info("=" * 60)
            log.info("TICK #%d", tick)
            # Poll exit fills first so SL/TP pushes are timely
            check_exit_fills(trading, alerter)
            # Phase-46c (user-fix): bracket orders are "complex orders"
            # Alpaca accepts even for symbols already held — no need to
            # filter. Just buy the top-N every tick. Pyramiding allowed.
            symbols = load_top_symbols(n=args.top_n)
            if not symbols:
                log.warning("no symbols in watchlist — sleeping")
            else:
                log.info("buying top-%d (bracket, complex order): %s",
                         len(symbols), symbols)
                for sym in symbols:
                    price = get_snapshot_price(data, sym)
                    buy_one(trading, alerter, sym, args.shares, price)
            log.info("sleeping %ds until next tick (exit-poll every %ds)",
                     args.interval_sec, EXIT_POLL_SUB_INTERVAL_SEC)
            # Sleep in sub-intervals + poll for exit fills, so SL/TP
            # pushes don't wait the full 5 min after fill.
            elapsed = 0
            while elapsed < args.interval_sec:
                step = min(EXIT_POLL_SUB_INTERVAL_SEC,
                            args.interval_sec - elapsed)
                time.sleep(step)
                elapsed += step
                check_exit_fills(trading, alerter)
    except KeyboardInterrupt:
        log.info("KeyboardInterrupt — exiting force-trade loop")
        if alerter is not None:
            try:
                alerter.send("info", "Force-Trade Mode stopped",
                              body="Ctrl+C received", force=True)
            except Exception:
                pass
        return 0


if __name__ == "__main__":
    sys.exit(main())
