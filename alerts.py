"""Logging + alert fan-out: rotating log file, dashboard feed, optional Telegram and Termux notification.
Notifications run in daemon threads so a slow network never delays a trade decision."""
import json
import logging
import shutil
import subprocess
import threading
import time
import urllib.parse
import urllib.request
from collections import deque
from logging.handlers import RotatingFileHandler

from .config import LOGS, load_secrets

_PUSH_LEVELS = {"TRADE", "WARNING", "ERROR", "CRITICAL"}


class Alerts:
    def __init__(self):
        self.recent = deque(maxlen=300)
        self.log = logging.getLogger("mahlabahlasele")
        if not self.log.handlers:
            self.log.setLevel(logging.INFO)
            fh = RotatingFileHandler(LOGS / "mahlabahlasele.log", maxBytes=1_000_000, backupCount=3)
            fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
            sh = logging.StreamHandler()
            sh.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%H:%M:%S"))
            self.log.addHandler(fh)
            self.log.addHandler(sh)

    def send(self, level, msg):
        level = level.upper()
        py_level = {"TRADE": logging.INFO, "INFO": logging.INFO, "WARNING": logging.WARNING,
                    "ERROR": logging.ERROR, "CRITICAL": logging.CRITICAL}.get(level, logging.INFO)
        self.log.log(py_level, "[%s] %s", level, msg)
        self.recent.append({"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "level": level, "msg": msg})
        if level in _PUSH_LEVELS:
            threading.Thread(target=self._push, args=(level, msg), daemon=True).start()

    def info(self, m): self.send("INFO", m)
    def warn(self, m): self.send("WARNING", m)
    def error(self, m): self.send("ERROR", m)
    def critical(self, m): self.send("CRITICAL", m)
    def trade(self, m): self.send("TRADE", m)

    def _push(self, level, msg):
        try:
            s = load_secrets()
            tok, chat = s.get("telegram_bot_token"), s.get("telegram_chat_id")
            if tok and chat:
                data = urllib.parse.urlencode({"chat_id": chat, "text": f"[{level}] {msg}"}).encode()
                urllib.request.urlopen(f"https://api.telegram.org/bot{tok}/sendMessage", data, timeout=8)
            if shutil.which("termux-notification"):
                subprocess.run(["termux-notification", "--title", f"Mahlabahlasele {level}",
                                "--content", msg[:200]], timeout=5)
        except Exception:
            pass  # alerting must never break trading


class NullAlerts:
    recent = deque(maxlen=1)
    def send(self, *a, **k): pass
    info = warn = error = critical = trade = send


alerts = Alerts()
