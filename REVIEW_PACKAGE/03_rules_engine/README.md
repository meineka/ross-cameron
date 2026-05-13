# Rules Engine

`constraints.yaml` ist die **Single Source of Truth** für alle harten Regeln.
Code im Backtest und (später) im Live-Adapter MUSS daraus laden — niemals
Werte hardcoden.

Schichten:

1. **Universe-Filter** (`universe:`) — pro Tag/Premarket angewendet, ergibt
   Watchlist (typisch 1–5 Tickers).
2. **Session-Filter** (`session:`) — Zeitfenster gating.
3. **Entry-Modelle** (`entries:`) — pattern_match() pro Bar; jedes Modell ist
   unabhängig prüfbar.
4. **Exit-Framework** (`exits:`) — angewendet auf offene Positionen.
5. **Risk-Layer** (`risk:`) — Position-Sizing + harte Caps (per-trade, daily).
6. **Vetos** (`vetos:`) — globale Kill-Switches; eines reicht zum Skip.

Validierungs-Reihenfolge pro potentiellem Trade:
```
universe → session → entry_model_match → veto_check → risk_sizing → execute
```

Wenn ein Veto greift: Trade nicht nehmen, Grund loggen.
Wenn risk_sizing 0 Shares ergibt (Stop zu weit): Trade nicht nehmen.
