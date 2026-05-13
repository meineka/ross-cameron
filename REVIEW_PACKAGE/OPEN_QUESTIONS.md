# Open Questions for Reviewer

These are unresolved items where another agent's perspective would help.

## 1. The threshold-tune decision (highest leverage)

**Question:** Should we change `POLE_TOPPING_TAIL_MAX` from `0.4` to `0.5`?

**Backtest signals:**
- +$114.67 PnL on 39-day pilot (+153%)
- +5 trades (17→22)
- +4% win-rate (67%→71%)
- Same max drawdown ($24.72)
- Avg PnL/trade nearly doubles ($4.42 → $8.63)

**Cameron-source check:**
- Cameron's video commentary: "topping tail above 50% is concerning"
- Original 0.4 was a conservative estimate, not from Cameron's literal spec

**Risk:**
- 39-day sample is small (~8 weeks)
- Could be overfitting to one regime

**Live evidence:**
- 2026-05-13 BWEN had a textbook bull flag (pole 6.98%, retrace 10%, breakout
  confirmed, VWAP+MACD+FBO all clean) but blocked at topping_max=0.605 vs 0.4.
- Bar showed clear momentum continuation post-block.

**Decision needed:** Apply or hold?

## 2. Live bot didn't trade today (2026-05-13) but pilot bot does

Production config produced 0 trades through 2 separate root causes:
1. GitHub Actions cron didn't fire (top-of-hour contention) — fixed: cron
   moved to `47 9 * * 1-5`.
2. Even if it had: 1-Min/5-Min bar timeframe mismatch (now fixed via
   `bar_aggregator.py`).

**Question:** Are there more "the bot can't even start working" bugs we haven't
hit yet? What's a reviewer's hypothesis for which scenario triggers next?

Specific concerns:
- Cloud restart mid-trading: position recovery + state-load flow has been
  fixed but not live-verified
- yfinance rate-limit handling: cache + retry exists but real outage
  behavior unproven
- Alpaca paper-to-live mode-switch: code is `paper=True` everywhere; ensure
  no leakage when user flips to live

## 3. Replay-Engine simplification gap

`ReplayBot._manage()` has the same T1/T2/stop accounting as live but is
missing:
- Quick-exit (30¢ adverse in 5 bars)
- MACD-bearish-cross exit
- Pyramid adds
- VWAP/MACD/FBO entry vetos (only applied via detect_bull_flag, not in
  replay path)

**Question:** Should we bring ReplayBot to full parity with live? That would
make backtest results more representative but introduces risk that backtest
"truths" change. The replay-regression-test baseline ($13.14) is a sanity
contract — changing the engine breaks it.

**Trade-off:** simpler ReplayBot = faster, smaller test surface; full-parity =
realistic but coupled to live-bot-internals.

## 4. Should Cameron's VWAP include premarket?

Currently `vwap_filter.session_vwap()` aggregates over all bars passed to it.
Bot's rolling bar window includes premarket data (subscribed since 12:27 CEST
= 06:27 ET) AND RTH bars. So our "session-VWAP" mixes premarket + RTH.

Cameron's standard VWAP charts reset at 9:30 ET. We don't.

**Question:** Is mixing premarket+RTH VWAP a meaningful divergence? Some
Cameron-style traders include premarket; some don't.

## 5. Why are RSI > 80 setups rejected?

`false_breakout_veto` rule 4: `if r > 80: return True, "rsi_overbought_80"`.

But Cameron's small-cap movers ROUTINELY trade at RSI 80+ during morning
sessions because the entire stock is parabolic. Today's BWEN was rejected
partly because RSI was 85.

**Question:** Is the FBO veto correctly screening false-breakouts, or is it
over-filtering genuine Cameron setups in their natural high-RSI environment?

Possible refinement: weight RSI veto with topping-tail and rejection-close,
not pure hard-veto.

## 6. The 2-loss spiral lock vs Cameron's actual rule

Bot's `spiral_locked` triggers after 2 consecutive losses → no more trades
that day.

Cameron's actual rule (from his stream): "If I lose 2 in a row, I step away
for 30 minutes to reset, then evaluate." Not a hard lock.

**Question:** Is the hard lock too strict? Soft lock with re-eval after N
bars might capture more recovery opportunities.

## 7. SPY-Reduce-Day handling

`spy_size_multiplier` set at premarket. If SPY changes intraday (e.g. opens
+0.3% then turns -1% by 11:00), the multiplier stays at the morning value.

**Question:** Should the bot recompute SPY multiplier intraday? Or is the
morning-set value sufficient because Cameron's setups happen mostly in
Power Hour anyway?

## 8. Pump-Dump-Filter heuristic

Currently:
- score > 10,000 → 25% size (one rule)
- OR (intraday > 100% AND rvol > 50×) → 25% size (combo rule)

Both are heuristics from Cameron's $17k ODYS loss post-mortem. But:
- Are there other pump-dump signals we should add? (Pre-market gap > 100%
  often signals pump-and-dump)
- Is 25% the right size-multiplier or should it be 0 (skip entirely)?

## 9. What's missing from the test suite?

We have ~500 tests covering:
- Math correctness (RSI, VWAP, MACD edge cases)
- Filter semantics (size compute, can_enter_new)
- State transitions (T1/T2/stop, manage_position)
- Robustness (corrupt JSON, missing keys, division by zero)
- Wiring (source-grep regressions)

**Question:** What test class is missing? Reviewer's perspective on coverage
gaps would help.

## 10. Should we ship live without more diligence?

User is on paper trading and has not yet flipped to live. Current state:
- 35+ bugs found and fixed in past 48 hours
- 501 tests passing
- Backtest shows positive expectancy but small absolute returns
- Live deployment has had 2 silent-failure days (2026-05-11 WS bug; 2026-05-13
  no-trades-due-to-cron+timeframe)

**Question:** What additional checks would a reviewer want before flipping
to live trading?

Candidates I considered:
- Run paper for 30 trading days uninterrupted with daily logs
- Implement strict-mode VWAP+catalyst (currently permissive defaults)
- Add Telegram/Discord webhook for instant trade notifications
- Live A/B period with `higher-topping` config

## How to respond

Please structure feedback as:
1. **Critical bugs** (CRITICAL/HIGH severity, reproducible)
2. **Defensive gaps** (could happen but unverified)
3. **Strategy fidelity questions** (does Cameron actually do X?)
4. **Test-coverage gaps** (where could a future bug land silently?)
5. **Recommendation on each Open Question above**

Concrete > generic. "Add try/except here because xyz" > "improve error handling".
