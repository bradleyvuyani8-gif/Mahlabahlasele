"""Market data: exchange klines (with retry/backoff and latency measurement), CSV, synthetic."""
import csv
import json
import math
import random
import time
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .config import ROOT

INTERVAL_MS = {"1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000,
               "30m": 1_800_000, "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}
HOSTS = ["https://data-api.binance.vision", "https://api.binance.com", "https://api1.binance.com"]


@dataclass
class FetchResult:
    candles: list
    latency_ms: float
    source: str


def fetch_klines(symbol, interval, limit=300, timeout=6, retries=3):
    """Try several mirrors with exponential backoff; bounded by an overall deadline so a
    flaky mobile connection can never stall the trading loop for long."""
    deadline = time.monotonic() + timeout * 2.5
    last = None
    for attempt in range(retries):
        for host in HOSTS:
            if time.monotonic() > deadline:
                raise ConnectionError(f"klines deadline exceeded: {last}")
            url = f"{host}/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
            t0 = time.perf_counter()
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "mahlabahlasele/1.0"})
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    raw = json.loads(r.read())
                lat = (time.perf_counter() - t0) * 1000
                candles = [{"t": int(k[0]), "o": float(k[1]), "h": float(k[2]),
                            "l": float(k[3]), "c": float(k[4]), "v": float(k[5])} for k in raw]
                return FetchResult(candles, lat, "exchange")
            except Exception as e:  # network, HTTP 451 geo-block, JSON...
                last = e
        time.sleep(min(0.5 * 2 ** attempt, 4))
    raise ConnectionError(f"klines fetch failed: {last}")


def synthetic_series(n, seed=7, start=30000.0, interval_ms=300_000, t0=1_700_000_000_000):
    """Regime-switching random walk (trending up / trending down / mean-reverting)."""
    rng = random.Random(seed)
    price, t, out = start, t0, []
    while len(out) < n:
        kind = rng.choice(["up", "down", "range", "range"])
        seg = rng.randint(80, 260)
        vol = rng.choice([0.002, 0.003, 0.006])
        drift = {"up": 0.0006, "down": -0.0006, "range": 0.0}[kind]
        anchor = price
        for _ in range(seg):
            if len(out) >= n:
                break
            o = price
            pull = -0.05 * math.log(price / anchor) if kind == "range" else 0.0
            c = o * math.exp(drift + pull + rng.gauss(0, vol))
            h = max(o, c) * (1 + abs(rng.gauss(0, vol / 2)))
            l = min(o, c) * (1 - abs(rng.gauss(0, vol / 2)))
            out.append({"t": t, "o": o, "h": h, "l": l, "c": c, "v": rng.uniform(10, 100)})
            price, t = c, t + interval_ms
    return out


def _parse_time(v, idx):
    try:
        x = float(v)
        return int(x if x > 1e11 else x * 1000)
    except (TypeError, ValueError):
        pass
    try:
        return int(datetime.fromisoformat(str(v)).timestamp() * 1000)
    except Exception:
        return idx


def load_csv(path):
    p = Path(path)
    if not p.is_absolute():
        p = ROOT / p
    rows = []
    with open(p, newline="") as f:
        for i, r in enumerate(csv.DictReader(f)):
            r = {k.strip().lower(): v for k, v in r.items()}
            try:
                t = _parse_time(r.get("time") or r.get("timestamp") or r.get("date") or r.get("t"), i)
                rows.append({"t": t, "o": float(r["open"]), "h": float(r["high"]),
                             "l": float(r["low"]), "c": float(r["close"]),
                             "v": float(r.get("volume") or 0)})
            except (KeyError, ValueError):
                continue
    if len(rows) < 50:
        raise ValueError("CSV needs columns time,open,high,low,close,volume and >= 50 rows")
    rows.sort(key=lambda c: c["t"])
    return rows


_syn = {"series": None, "ptr": 0}


def _synthetic_window(limit, interval_ms):
    if _syn["series"] is None:
        _syn["series"] = synthetic_series(6000, interval_ms=interval_ms)
        _syn["ptr"] = limit + 50
    s, ptr = _syn["series"], _syn["ptr"]
    window = s[max(0, ptr - limit):ptr]
    _syn["ptr"] = ptr + 1 if ptr < len(s) else limit + 50
    return window


def get_candles(cfg, limit=300) -> FetchResult:
    src = cfg.get("data_source")
    if src == "exchange":
        return fetch_klines(cfg.get("symbol"), cfg.get("timeframe"), limit,
                            cfg.get("http_timeout"), cfg.get("http_retries"))
    t0 = time.perf_counter()
    if src == "csv":
        rows = load_csv(cfg.get("csv_path"))[-limit:]
    else:
        rows = _synthetic_window(limit, INTERVAL_MS.get(cfg.get("timeframe"), 300_000))
    return FetchResult(rows, (time.perf_counter() - t0) * 1000, src)
