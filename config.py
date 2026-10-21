"""Configuration: DEFAULTS -> config.json -> data/runtime_config.json (written by the dashboard)."""
import json
import os
import re
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
LOGS = ROOT / "logs"
DATA.mkdir(exist_ok=True)
LOGS.mkdir(exist_ok=True)

DEFAULTS = {
    "mode": "paper",                 # paper | live (live also needs MAHLA_ALLOW_LIVE=1)
    "exchange": "binance",
    "symbol": "BTCUSDT",
    "ccxt_symbol": "BTC/USDT",       # live mode only
    "timeframe": "5m",
    "data_source": "exchange",       # exchange | synthetic | csv
    "csv_path": "data/history.csv",
    "poll_seconds": 30,
    "initial_balance": 1000.0,
    "fee_rate": 0.001,
    "slippage": 0.0005,
    "unit_per_lot": 1.0,             # units per "lot" (forex: 100000)
    "max_leverage": 1.0,
    "min_notional": 10.0,
    "allow_short": True,             # set False for spot markets
    # indicators
    "sma_fast": 10, "sma_slow": 30, "rsi_period": 14, "atr_period": 14,
    "rsi_oversold": 30.0, "rsi_overbought": 70.0,
    # strategy / risk
    "signal_threshold": 0.3,
    "risk_per_trade": 0.01,
    "min_rr": 1.2,
    "max_cost_to_risk": 0.25,        # skip trades whose round-trip fees+slippage exceed this share of the stop distance
    "max_hold_bars": 48,
    # circuit breaker
    "max_drawdown": 0.10,
    "daily_loss_limit": 0.03,
    "max_consecutive_losses": 5,
    "max_data_errors": 5,
    # latency / connectivity
    "max_latency_ms": 3000,
    "http_timeout": 6,
    "http_retries": 3,
    # learning (reward system)
    "learning_enabled": True,
    "learn_alpha": 0.1,
    "epsilon_start": 0.30,
    "epsilon_floor": 0.05,
    "epsilon_decay": 0.995,
    "ucb_c": 0.5,
    "shrink_k": 3,
    # optional CrewAI narrative layer
    "crew_enabled": False,
}

EDITABLE = {
    "mode": ("choice", ["paper", "live"]),
    "symbol": ("str",),
    "timeframe": ("choice", ["1m", "3m", "5m", "15m", "30m", "1h", "4h", "1d"]),
    "data_source": ("choice", ["exchange", "synthetic", "csv"]),
    "poll_seconds": ("int", 5, 3600),
    "risk_per_trade": ("float", 0.001, 0.05),
    "min_rr": ("float", 0.5, 5.0),
    "max_hold_bars": ("int", 2, 1000),
    "sma_fast": ("int", 2, 200),
    "sma_slow": ("int", 5, 400),
    "rsi_period": ("int", 2, 100),
    "rsi_oversold": ("float", 5, 45),
    "rsi_overbought": ("float", 55, 95),
    "signal_threshold": ("float", 0.05, 1.0),
    "max_drawdown": ("float", 0.01, 0.5),
    "daily_loss_limit": ("float", 0.005, 0.3),
    "max_consecutive_losses": ("int", 2, 50),
    "max_latency_ms": ("int", 200, 30000),
    "allow_short": ("bool",),
    "learning_enabled": ("bool",),
    "crew_enabled": ("bool",),
}


def _coerce(key, value):
    spec = EDITABLE[key]
    kind = spec[0]
    if kind == "bool":
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on")
        return bool(value)
    if kind == "choice":
        if value not in spec[1]:
            raise ValueError(f"must be one of {spec[1]}")
        return value
    if kind == "str":
        v = str(value).strip().upper()
        if not re.fullmatch(r"[A-Z0-9_/\-]{3,20}", v):
            raise ValueError("invalid symbol")
        return v
    x = int(value) if kind == "int" else float(value)
    if not (spec[1] <= x <= spec[2]):
        raise ValueError(f"must be between {spec[1]} and {spec[2]}")
    return x


class Config:
    def __init__(self):
        self._lock = threading.RLock()
        self._d = dict(DEFAULTS)
        for p in (ROOT / "config.json", DATA / "runtime_config.json"):
            if p.exists():
                try:
                    self._d.update(json.loads(p.read_text()))
                except Exception:
                    pass

    def get(self, key, default=None):
        with self._lock:
            return self._d.get(key, default)

    def snapshot(self):
        with self._lock:
            return dict(self._d)

    def editable_snapshot(self):
        with self._lock:
            return {k: self._d.get(k) for k in EDITABLE}

    def update(self, changes: dict):
        clean, errors = {}, []
        for k, v in changes.items():
            if k not in EDITABLE:
                errors.append(f"{k}: not editable")
                continue
            try:
                clean[k] = _coerce(k, v)
            except (ValueError, TypeError) as e:
                errors.append(f"{k}: {e}")
        fast = clean.get("sma_fast", self.get("sma_fast"))
        slow = clean.get("sma_slow", self.get("sma_slow"))
        if fast >= slow:
            errors.append("sma_fast must be smaller than sma_slow")
        if errors:
            raise ValueError("; ".join(errors))
        with self._lock:
            self._d.update(clean)
            runtime = {k: self._d[k] for k in EDITABLE}
        (DATA / "runtime_config.json").write_text(json.dumps(runtime, indent=2))
        return clean


class StaticConfig:
    """Read-only config view used by the backtester (never touches disk)."""

    def __init__(self, d):
        self._d = dict(d)

    def get(self, key, default=None):
        return self._d.get(key, default)

    def snapshot(self):
        return dict(self._d)


cfg = Config()

# ---------------------------------------------------------------- secrets
SECRETS_PATH = DATA / "secrets.json"
SECRET_FIELDS = ["exchange_api_key", "exchange_api_secret", "telegram_bot_token",
                 "telegram_chat_id", "llm_api_key"]


def load_secrets():
    data = {}
    if SECRETS_PATH.exists():
        try:
            data = json.loads(SECRETS_PATH.read_text())
        except Exception:
            data = {}
    for f in SECRET_FIELDS:
        if not data.get(f):
            data[f] = os.environ.get("MAHLA_" + f.upper(), "")
    return data


def save_secrets(new: dict):
    data = load_secrets()
    for f in SECRET_FIELDS:
        v = (new.get(f) or "").strip()
        if v:
            data[f] = v
    SECRETS_PATH.write_text(json.dumps(data))
    try:
        os.chmod(SECRETS_PATH, 0o600)
    except OSError:
        pass


def secrets_status():
    s = load_secrets()
    return {f: bool(s.get(f)) for f in SECRET_FIELDS}
