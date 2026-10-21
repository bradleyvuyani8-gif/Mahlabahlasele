"""FastAPI controller: dashboard endpoints + static dashboard. All /api routes need X-Token."""
import hmac
import os
import secrets as pysecrets
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import Body, Depends, FastAPI, Header, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .alerts import alerts
from .backtest import backtest, walk_forward
from .config import DATA, ROOT, cfg, save_secrets, secrets_status
from .data import INTERVAL_MS, get_candles, load_csv, synthetic_series
from .engine import engine


def _token():
    t = os.environ.get("MAHLA_TOKEN")
    if t:
        return t
    f = DATA / "token.txt"
    if not f.exists():
        f.write_text(pysecrets.token_urlsafe(16))
        try:
            os.chmod(f, 0o600)
        except OSError:
            pass
    return f.read_text().strip()


TOKEN = _token()


def auth(x_token: Optional[str] = Header(None)):
    if not x_token or not hmac.compare_digest(x_token, TOKEN):
        raise HTTPException(401, "Invalid or missing token")


app = FastAPI(title="Mahlabahlasele", version="1.0.0")
api = Depends(auth)


@app.get("/api/ping")
def ping():
    return {"ok": True}


@app.get("/api/status", dependencies=[api])
def status():
    return engine.status()


@app.post("/api/bot/start", dependencies=[api])
def start():
    if engine.breaker.tripped:
        raise HTTPException(409, f"Circuit breaker is open: {engine.breaker.reason}. Reset it first.")
    engine.start()
    return engine.status()


@app.post("/api/bot/stop", dependencies=[api])
def stop():
    engine.stop()
    return engine.status()


@app.post("/api/breaker/reset", dependencies=[api])
def reset_breaker():
    engine.reset_breaker()
    return engine.status()


@app.get("/api/config", dependencies=[api])
def get_config():
    return cfg.editable_snapshot()


@app.post("/api/config", dependencies=[api])
def set_config(changes: Dict[str, Any] = Body(...)):
    old_mode = cfg.get("mode")
    try:
        applied = cfg.update(changes)
        if cfg.get("mode") != old_mode:
            engine.apply_mode_change()
    except (ValueError, RuntimeError) as e:
        if cfg.get("mode") != old_mode:
            cfg.update({"mode": old_mode})
        raise HTTPException(400, str(e))
    alerts.info(f"Config updated: {applied}")
    return cfg.editable_snapshot()


@app.get("/api/keys", dependencies=[api])
def get_keys():
    return secrets_status()  # booleans only - secrets are never sent back


@app.post("/api/keys", dependencies=[api])
def set_keys(body: Dict[str, str] = Body(...)):
    save_secrets(body)
    alerts.info("API keys / credentials updated")
    return secrets_status()


@app.get("/api/trades", dependencies=[api])
def trades(limit: int = 50):
    return list(engine.trades)[-limit:][::-1]


@app.get("/api/equity", dependencies=[api])
def equity():
    return list(engine.equity_hist)


@app.get("/api/learning", dependencies=[api])
def learning():
    return engine.learner.summary()


@app.get("/api/logs", dependencies=[api])
def logs(limit: int = 100):
    return list(alerts.recent)[-limit:][::-1]


@app.post("/api/backtest", dependencies=[api])
async def run_backtest(body: Dict[str, Any] = Body(default={})):
    """body: source (synthetic|csv|exchange), candles (int), walk_forward (bool), pretrain (bool)."""
    source = body.get("source", "synthetic")
    n = max(400, min(int(body.get("candles", 1500)), 20000))

    def work():
        if source == "csv":
            candles = load_csv(cfg.get("csv_path"))[-n:]
        elif source == "exchange":
            candles = get_candles(cfg, min(n, 1000)).candles
        else:
            candles = synthetic_series(n, seed=int(body.get("seed", 11)),
                                       interval_ms=INTERVAL_MS.get(cfg.get("timeframe"), 300_000))
        snap = cfg.snapshot()
        if body.get("pretrain") and source != "synthetic":
            learner = engine.learner  # learn from history into the live bot (real data only)
        else:
            learner = engine.learner.clone()  # sandboxed: live learning state untouched
        if body.get("walk_forward"):
            return walk_forward(candles, snap, learner)
        return backtest(candles, snap, learner, train=True)

    try:
        return await run_in_threadpool(work)
    except Exception as e:
        raise HTTPException(400, f"Backtest failed: {e}")


DASH = ROOT / "dashboard"


@app.get("/")
def index():
    return FileResponse(DASH / "index.html")


app.mount("/static", StaticFiles(directory=DASH), name="static")

if os.environ.get("MAHLA_AUTOSTART") == "1":
    @app.on_event("startup")
    def _autostart():
        engine.start()
