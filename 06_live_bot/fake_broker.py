"""FakeBroker — Order-lifecycle-aware test double for Bot.

Review-V2 P2.x: previously tests mocked AlpacaExecutor as a MagicMock,
which returned MagicMock for every method. That hides bugs where the
bot misinterprets order-state (the core problem reviewer identified).

This FakeBroker implements the same interface as AlpacaExecutor's
order-submission methods but with REAL state-machine semantics:

  - Orders go new → accepted → filled/partial/rejected/canceled/timeout
  - Position-state tracked independently from "what the bot believes"
  - Configurable behaviors per-symbol or globally (fill_at_limit, partial,
    reject, timeout, slippage, market-fallback)
  - Tests can assert on BOTH bot-internal state AND broker-truth state
    to catch divergence

Usage:
    fb = FakeBroker(default_behavior="filled_at_limit", slippage_cents=0.02)
    fb.set_behavior("HSPT", "filled_with_slip", slip_cents=2.50)  # stale-trade
    bot.executor = fb
    # ... run scenario ...
    assert fb.positions["AAA"] == 0  # broker truly flat
    assert not bot.tickers["AAA"].in_position  # bot also flat → parity
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
import logging

log = logging.getLogger("fake-broker")


@dataclass
class FakeOrder:
    order_id: str
    symbol: str
    side: str  # "BUY" / "SELL"
    qty: int
    order_type: str  # "BRACKET" / "LIMIT" / "MARKET"
    limit_price: Optional[float] = None
    stop_price: Optional[float] = None
    take_profit: Optional[float] = None
    status: str = "new"  # new | filled | partial | rejected | canceled | timeout
    filled_qty: int = 0
    avg_fill_price: float = 0.0
    parent_id: Optional[str] = None


@dataclass
class FakeBroker:
    """Stateful drop-in replacement for AlpacaExecutor in tests.

    Default behavior: every order fills at limit price (no slippage, no rejection).
    Override per-symbol via set_behavior() for scenario testing.
    """
    default_behavior: str = "filled_at_limit"
    default_slippage_cents: float = 0.0
    dry_run: bool = False

    # Per-symbol behavior override
    per_symbol_behavior: dict[str, dict] = field(default_factory=dict)

    # Broker-truth state (independent of bot.tickers)
    positions: dict[str, int] = field(default_factory=dict)  # symbol → shares
    avg_prices: dict[str, float] = field(default_factory=dict)
    orders: dict[str, FakeOrder] = field(default_factory=dict)
    bracket_children: dict[str, list[str]] = field(default_factory=dict)
    next_order_id: int = 0
    equity_value: float = 25_000.0

    # Behaviors (override per-symbol):
    #   "filled_at_limit"    → fill at limit_price exactly
    #   "filled_with_slip"   → fill at limit + slip_cents (caller specifies)
    #   "partial"            → fill partial_qty only
    #   "rejected"           → reject immediately
    #   "timeout"            → no fill, return timeout (caller calls _expire)

    def _gen_id(self, sym: str) -> str:
        self.next_order_id += 1
        return f"fakeord-{sym}-{self.next_order_id}"

    def _behavior(self, sym: str) -> dict:
        return self.per_symbol_behavior.get(sym, {"behavior": self.default_behavior})

    def set_behavior(self, symbol: str, behavior: str, **kwargs) -> None:
        """Override behavior for a symbol. kwargs: slip_cents, partial_qty, etc."""
        self.per_symbol_behavior[symbol] = {"behavior": behavior, **kwargs}

    def get_equity(self) -> float:
        return self.equity_value

    # ─── Order submission methods (mirror AlpacaExecutor interface) ────────
    def submit_bracket_buy(self, symbol: str, shares: int, entry: float,
                            stop: float, take_profit: float,
                            wait_fill_seconds: float = 20.0) -> dict:
        if stop >= entry:
            return {"status": "failed", "reason": "stop>=entry"}
        if take_profit <= entry:
            return {"status": "failed", "reason": "tp<=entry"}

        order_id = self._gen_id(symbol)
        beh = self._behavior(symbol)
        b = beh["behavior"]

        if b == "rejected":
            self.orders[order_id] = FakeOrder(order_id, symbol, "BUY", shares,
                                              "BRACKET", entry, stop, take_profit,
                                              status="rejected")
            return {"status": "rejected", "order_id": order_id}
        if b == "timeout":
            self.orders[order_id] = FakeOrder(order_id, symbol, "BUY", shares,
                                              "BRACKET", entry, stop, take_profit,
                                              status="timeout")
            return {"status": "timeout", "order_id": order_id}

        # Determine fill price
        if b == "filled_with_slip":
            slip = beh.get("slip_cents", self.default_slippage_cents)
            fill_price = round(entry + slip, 2)
        else:  # filled_at_limit
            fill_price = entry

        fill_qty = beh.get("partial_qty", shares) if b == "partial" else shares
        order = FakeOrder(order_id, symbol, "BUY", shares, "BRACKET",
                          entry, stop, take_profit, status="filled",
                          filled_qty=fill_qty, avg_fill_price=fill_price)
        self.orders[order_id] = order

        # Update broker-truth position
        self.positions[symbol] = self.positions.get(symbol, 0) + fill_qty
        prev_avg = self.avg_prices.get(symbol, 0.0)
        prev_qty = self.positions[symbol] - fill_qty
        if prev_qty > 0:
            self.avg_prices[symbol] = (prev_avg * prev_qty + fill_price * fill_qty) / (prev_qty + fill_qty)
        else:
            self.avg_prices[symbol] = fill_price

        # Create bracket-children (stop + tp orders)
        stop_id = self._gen_id(symbol)
        tp_id = self._gen_id(symbol)
        self.orders[stop_id] = FakeOrder(stop_id, symbol, "SELL", fill_qty,
                                          "STOP", limit_price=None,
                                          stop_price=stop, parent_id=order_id,
                                          status="new")
        self.orders[tp_id] = FakeOrder(tp_id, symbol, "SELL", fill_qty,
                                        "LIMIT", limit_price=take_profit,
                                        parent_id=order_id, status="new")
        self.bracket_children[order_id] = [stop_id, tp_id]

        return {"status": "filled", "order_id": order_id,
                "fill_price": fill_price, "shares": fill_qty}

    def submit_sell_with_confirm(self, symbol: str, shares: int, price: float,
                                  reason: str, wait_fill_seconds: float = 8.0,
                                  market_fallback: bool = True) -> dict:
        order_id = self._gen_id(symbol)
        beh = self._behavior(symbol)
        b = beh["behavior"]

        # First, cancel any bracket-children for this symbol (mirror real exec)
        for parent_id, child_ids in list(self.bracket_children.items()):
            parent = self.orders.get(parent_id)
            if parent and parent.symbol == symbol:
                for cid in child_ids:
                    if cid in self.orders and self.orders[cid].status == "new":
                        self.orders[cid].status = "canceled"
                del self.bracket_children[parent_id]

        if b == "rejected":
            self.orders[order_id] = FakeOrder(order_id, symbol, "SELL", shares,
                                              "LIMIT", price, status="rejected")
            return {"status": "rejected", "filled_qty": 0, "order_id": order_id}

        if b == "timeout":
            if market_fallback:
                # Market-fallback fills at price (or slip)
                mkt_id = self._gen_id(symbol)
                slip = beh.get("market_slip_cents", 0.05)
                mkt_price = round(price - slip, 2)
                fill_qty = min(shares, self.positions.get(symbol, 0))
                self.orders[mkt_id] = FakeOrder(mkt_id, symbol, "SELL", shares,
                                                "MARKET", status="filled",
                                                filled_qty=fill_qty,
                                                avg_fill_price=mkt_price)
                self.positions[symbol] = max(0, self.positions.get(symbol, 0) - fill_qty)
                return {"status": "timeout_market_filled",
                        "filled_qty": fill_qty, "avg_fill_price": mkt_price,
                        "remaining_qty": shares - fill_qty,
                        "order_id": order_id, "market_order_id": mkt_id}
            self.orders[order_id] = FakeOrder(order_id, symbol, "SELL", shares,
                                              "LIMIT", price, status="timeout")
            return {"status": "timeout", "filled_qty": 0, "remaining_qty": shares,
                    "order_id": order_id}

        # Fill (full or partial)
        fill_qty = beh.get("partial_qty", shares) if b == "partial" else shares
        fill_qty = min(fill_qty, self.positions.get(symbol, 0))  # can't sell more than own
        if b == "filled_with_slip":
            slip = beh.get("slip_cents", self.default_slippage_cents)
            fill_price = round(price - slip, 2)
        else:
            fill_price = price

        self.orders[order_id] = FakeOrder(order_id, symbol, "SELL", shares,
                                          "LIMIT", price, status="filled" if fill_qty == shares else "partial",
                                          filled_qty=fill_qty, avg_fill_price=fill_price)
        self.positions[symbol] = max(0, self.positions.get(symbol, 0) - fill_qty)

        result_status = "filled" if fill_qty == shares else "partial"
        return {"status": result_status, "filled_qty": fill_qty,
                "avg_fill_price": fill_price, "remaining_qty": shares - fill_qty,
                "order_id": order_id}

    def submit_buy_with_confirm(self, symbol: str, shares: int, price: float,
                                 wait_fill_seconds: float = 8.0) -> dict:
        order_id = self._gen_id(symbol)
        beh = self._behavior(symbol)
        b = beh["behavior"]

        if b == "rejected":
            self.orders[order_id] = FakeOrder(order_id, symbol, "BUY", shares,
                                              "LIMIT", price, status="rejected")
            return {"status": "rejected", "filled_qty": 0, "order_id": order_id}
        if b == "timeout":
            self.orders[order_id] = FakeOrder(order_id, symbol, "BUY", shares,
                                              "LIMIT", price, status="timeout")
            return {"status": "timeout", "filled_qty": 0, "remaining_qty": shares,
                    "order_id": order_id}

        fill_qty = beh.get("partial_qty", shares) if b == "partial" else shares
        if b == "filled_with_slip":
            slip = beh.get("slip_cents", self.default_slippage_cents)
            fill_price = round(price + slip, 2)
        else:
            fill_price = price

        self.orders[order_id] = FakeOrder(order_id, symbol, "BUY", shares,
                                          "LIMIT", price, status="filled" if fill_qty == shares else "partial",
                                          filled_qty=fill_qty, avg_fill_price=fill_price)
        # Update position (add to existing)
        prev_qty = self.positions.get(symbol, 0)
        prev_avg = self.avg_prices.get(symbol, 0.0)
        new_qty = prev_qty + fill_qty
        if new_qty > 0:
            self.avg_prices[symbol] = (prev_avg * prev_qty + fill_price * fill_qty) / new_qty
        self.positions[symbol] = new_qty

        result_status = "filled" if fill_qty == shares else "partial"
        return {"status": result_status, "filled_qty": fill_qty,
                "avg_fill_price": fill_price, "remaining_qty": shares - fill_qty,
                "order_id": order_id}

    # ─── Protection/repair stubs ───────────────────────────────────────────
    def verify_and_repair_protection(self, symbol: str, fill_price: float,
                                      planned_stop: float, planned_tp: float,
                                      shares: int) -> bool:
        """Returns True if repair was needed (planned_stop above actual fill)."""
        if planned_stop >= fill_price:
            log.info("FakeBroker: REPAIR %s (stop %.2f >= fill %.4f)",
                     symbol, planned_stop, fill_price)
            # In real-broker we'd cancel + re-submit bracket-children with valid stop
            return True
        return False

    def protect_position(self, symbol: str, shares: int, stop: float,
                          take_profit: float) -> None:
        """No-op for tests — but adds new bracket-children-tracking."""
        stop_id = self._gen_id(symbol)
        tp_id = self._gen_id(symbol)
        self.orders[stop_id] = FakeOrder(stop_id, symbol, "SELL", shares,
                                          "STOP", stop_price=stop, status="new")
        self.orders[tp_id] = FakeOrder(tp_id, symbol, "SELL", shares,
                                        "LIMIT", limit_price=take_profit,
                                        status="new")

    def cancel_open_orders_for(self, symbol: str) -> None:
        for o in self.orders.values():
            if o.symbol == symbol and o.status == "new":
                o.status = "canceled"

    # ─── Position assertions (for tests) ──────────────────────────────────
    def is_flat(self, symbol: str) -> bool:
        return self.positions.get(symbol, 0) == 0

    def total_position(self, symbol: str) -> int:
        return self.positions.get(symbol, 0)

    def open_brackets_for(self, symbol: str) -> list[FakeOrder]:
        return [o for o in self.orders.values()
                if o.symbol == symbol and o.status == "new"]
