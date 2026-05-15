"""health_monitor.py — Phase-25: health-check + alerter loop.

Probes every CHECK_INTERVAL_SEC (default 300 = 5 min):

  - heartbeat.txt age (bot daemon liveness)
  - audit.recommendation (composite bot-health verdict)
  - yfinance: SPY snapshot reachable + non-stale
  - Alpaca: account.get + latest_trade fresh
  - postmortem.last_watchdog_error (consumption tracker)

Each probe maintains a FAILURE STREAK. On reaching N_CONSECUTIVE
(default 2 = 10 min of badness) the alerter fires. When a probe
recovers, the streak resets and a "recovered" info-alert is sent.

Run:
  python 06_live_bot/health_monitor.py
or as a Windows service / cron job.

This is meant to run ALONGSIDE the watchdog, not replace it. The
watchdog handles process-lifecycle; this monitor handles external-
dependency-and-data-feed health.
"""
from __future__ import annotations
import argparse
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

log = logging.getLogger("health-monitor")

CHECK_INTERVAL_SEC = 300  # 5 min
N_CONSECUTIVE_FAILURES = 2  # legacy default — most probes use per-probe override below
HEARTBEAT_MAX_AGE_SEC = 1800  # 30 min (bot sleeps between scans, that's OK)
YF_QUOTE_MAX_AGE_SEC = 7200   # 2 h (covers extended-hours, overnight, etc.)
ALPACA_QUOTE_MAX_AGE_SEC = 7200

# Phase-25c (user request): yahoo + alpaca + catalyst-news fire IMMEDIATELY
# on first failure (n=1) so we don't waste 5 minutes when the data feed is
# blocked / rate-limited / down. heartbeat + audit stay at 2 because a
# one-off blip in process-detection or heartbeat timing is noise, not signal.
PROBE_THRESHOLDS = {
    "heartbeat": 2,
    "audit": 2,
    "yfinance": 1,       # immediate alert on yahoo blocked / no-data
    "alpaca": 1,         # immediate alert on alpaca account / data outage
    "catalyst_news": 1,  # immediate alert on news API broken
}

# Phase-25d (user request): re-fire alert every RE_FIRE_AFTER_SEC if the
# probe is STILL failing. Without this, a long outage produces 1 push and
# then radio silence. With this, you get a "still failing 1h later" push,
# then 2h, etc. — so you can't accidentally miss that the bot's deaf.
RE_FIRE_AFTER_SEC = 3600  # 1 h


class ProbeResult:
    __slots__ = ("name", "ok", "detail", "value")

    def __init__(self, name: str, ok: bool, detail: str = "", value=None):
        self.name = name
        self.ok = ok
        self.detail = detail
        self.value = value


class HealthMonitor:
    def __init__(self, *, alerter, interval_sec: int = CHECK_INTERVAL_SEC,
                  n_consecutive: int = N_CONSECUTIVE_FAILURES,
                  re_fire_after_sec: int = RE_FIRE_AFTER_SEC):
        self.alerter = alerter
        self.interval_sec = interval_sec
        self.n_consecutive = n_consecutive
        self.re_fire_after_sec = re_fire_after_sec
        # Per-probe failure streak counter
        self._streak: dict[str, int] = {}
        # Per-probe last-alerted flag (so we can fire a "recovered" alert)
        self._alerted: dict[str, bool] = {}
        # Phase-25d: per-probe timestamp of last failure-alert so we can
        # re-fire every re_fire_after_sec while the probe keeps failing.
        self._last_alert_ts: dict[str, float] = {}

    # ─── Individual probes ─────────────────────────────────────────────────
    def probe_heartbeat(self) -> ProbeResult:
        hb = HERE / "heartbeat.txt"
        if not hb.exists():
            return ProbeResult("heartbeat", False, "heartbeat.txt missing")
        age = time.time() - hb.stat().st_mtime
        if age > HEARTBEAT_MAX_AGE_SEC:
            return ProbeResult("heartbeat", False,
                                f"heartbeat.txt {age:.0f}s old (>{HEARTBEAT_MAX_AGE_SEC}s)",
                                value=age)
        return ProbeResult("heartbeat", True, f"age={age:.0f}s", value=age)

    def probe_audit_recommendation(self) -> ProbeResult:
        try:
            import audit
            status = audit.get_bot_status()
            cls = status.get("bot_proc_classification", {})
            classification = cls.get("classification")
            if classification == "multiple_independent_bots":
                return ProbeResult("audit", False,
                                    f"multiple_independent_bots: {cls.get('pids')}")
            if classification == "none":
                return ProbeResult("audit", False, "no bot process running")
            return ProbeResult("audit", True,
                                f"classification={classification}",
                                value=classification)
        except Exception as e:
            return ProbeResult("audit", False, f"audit raised {type(e).__name__}: {e}")

    def probe_yfinance(self) -> ProbeResult:
        try:
            import yfinance as yf
            t = yf.Ticker("SPY")
            info = t.fast_info
            price = info.last_price
            if price is None or price <= 0:
                return ProbeResult("yfinance", False, "SPY price is 0/None")
            return ProbeResult("yfinance", True, f"SPY=${price:.2f}", value=price)
        except Exception as e:
            return ProbeResult("yfinance", False,
                                f"{type(e).__name__}: {str(e)[:120]}")

    def probe_alpaca(self) -> ProbeResult:
        try:
            from secrets_loader import get_alpaca_keys
            from alpaca.trading.client import TradingClient
            from alpaca.data.historical import StockHistoricalDataClient
            from alpaca.data.requests import StockSnapshotRequest
            k, s = get_alpaca_keys()
            tc = TradingClient(k, s, paper=True)
            a = tc.get_account()
            if str(a.status) != "AccountStatus.ACTIVE":
                return ProbeResult("alpaca", False, f"account_status={a.status}")
            if a.trading_blocked:
                return ProbeResult("alpaca", False, "trading_blocked=True")
            # Market-hours-aware staleness check
            try:
                clock = tc.get_clock()
                is_open = bool(getattr(clock, "is_open", False))
            except Exception:
                is_open = False
            stale_threshold = 600 if is_open else 24 * 3600  # 10 min RTH / 24 h closed
            # Quick data ping
            dc = StockHistoricalDataClient(k, s)
            snap = dc.get_stock_snapshot(
                StockSnapshotRequest(symbol_or_symbols=["SPY"], feed="iex"))
            sp = snap.get("SPY")
            if not sp or not sp.latest_trade:
                return ProbeResult("alpaca", False, "SPY snapshot empty")
            age = (datetime.now(timezone.utc) - sp.latest_trade.timestamp).total_seconds()
            if age > stale_threshold:
                return ProbeResult("alpaca", False,
                                    f"SPY trade {age:.0f}s old (market_open={is_open}, threshold={stale_threshold}s)",
                                    value=age)
            return ProbeResult("alpaca", True,
                                f"account=ACTIVE, market_open={is_open}, SPY age={age:.0f}s",
                                value=age)
        except Exception as e:
            return ProbeResult("alpaca", False,
                                f"{type(e).__name__}: {str(e)[:120]}")

    def probe_catalyst_news(self) -> ProbeResult:
        """Phase-25: ping yfinance news on a known-good symbol so we
        notice when the catalyst pipeline is hosed even before the bot
        tries to use it."""
        try:
            import yfinance as yf
            t = yf.Ticker("AAPL")
            news = t.news
            if not isinstance(news, list):
                return ProbeResult("catalyst_news", False, "news is not a list")
            return ProbeResult("catalyst_news", True,
                                f"AAPL news items={len(news)}",
                                value=len(news))
        except Exception as e:
            return ProbeResult("catalyst_news", False,
                                f"{type(e).__name__}: {str(e)[:120]}")

    # ─── Probe loop ───────────────────────────────────────────────────────
    def run_once(self) -> list[ProbeResult]:
        """Run all probes once. Updates streak counters and fires alerts
        when threshold reached. Returns the result list."""
        probes: list[Callable[[], ProbeResult]] = [
            self.probe_heartbeat,
            self.probe_audit_recommendation,
            self.probe_yfinance,
            self.probe_alpaca,
            self.probe_catalyst_news,
        ]
        results = []
        for fn in probes:
            try:
                r = fn()
            except Exception as e:
                r = ProbeResult(fn.__name__.replace("probe_", ""),
                                 False, f"probe crashed: {type(e).__name__}")
            results.append(r)
            self._handle_result(r)
        return results

    def _handle_result(self, r: ProbeResult) -> None:
        name = r.name
        now = time.time()
        if r.ok:
            # If we'd alerted on this probe and it's now back, fire a recovered note
            if self._alerted.get(name):
                # Phase-25d: include how long the outage lasted in the recovery push
                outage_min = None
                first_alert = self._last_alert_ts.get(name)
                if first_alert:
                    outage_min = int((now - first_alert) / 60)
                body = f"Probe {name} is healthy again: {r.detail}"
                if outage_min is not None:
                    body += f"\n\nOutage lasted ~{outage_min} min."
                self.alerter.send("info", f"recovered: {name}",
                                   body=body, force=True)
                self._alerted[name] = False
                self._last_alert_ts.pop(name, None)
            self._streak[name] = 0
            return
        # Failure
        self._streak[name] = self._streak.get(name, 0) + 1
        streak = self._streak[name]
        # Phase-25c: per-probe threshold. Yahoo/Alpaca/news fire on first
        # failure (n=1); heartbeat/audit on second (n=2) to suppress blips.
        threshold = PROBE_THRESHOLDS.get(name, self.n_consecutive)
        if streak < threshold:
            return
        # Phase-25d: decide whether to push. Two cases trigger:
        #   (a) First time we hit threshold and haven't alerted yet.
        #   (b) Probe still failing and last alert was > re_fire_after_sec ago.
        last_alert = self._last_alert_ts.get(name)
        should_fire = False
        if not self._alerted.get(name):
            should_fire = True  # first push for this outage
            title_prefix = ""
        elif last_alert and (now - last_alert) >= self.re_fire_after_sec:
            should_fire = True  # re-fire after re_fire_after_sec
            title_prefix = "still "
        else:
            title_prefix = ""

        if should_fire:
            level = "critical" if name in ("alpaca", "audit") else "error"
            duration_min = int((now - last_alert) / 60) if last_alert else 0
            extra = (f"\n\n(continuously failing for ~{duration_min} min — "
                     f"re-fire every {self.re_fire_after_sec // 60} min)"
                     if last_alert else "")
            self.alerter.send(
                level, f"{title_prefix}{name} unhealthy",
                body=f"Probe {name} failed {streak}x in a row (threshold={threshold}): {r.detail}{extra}",
                force=True,  # bypass alerter-level debounce; this monitor IS the gate
            )
            self._alerted[name] = True
            self._last_alert_ts[name] = now

    def run(self) -> None:
        log.info("HEALTH-MONITOR START — interval=%ds, alert-after-%d-consecutive",
                 self.interval_sec, self.n_consecutive)
        while True:
            try:
                results = self.run_once()
                ok = sum(1 for r in results if r.ok)
                log.info("tick: %d/%d probes OK", ok, len(results))
                for r in results:
                    log.info("  %-15s %s  %s",
                             r.name, "OK" if r.ok else "FAIL", r.detail)
            except Exception as e:
                log.exception("monitor tick raised: %s", e)
            time.sleep(self.interval_sec)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--once", action="store_true",
                   help="run a single probe cycle and exit (for testing)")
    p.add_argument("--interval", type=int, default=CHECK_INTERVAL_SEC,
                   help="probe interval in seconds")
    p.add_argument("--n-consecutive", type=int, default=N_CONSECUTIVE_FAILURES,
                   help="failure streak before alert fires")
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    from alerter import make_alerter
    alerter = make_alerter()
    log.info("alerter selected: %s",
             type(alerter).__name__)
    mon = HealthMonitor(alerter=alerter,
                         interval_sec=args.interval,
                         n_consecutive=args.n_consecutive)
    if args.once:
        for r in mon.run_once():
            print(f"  {r.name:<15} {'OK' if r.ok else 'FAIL'}  {r.detail}")
        return 0
    mon.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
