# Cameron-Compliance-Audit — 2026-05-11

Vergleich: `03_rules_engine/constraints.yaml` (SSOT) vs `06_live_bot/bot.py` (Implementation).
Kategorisiert nach Cameron's eigener Hierarchie: **Selection → Pattern → Entry → Management**.

Severity-Legende: 🔴 KRITISCH (verändert Strategie-Verhalten) · 🟡 MITTEL · 🟢 NICE-TO-HAVE

---

## 1. Stock-Selection (5 Pillars)

| Pillar | Cameron-Soll (YAML) | Bot-Ist | Status |
|---|---|---|---|
| **Price** | $2-$20 strict, Sweet $5-$10 | `PRICE_MIN=2, PRICE_MAX=20` | ✅ konform |
| **Float** | `<10 M` strict, `<5 M` rocket | float_filter.py existiert — **wird nicht aufgerufen** | 🔴 **NICHT WIRKSAM** |
| **RVOL** | `≥ 5.0` (YAML) | `RVOL_MIN_PROXY = 2.0` (bot.py:74) | 🔴 **2.5× zu lasch** |
| **% Change** | `≥10 %` daily / `≥4 %` gap | `DAILY_GAIN_MIN_PCT=10` | ✅ konform |
| **Catalyst** | `catalyst_required: true` | nicht implementiert | 🔴 **fehlt komplett** |

**3 von 5 Pillars defekt** → Watchlist enthält Stocks die Cameron NIE traden würde.

### Maßnahme S-1 🔴
- `RVOL_MIN_PROXY 2.0 → 5.0` (1 Zeile, sofort)
- `float_filter.passes_float_filter()` in `_premarket_scan_inner` nach Pillars-Filter aufrufen
- Catalyst: minimaler MVP per **Yahoo-News-Feed** (`yf.Ticker(sym).news`) → flag wenn ≥1 Headline letzte 24 h. Voll-Implementation SEC-EDGAR später.

---

## 2. Pattern-Detection (Bull-Flag)

| Element | Cameron | Bot | Status |
|---|---|---|---|
| Pole-Candles | 3-7 grün | `POLE_MIN/MAX = 3/7` | ✅ |
| Pole-Min-Move | ≥5 % | `POLE_MIN_MOVE_PCT=5` | ✅ |
| Topping-Tail | <40 % | `POLE_TOPPING_TAIL_MAX=0.4` | ✅ |
| Pole-Volume rising | ja | `POLE_VOLUME_RISING_REQUIRED` | ✅ |
| Flag-Candles | 1-3 rot | `FLAG_MIN/MAX=1/3` | ✅ |
| Flag-Retrace | ≤50 % | `FLAG_RETRACE_MAX_PCT=50` | ✅ |
| Breakout-Volume | ≥1.5× SMA20 | `BREAKOUT_VOL_FACTOR=1.5` | ✅ |
| **VWAP-Hold** | Pflicht | `is_above_vwap` importiert — **nie aufgerufen** | 🔴 **inaktiv** |
| **MACD-Confirm** | 12/26/9 bullish | `patterns_rejected_macd` Counter — **keine Berechnung im Code** | 🔴 **fehlt** |
| **FBO-Filter** | 5-Indicator Veto | `patterns_rejected_fbo` Counter — **keine Berechnung** | 🔴 **fehlt** |
| Pullback-Count | max 2 | nicht im Live-Bot | 🟡 |

**Drei Indikatoren werden als "rejected" geloggt obwohl der Code sie nie prüft** — Day-Summary zeigt also Geister-Zahlen.

### Maßnahme P-1 🔴 (Pattern-Filter aktivieren)
```python
# In detect_bull_flag, vor Return True:
if not is_above_vwap(bars[:i+1], c[i]):
    return False, {"_veto": "vwap"}
# MACD 12/26/9
ema12 = pd.Series(c).ewm(span=12).mean()
ema26 = pd.Series(c).ewm(span=26).mean()
macd  = ema12 - ema26
signal = macd.ewm(span=9).mean()
if macd.iloc[i] <= signal.iloc[i] or macd.iloc[i] <= 0:
    return False, {"_veto": "macd"}
```

### Maßnahme P-2 🔴 (FBO-5-Indicator)
Cameron's False-Breakout-Veto: (a) Breakout-Bar mit Topping-Tail >40 %, (b) Volume <1.5× SMA20 (already enforced), (c) Close in unterstem Drittel der Range, (d) RSI(14)>80 (overbought), (e) keine 2 grünen Bestätigungs-Bars.

→ Eigene `false_breakout_filter()` Funktion, returnt `(rejected, reason)`. Plugin-style in `detect_bull_flag` vor return.

---

## 3. Risk / Position-Sizing

| Regel | Cameron | Bot | Status |
|---|---|---|---|
| Max-Loss pro Trade | $50 (Paper) / risk-per-share basiert | `MAX_LOSS_PER_TRADE_USD=50` | ✅ |
| Daily-Loss-Cap | 3× Trade-Loss | `DAILY_MAX_LOSS_USD=150` | ✅ |
| Daily-Goal-Stop | nach Treffer Stop | `DAILY_GOAL_USD=150` | ✅ |
| Quarter-Size | bei Uncertain | `QUARTER_SIZE_UNLOCK_CENTS=0.20` | ✅ |
| 1:2 R:R-Minimum | `T1=entry+(entry-stop)` | `target1 = ep+(ep-sp)` | ✅ |
| Psych-Level-T2 | whole/half | `USE_PSYCH_LEVEL_T2=True` | ✅ |
| **Liquidity-Cap** | `1 %` of avg vol | `LIQUIDITY_CAP_PCT_OF_AVG_VOL=1.0` definiert | 🟡 **nicht in size-calc verwendet** |
| Slippage-Sim | realistic | `SLIPPAGE_CENTS=0.05` | ✅ (heute live: $0.18 auf STFS = höher) |

### Maßnahme R-1 🟡
Liquidity-Cap in `compute_position_size`: `shares = min(shares, int(avg_vol * LIQUIDITY_CAP_PCT_OF_AVG_VOL / 100))`. Damit nicht "Whales-in-Pond"-Problem bei dünnen Names.

---

## 4. Timing / Sessions

| Regel | Cameron | Bot | Status |
|---|---|---|---|
| First-Hour Power | 9:30-10:30 | nur RTH-Start, kein Power-Boost | 🟡 |
| No-New-Entries | nach 11:30 | `TIME_NEW_ENTRIES_END=11:30` | ✅ |
| Hard-Flat | 12:00 ET | `TIME_HARD_FLAT=12:00` | ✅ |
| **Best-Days-Boost** | Mo/Di/Mi | `best_news_days: [Mon,Tue,Wed]` in YAML — **nicht im Code** | 🟢 |
| Avoid-Fri-PM | nach 12:00 Fr | nicht im Code | 🟢 |

### Maßnahme T-1 🟡
Power-Hour-Size-Boost: in 9:30-10:30 ET 100 %, 10:30-11:30 75 %, danach kein neuer Trade. Cameron's eigene Stats: ~80 % seiner Gewinne in Power-Hour.

---

## 5. Trade-Management

| Regel | Cameron | Bot | Status |
|---|---|---|---|
| Quick-Exit 30¢ | innerhalb 5 Bars | `QUICK_EXIT_THRESHOLD_CENTS=0.30, QUICK_EXIT_BARS_LIMIT=5` | ✅ |
| Pyramiding | +10¢ alle 25 % more | `ADD_TO_WINNER_ENABLED, +10¢, 25%, max 3` | ✅ |
| Stop-to-BE | nach T1 fill | im manage_position | ✅ (Code-Review nötig für Edge-Cases) |
| Trailing-Stop | Cameron lockerer | nicht implementiert | 🟢 |
| **MACD-Exit** | bei bear-cross | erwähnt in Docstring — **nicht im Code** | 🔴 |
| Time-Stop | hard-flat 12:00 | ja | ✅ |

### Maßnahme M-1 🔴
MACD-Exit in `manage_position`: nach Entry continuously MACD prüfen, bei bullish→bearish-Cross → Sell-Market. Verhindert "fade-aways" nach erfolgreichem Pattern.

---

## 6. Spiral / Self-Protection

| Regel | Cameron | Bot | Status |
|---|---|---|---|
| 2-Loss-Spiral-Stop | ja | implementiert | ✅ |
| SPY-Bear-Veto | ja | `SPY_TREND_VETO_PCT=-1.0` | ✅ |
| Max-Trades/Tag | 3-5 | `MAX_TRADES_PER_DAY=5` | ✅ |
| Reduced-Size SPY-soft | <-0.5% | implementiert | ✅ |

→ Sehr gut, keine Befunde.

---

## 7. Operations (heute neu)

| Item | Status |
|---|---|
| Pre-Flight | ✅ neu |
| Watchlist-Persist | ✅ neu |
| Reconnect-Backoff | ✅ neu |
| Position-Recovery | ✅ neu |
| Slippage-Log | ✅ neu |
| Status.json | ✅ neu |
| Day-Summary-File | ✅ neu |
| CI/GitHub-Actions | ✅ neu |

---

## 8. Tests-Coverage-Gaps

Aktuell 67 Tests grün, aber **kein Test prüft**:
- ob detect_bull_flag VWAP-Veto wirklich greift (weil nicht implementiert)
- ob MACD-Check greift (dito)
- ob float_filter in premarket_scan aufgerufen wird (dito)
- end-to-end Watchlist→Trade auf historischen Tag (Replay läuft, aber nicht in Fast-Suite)

### Maßnahme TEST-1
Nach P-1 / S-1 / M-1 Implementation: Tests dass Veto-Counter sich nach injiziertem Bar bewegt.

---

## 9. Priorisierte Action-Liste (To-Do)

Reihenfolge = Impact × Aufwand:

| # | Maßnahme | Effort | Impact |
|---|---|---|---|
| 1 | **RVOL 2.0 → 5.0** | 1 min | 🔴 großer Filter-Effekt |
| 2 | **float_filter wiring** | 10 min | 🔴 |
| 3 | **VWAP-Veto in detect_bull_flag** | 15 min | 🔴 |
| 4 | **MACD 12/26/9 + Entry-Veto** | 30 min | 🔴 |
| 5 | **MACD-Exit in manage_position** | 20 min | 🔴 |
| 6 | **FBO-5-Indicator** | 45 min | 🔴 |
| 7 | **Catalyst MVP** (Yahoo-News) | 30 min | 🔴 |
| 8 | **Liquidity-Cap in sizing** | 10 min | 🟡 |
| 9 | **Power-Hour-Boost** | 20 min | 🟡 |
| 10 | **Tests für 3-7** | 40 min | 🟡 |

**Gesamtaufwand:** ~3.5 h. Nicht für die Premarket-Phase morgen — danach.

---

## 10. Bottom Line

**Stand heute Abend:**
- Operations (Resilienz, Telemetrie, Recovery) sind **state-of-the-art**.
- Strategie-Layer **ist 50 % Cameron** — Pillars halb, Pattern-Filter halb.

**Bot wird morgen traden**, aber mit zu loosen Filtern und ohne VWAP/MACD-Veto → höhere Trade-Frequenz und schlechtere Win-Rate als Cameron. Realer Schaden in Paper-Trading = null, aber für Vergleichbarkeit mit Cameron-Stats müssen die Filter rein.

**Empfehlung:** Top-7 der Action-Liste vor Live-Real-Money. Können wir morgen Abend nach US-Close zusammen durchgehen.
