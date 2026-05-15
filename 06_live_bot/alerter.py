"""alerter.py — Phase-25 (live-readiness): minimal operator-alerter.

Three channels, pick whichever .env config you have:

  TelegramAlerter (recommended for live)
    Needs: TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID env vars.
    Push notification to your phone within seconds.

  SMTPAlerter
    Needs: SMTP_HOST + SMTP_PORT + SMTP_USER + SMTP_PASS + SMTP_TO env vars.
    Sends an email. Use TLS unless port is 25.

  LogAlerter (always-on fallback)
    Writes alerts to alerts.log so you at least see them on disk.

Use `make_alerter()` to auto-select from environment, or instantiate
directly. All alerters share the same `.send(level, title, body)` API.
The level field is one of: info, warn, error, critical.

Debounce: by default the alerter suppresses duplicate level+title within
the same suppression-window (default 15 min) so a single repeated
failure doesn't spam your phone. Use `.send(force=True)` to bypass.
"""
from __future__ import annotations
import os
import json
import logging
import threading
import time
from pathlib import Path
from typing import Optional

log = logging.getLogger("alerter")

# Suppression: same (level, title) won't re-fire within this window
DEFAULT_SUPPRESS_SECONDS = 15 * 60


class _BaseAlerter:
    """Common debounce + safety wrapper. Subclasses implement _do_send()."""

    name: str = "base"

    def __init__(self, *, suppress_seconds: int = DEFAULT_SUPPRESS_SECONDS):
        self.suppress_seconds = suppress_seconds
        self._last_sent: dict[tuple[str, str], float] = {}
        self._lock = threading.Lock()

    def _should_send(self, level: str, title: str, force: bool) -> bool:
        if force:
            return True
        key = (level, title)
        now = time.time()
        with self._lock:
            last = self._last_sent.get(key)
            if last and (now - last) < self.suppress_seconds:
                return False
            self._last_sent[key] = now
        return True

    def send(self, level: str, title: str, body: str = "",
              *, force: bool = False) -> bool:
        """Send an alert. Returns True if sent, False if suppressed/failed.
        Never raises — alerter failures must not crash the bot."""
        if level not in ("info", "warn", "error", "critical"):
            level = "info"
        if not self._should_send(level, title, force):
            return False
        try:
            return self._do_send(level, title, body)
        except Exception as e:
            log.warning("%s alerter failed: %s", self.name, e)
            return False

    def _do_send(self, level: str, title: str, body: str) -> bool:
        raise NotImplementedError


class NtfyAlerter(_BaseAlerter):
    """Push to ntfy.sh — zero-signup push-notification service.

    User installs the ntfy mobile app, subscribes to a unique topic
    name (their choice, no account needed), then this alerter publishes
    to https://ntfy.sh/<topic>. The phone receives push immediately.

    Self-host alternative: pass server="https://your-ntfy-server" to
    point at a private instance instead of the public ntfy.sh service.
    """

    name = "ntfy"

    def __init__(self, *, topic: str,
                  server: str = "https://ntfy.sh",
                  suppress_seconds: int = DEFAULT_SUPPRESS_SECONDS,
                  http_post=None):
        super().__init__(suppress_seconds=suppress_seconds)
        self.topic = topic
        self.server = server.rstrip("/")
        self._post = http_post

    def _do_send(self, level: str, title: str, body: str) -> bool:
        priority_int = {
            "info": 3, "warn": 3, "error": 4, "critical": 5,
        }.get(level, 3)
        tag = {
            "info": "information_source", "warn": "warning",
            "error": "x", "critical": "rotating_light",
        }.get(level, "bell")
        title_full = f"[{level.upper()}] {title}"[:200]
        # Phase-48 (2026-05-15): use ntfy's JSON POST API instead of
        # HTTP headers. urllib AND requests/urllib3 both encode headers
        # as latin-1, which rejects emoji codepoints. JSON body is
        # full UTF-8 → emoji like 🟢▲ TP / 🟠▼ SL survive intact.
        #
        # ntfy.sh JSON endpoint: POST https://ntfy.sh/ with body
        # {"topic": "<topic>", "title": "...", "message": "...",
        #  "priority": 1..5, "tags": ["x", "y"]}
        payload = {
            "topic": self.topic,
            "title": title_full,
            "message": body or title,
            "priority": priority_int,
            "tags": [tag],
        }
        import json as _json
        data = _json.dumps(payload, ensure_ascii=False).encode("utf-8")
        url = self.server  # POST to root, NOT /<topic>
        headers = {"Content-Type": "application/json; charset=utf-8"}
        if self._post is not None:
            try:
                r = self._post(url, data=data, headers=headers, timeout=10)
                return getattr(r, "status_code", 0) in (200, 202)
            except Exception as e:
                log.warning("ntfy custom-post failed: %s", e)
                return False
        import urllib.request
        req = urllib.request.Request(url, data=data, headers=headers,
                                      method="POST")
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status in (200, 202)
        except Exception as e:
            log.warning("ntfy urlopen failed: %s", e)
            return False


class TelegramAlerter(_BaseAlerter):
    """Push to a Telegram bot. Set TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID."""

    name = "telegram"

    def __init__(self, *, bot_token: str, chat_id: str,
                  suppress_seconds: int = DEFAULT_SUPPRESS_SECONDS,
                  http_get=None, http_post=None):
        super().__init__(suppress_seconds=suppress_seconds)
        self.bot_token = bot_token
        self.chat_id = chat_id
        # Allow injecting a fake HTTP client for tests
        self._post = http_post

    def _do_send(self, level: str, title: str, body: str) -> bool:
        icon = {"info": "ℹ️", "warn": "⚠️", "error": "❌", "critical": "🚨"}.get(level, "•")
        msg = f"{icon} *{level.upper()}*: {title}\n\n{body}"[:4000]
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {"chat_id": self.chat_id, "text": msg, "parse_mode": "Markdown"}
        if self._post is not None:
            r = self._post(url, json=payload, timeout=10)
        else:
            import urllib.request
            import urllib.parse
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                url, data=data,
                headers={"Content-Type": "application/json"},
            )
            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    return resp.status == 200
            except Exception as e:
                log.warning("telegram urlopen failed: %s", e)
                return False
        return getattr(r, "status_code", 0) == 200


class SMTPAlerter(_BaseAlerter):
    """Send an email via SMTP. TLS unless port == 25."""

    name = "smtp"

    def __init__(self, *, host: str, port: int, user: str, password: str,
                  to_addr: str,
                  suppress_seconds: int = DEFAULT_SUPPRESS_SECONDS,
                  smtp_factory=None):
        super().__init__(suppress_seconds=suppress_seconds)
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.to_addr = to_addr
        self._smtp_factory = smtp_factory

    def _do_send(self, level: str, title: str, body: str) -> bool:
        subj = f"[CAMERON-{level.upper()}] {title}"[:200]
        from email.mime.text import MIMEText
        msg = MIMEText(body or title)
        msg["Subject"] = subj
        msg["From"] = self.user
        msg["To"] = self.to_addr
        if self._smtp_factory is not None:
            client = self._smtp_factory(self.host, self.port)
            close_method = "quit"
        else:
            import smtplib
            if self.port == 25:
                client = smtplib.SMTP(self.host, self.port, timeout=15)
            else:
                client = smtplib.SMTP_SSL(self.host, self.port, timeout=15)
            close_method = "quit"
        try:
            client.login(self.user, self.password)
            client.sendmail(self.user, [self.to_addr], msg.as_string())
            return True
        finally:
            try:
                getattr(client, close_method)()
            except Exception:
                pass


class LogAlerter(_BaseAlerter):
    """Always-on fallback: write to alerts.log. Used when neither
    Telegram nor SMTP credentials are configured."""

    name = "log"

    def __init__(self, *, path: Path | str,
                  suppress_seconds: int = DEFAULT_SUPPRESS_SECONDS):
        super().__init__(suppress_seconds=suppress_seconds)
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _do_send(self, level: str, title: str, body: str) -> bool:
        from datetime import datetime, timezone
        line = json.dumps({
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": level,
            "title": title,
            "body": body[:2000],
        }) + "\n"
        with self.path.open("a", encoding="utf-8") as f:
            f.write(line)
        return True


class CompositeAlerter(_BaseAlerter):
    """Send through ALL configured channels. Useful for "Telegram + log
    so I have an audit trail even when the network is down"."""

    name = "composite"

    def __init__(self, alerters: list[_BaseAlerter],
                  *, suppress_seconds: int = DEFAULT_SUPPRESS_SECONDS):
        super().__init__(suppress_seconds=suppress_seconds)
        self.alerters = alerters

    def _do_send(self, level: str, title: str, body: str) -> bool:
        any_ok = False
        for a in self.alerters:
            try:
                if a.send(level, title, body, force=True):
                    any_ok = True
            except Exception as e:
                log.warning("composite child %s failed: %s", a.name, e)
        return any_ok


def make_alerter(*, alerts_log_path: Path | str | None = None,
                  env: dict | None = None) -> _BaseAlerter:
    """Build the best alerter available from env vars.

    Priority (every configured channel gets included; LogAlerter always):
      - NTFY_TOPIC (+ optional NTFY_SERVER)        → NtfyAlerter
      - TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID      → TelegramAlerter
      - SMTP_HOST + SMTP_USER + SMTP_PASS + SMTP_TO → SMTPAlerter
      - (always) LogAlerter on disk

    The Log alerter is ALWAYS included so you have a disk audit trail.

    Phase-25 ENV resolution: caller may pass env= explicitly. Otherwise
    we trigger secrets_loader's .env loader first (mirroring how
    get_alpaca_keys() picks up TELEGRAM_* vars from the same .env file).
    """
    if env is None:
        # Trigger .env → os.environ population (idempotent, only sets
        # keys that aren't already there).
        try:
            from secrets_loader import _load_env_file
            _load_env_file()
        except Exception:
            pass
        env = os.environ
    if alerts_log_path is None:
        alerts_log_path = Path(__file__).resolve().parent / "alerts.log"
    log_alerter = LogAlerter(path=alerts_log_path)
    children: list[_BaseAlerter] = []

    ntfy_topic = env.get("NTFY_TOPIC")
    if ntfy_topic:
        ntfy_server = env.get("NTFY_SERVER", "https://ntfy.sh")
        children.append(NtfyAlerter(topic=ntfy_topic, server=ntfy_server))

    tg_token = env.get("TELEGRAM_BOT_TOKEN")
    tg_chat = env.get("TELEGRAM_CHAT_ID")
    if tg_token and tg_chat:
        children.append(TelegramAlerter(bot_token=tg_token, chat_id=tg_chat))

    smtp_host = env.get("SMTP_HOST")
    smtp_user = env.get("SMTP_USER")
    smtp_pass = env.get("SMTP_PASS")
    smtp_to = env.get("SMTP_TO")
    smtp_port = int(env.get("SMTP_PORT", "465"))
    if smtp_host and smtp_user and smtp_pass and smtp_to:
        children.append(SMTPAlerter(
            host=smtp_host, port=smtp_port,
            user=smtp_user, password=smtp_pass, to_addr=smtp_to,
        ))

    children.append(log_alerter)
    if len(children) == 1:
        return log_alerter
    return CompositeAlerter(children)
