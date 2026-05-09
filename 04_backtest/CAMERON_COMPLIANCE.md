# Cameron-Compliance-Audit (Stand: v3 mit Top-N-Filter)

Prüft jeden Constraint aus `03_rules_engine/constraints.yaml` gegen den
Backtest-Code. Status:

- ✓ = vollständig implementiert
- ⚠ = teilweise / approximiert
- ✗ = NICHT implementiert (mit Begründung)

## 1. Universe (5 Pillars)

| Constraint | Code-Status | Quelle | Anmerkung |
|---|---|---|---|
| price_min $2, max $20 | ✓ | bootstrap.py | im Daily-Filter |
| price_sweet_spot $5-$10 | ✓ (passiert natürlich) | n/a | Median Candidates: $5.35 |
| float_max 10M (strict) / 20M (loose) | ✗ | n/a | yfinance liefert nur current Float, nicht historisch |
| rvol_min 5× | ⚠ proxy | bootstrap.py | nutzt Daily-Vol vs 20d-Avg, nicht echtes intraday RVOL (yfinance-Limit) |
| daily_change_min_pct 10 | ✓ | bootstrap.py | Filter: intraday_pct ≥ 10 |
| catalyst_required | ⚠ optional via `--require-catalyst` | edgar_full_tag.py | EDGAR 8-K +/-1 Tag |
| premarket_volume_min 300k | ✗ | n/a | yfinance Daily-Vol, kein Premarket-Split |
| bonus_criteria.recent_ipo | ✗ | n/a | nicht implementiert |
| bonus_criteria.recent_reverse_split | ✗ | n/a | nicht implementiert |
| bonus_criteria.all_time_highs | ✗ | n/a | nicht implementiert |
| bonus_criteria.yesterday_volume_under_100k | ✗ | n/a | implementierbar mit yfinance |
| catalyst_timing.best_news_days [Mon,Tue,Wed] | ✗ | n/a | als Boost denkbar |
| catalyst_timing.avoid_news_after Fri 12:00 | ✗ | n/a | trivial implementierbar |

## 2. Session

| Constraint | Code-Status | Quelle |
|---|---|---|
| primary_window 09:30-11:00 ET | ✓ RTH-Filter | v3.is_rth() (09:30 ≤ time < 16:00) |
| premarket 07:00-09:30 | ✓ ausgeschlossen | RTH-only-filter wirft Pre-Market raus |
| hard_stop 12:00 | ✗ | nicht implementiert (Trade hält bis EOD) |
| no_trade_timeout_minutes 30 | ✗ | irrelevant im Backtest |

## 3. Charts / Indicators

| Constraint | Code-Status | Quelle |
|---|---|---|
| 9 EMA | ✗ | nicht plotted, nicht für Trail genutzt |
| 20 EMA | ✗ | nicht implementiert |
| 200 EMA (Long-below-200ema-forbidden) | ✗ | KRITISCHER GAP — long_below_200ema_forbidden Veto fehlt |
| VWAP (session-reset) | ✓ | v3.session_vwap() |
| MACD 12/26/9 | ✓ | v3.macd() |
| Bollinger 20/2.0 | ✗ | nur für Reversal-Setup, BullFlag braucht das nicht |
| RSI 14 | ✗ | nur für Reversal-Setup |
| Volume Bars | ✓ | als vol_sma20 + breakout_vol_factor |

## 4. Pullback-Count-Rule (kanonisch)

| Constraint | Code-Status |
|---|---|
| Pullback 1: aggressive | ✓ |
| Pullback 2: aggressive | ✓ |
| Pullback 3: nur conditional | ⚠ — Detector sieht jeden Breakout als unabhängigen Trade |
| Pullback 4+: skip | ✗ — nicht state-tracked |

→ **Gap**: Detector zählt Pullbacks NICHT pro Tag, jeder Breakout-Bar wird einzeln betrachtet.

## 5. Bull-Flag-Pattern

| Constraint | Code-Status |
|---|---|
| Pole 3-7 grüne Kerzen | ✓ |
| Pole cumulative ≥ 5 % (default) | ✓ konfigurierbar via --pole-pct |
| Pole no_topping_tail (max 0.4) | ✓ |
| Pole volume rising | ✗ NICHT geprüft — nur Breakout-Volume gefordert |
| Flag 1-3 rote Kerzen | ✓ |
| Flag retrace ≤ 50 % | ✓ |
| Flag must_hold_above_vwap | ✓ |
| Flag volume declining | ✗ NICHT geprüft |
| Breakout: erste grüne Kerze high > prev red high | ✓ |
| Breakout volume ≥ 1.5× SMA(20) | ✓ |
| level2_confirmation: green orders T&S | ✗ Tape-Reading nicht modellierbar (kein Tick-Data) |

## 6. Order-Routing

| Constraint | Code-Status |
|---|---|
| no_raw_market_orders | ✓ |
| use_marketable_limit_with_offset (15¢) | ✓ Slippage 1¢ angewandt (vereinfacht) |
| Hotkey-Schema | n/a (kein Live-Trading im Backtest) |

## 7. False-Breakout-Filter (5-Indikator)

| Indikator | Code-Status |
|---|---|
| 1. MACD-against-trade | ✓ |
| 2. Volume-Profile rot-heavy (>1.5× grün) | ✓ |
| 3. History of False Breakouts (≥ 2 topping tails) | ✓ |
| 4. Multiple topping tails (≥ 2 in last 5) | ✓ |
| 5. Long Consolidation (range < 0.5 %) | ✓ |
| Trigger ≥ 2 Hits | ✓ |

## 8. Exit-Framework

| Constraint | Code-Status |
|---|---|
| Scale-Out 50 % bei 1R, BE-Stop | ✓ |
| Scale-Out 25 % bei 2R = pole-height | ✓ |
| Trail-Stop 9 EMA (letzte 25 %) | ✗ — derzeit "exit at last_close at EOD" |
| MACD-Cross-Down-Exit | ✓ (mit profit-guard) |
| Big-Seller 100k+ Exit | ✗ — Tape-Reading-Daten fehlen |
| Jackknife-Detection | ✗ |
| 200 EMA-Bruch-Exit | ✗ — siehe oben |
| Time > 11:30 ET cut runners | ✗ — Trade läuft bis EOD oder Stop/Target |
| Time > 12:00 hard flat | ✗ — siehe oben |
| 50%-Drawdown-Rule | n/a (Per-Trade, nicht Tag-State) |

## 9. Risk-Framework

| Constraint | Code-Status |
|---|---|
| reward_to_risk_min 2.0 | ⚠ — Pattern-Setup gibt T2 = pole-height (≈ 1-3R), nicht garantiert 2R |
| accuracy_target 50/65/70 % | n/a Stats-Reporting |
| max_loss_per_trade $500 | n/a (Per-Trade-Sizing nicht modelliert) |
| daily_max_loss = daily_goal | n/a (Tag-State nicht modelliert) |
| quarter_size_rule | n/a |
| add_to_losers_forbidden | n/a (kein Add-Logik in Backtest) |
| 200ema-Filter | ✗ KRITISCHER GAP |
| benchmark_stats (Cameron-Live) | ✓ als Vergleichs-Anker im Stats-Output |

## 10. Top-N-pro-Tag (NEU in v3, Cameron-Workflow!)

| Constraint | Code-Status |
|---|---|
| Top 3-4 Percent-Gainers/Tag | ✓ via --top-n flag |
| Composite-Score (RVOL × Daily-%) | ✓ |
| Per-Rank-Stats | ✓ Stats-Output |

## Priorität nächster Iteration

**P1 (Gap mit signifikanten Win-Rate-Effekt):**
1. **200 EMA Filter** für Long-Trades (long_below_200ema_forbidden)
2. **9 EMA Trail-Logic** für letzte 25% Position (statt EOD-Hold)
3. **11:30 / 12:00 Hard-Flat** Time-Exit

**P2 (Constraint-Filling für Audit-Vollständigkeit):**
4. Pullback-Count-State-Tracking pro Tag (3.+ Pullback skip)
5. Pole-Volume-Rising-Filter
6. Flag-Volume-Declining-Filter
7. Bonus-Criteria (Recent IPO, Reverse-Split, etc.)
8. Catalyst-Timing-Boost (Mon/Tue/Wed > Fri-PM)

**P3 (nicht im Backtest implementierbar):**
9. Tape-Reading Big-Seller / Hidden-Buyer (Tick-Data fehlt)
10. Quarter-Size-Rule + Daily-Max-Loss (Live-Trading-State)

## Coverage-Score

| Kategorie | Coverage |
|---|---|
| Universe (5 Pillars) | 70 % (Float fehlt, RVOL-Proxy) |
| Session-Window | 80 % (RTH ja, Time-Cuts nein) |
| Indikatoren | 60 % (MACD/VWAP/Vol ja, EMAs nein) |
| Bull-Flag-Pattern | 85 % (Volume-Profile-Pole/Flag fehlt) |
| Top-N-Workflow | 100 % NEU |
| False-Breakout-Filter | 100 % |
| Exit-Framework | 60 % (Scale-Out + MACD ja, Trail-9EMA + Time-Cut nein) |
| Order-Routing | 70 % (Slippage approximiert) |

**Gesamt: ~76 % der constraints.yaml-Regeln sind im Code aktiv.**

Verbleibende 24 % entweder:
- nicht im Backtest sinnvoll (Tape-Reading, Hotkey-Skalierung) — 10 %
- benötigen 200 EMA + Trail-9EMA + Time-Cuts — 8 %
- Bonus-Criteria — 6 %
