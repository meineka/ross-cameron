# Ross Cameron Strategy — Original Rules

Source: Cameron's Warrior Trading curriculum + his daily YouTube recaps.
These are the rules the bot tries to implement faithfully.

## The 5 Pillars (Premarket Stock Selection)

A stock must satisfy ALL FIVE to be on the watchlist:

| # | Pillar | Threshold | Constant |
|---|--------|-----------|----------|
| 1 | Price | $2.00 - $20.00 | `PRICE_MIN`, `PRICE_MAX` |
| 2 | Float | < 10 million shares | `FLOAT_MAX_SHARES` |
| 3 | Relative Volume | ≥ 5× average | `RVOL_MIN_PROXY` |
| 4 | Intraday gain | ≥ 10% from prior close | `DAILY_GAIN_MIN_PCT` |
| 5 | News catalyst | Recent positive news | catalyst_filter.py |

Rationale: small float + high volume + news + price-in-range = parabolic
move potential ("Whales in a small pond").

## The Bull-Flag Pattern (5-min bars)

Cameron's primary setup. Detected by `detect_bull_flag()` in `bot.py`.

### Pole (the strong move up)
- 3-7 consecutive **green** bars
- Minimum cumulative move: **5%** (`POLE_MIN_MOVE_PCT`)
- No bar has upper-wick > 40% of range (`POLE_TOPPING_TAIL_MAX`)
- Volume in second half ≥ first half × 0.9 (rising-volume confirmation)

### Flag (the consolidation pullback)
- 1-3 bars after the pole
- Flag low must NOT retrace more than 50% of pole height
  (`FLAG_RETRACE_MAX_PCT`)

### Breakout (the entry trigger)
- Current bar must be **green**
- Current high > max-high of flag bars
- Volume ≥ 1.5× SMA20 (`BREAKOUT_VOL_FACTOR`)
- Price in $2-$20 range

### Entry, Stop, Targets
- **Entry:** flag-high + slippage_cents
- **Stop:** flag-low - slippage_cents
- **T1:** entry + (entry - stop) — sell half at 1R
- **T2:** entry + 2 × pole_height OR next 0.50/1.00 psych level, whichever higher

## Veto Filters (anti-False-Breakout)

Applied AFTER pattern detection. Any single veto rejects the entry.

### VWAP Filter
- Cameron only trades ABOVE the session VWAP
- Implemented in `vwap_filter.py`

### MACD Filter (12/26/9)
- MACD-line must be > Signal-line AND > 0
- "Don't fight the MACD"

### FBO 5-Indicator (False-Breakout Veto)
1. Topping-tail > 50% of breakout-bar range → veto
2. Volume < 1.5× SMA20 (redundant safety) → veto
3. Close in lower-third of breakout-bar → veto
4. RSI > 80 (overbought) → veto
5. < 1 green confirmation bar in prior 2 → veto

## Risk Management (Cameron's Hard Rules)

| Rule | Threshold | Constant |
|------|-----------|----------|
| Max loss per trade | 1% of account equity OR $50 hard cap | `MAX_LOSS_PER_TRADE_USD` |
| Min stop distance | $0.05 (5¢) | hardcoded in compute_position_size |
| Max trades per day | 5 | `MAX_TRADES_PER_DAY` |
| Daily goal stop | +$150 → no new entries | `DAILY_GOAL_USD` |
| Daily max loss | -$150 → bot locks | `DAILY_MAX_LOSS_USD` |
| 2-loss spiral | After 2 consecutive losses → stop trading | spiral_locked |
| Intraday drawdown | -50% from peak PnL → stop | `INTRADAY_DRAWDOWN_PCT_OF_PROFITS` |
| Quarter-Size start | First $0.50/share cumulative gain only at quarter-size | `QUARTER_SIZE_UNLOCK_CENTS` |

## Time Rules (NY-time)

| Time (ET) | Action |
|-----------|--------|
| 06:27 | Premarket scan → watchlist |
| 09:30 | Market open, RTH starts |
| 09:35 | First entries allowed (skip opening-range chaos) |
| 11:30 | Last allowed entry — too late = no new positions |
| 12:00 | HARD_FLAT — close all positions, day done |

(In cloud-config UTC: 10:27 / 13:30 / 13:35 / 15:30 / 16:00 UTC summer time.)

## Position Management

### Add-to-Winner (Pyramiding)
- On every +10¢ above last add-price → buy 25% additional
- Max 3 adds per trade
- On first add: move stop to original entry (breakeven for full position)
- All adds at same R:R structure as initial

### T1 (50% Partial Profit)
- When high ≥ T1: sell half
- Move stop to entry (breakeven on remaining)
- This is "playing with house money"

### T2 (Final Profit)
- When high ≥ T2: sell remaining
- Reset for next setup

### Quick-Exit (Damage Control)
- If price moves 30¢ AGAINST entry in first 5 bars → exit immediately
- Cameron: "If it doesn't work, get out fast"

### MACD-Exit (Trend-Loss)
- On MACD bearish-cross during open position → exit immediately
- "The strongest signal that the move is done"

## SPY Trend Filter

- SPY +0.0% to +0.5% intraday → normal size (1.0×)
- SPY -1.0% to 0.0% → half size (0.5×) — reduce-day
- SPY < -1.0% → SKIP DAY (`spy_size_multiplier = 0`)

## Pump-Dump-Risk Reducer

After Cameron's $17,000 ODYS loss (2026-05-12 lesson):
- Score = RVOL × intraday_pct
- If score > 10,000 OR (intraday > 100% AND RVOL > 50×) → position size × 0.25

## What Cameron emphasizes (and bot implements)

1. **Selectivity:** "2-3 best setups per day, not 10"
2. **Power Hour:** 9:30-10:30 ET is prime time (full size)
3. **After Power Hour:** 75% size (Cameron switches to less aggressive)
4. **Don't fight the trend:** SPY filter + MACD filter
5. **Quick to cut losses:** 30¢ rule + MACD exit
6. **Take profits incrementally:** T1 partial + T2 final

## What Cameron emphasizes (and is harder to implement)

- **Tape reading** — bid/ask dynamics, market depth (we approximate via Alpaca
  snapshot quotes but it's not real Level-2)
- **Discretion in similar setups** — choosing the "cleaner" one (bot ranks by
  rvol × intraday_pct)
- **Sector themes** — Cameron sometimes plays multiple in same sector (bot
  doesn't group; trades each independently)
- **Psychology** — Cameron explicitly mentions "do NOT revenge trade" after
  a loss. Bot implements via the spiral-lock.
