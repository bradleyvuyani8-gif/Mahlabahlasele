"""Trading engine: the live loop that wires data -> agents -> broker -> reward learning."""
import json
import os
import threading
import time
from collections import deque

from .agents.crew import CrewAdvisor
from .alerts import alerts
from .broker import LiveBroker, PaperBroker
from .circuit_breaker import CircuitBreaker
from .config import DATA, cfg
from .data import get_candles
from .learning import Learner, reward_from_trade
from .pipeline import Pipeline

TRADES_FILE = DATA / "trades.jsonl"


def learner_params(c):
    return {"alpha": c.get("learn_alpha"), "eps_start": c.get("epsilon_start"),
            "eps_floor": c.get("epsilon_floor"), "eps_decay": c.get("epsilon_decay"),
            "ucb_c": c.get("ucb_c"), "shrink_k": c.get("shrink_k")}


class Engine:
    def __init__(self):
        self.lock = threading.RLock()
        self.learner = Learner(params=learner_params(cfg))
        self.pipeline = Pipeline(cfg, self.learner)
        self.advisor = CrewAdvisor()
        self.running = False
        self.thread = None
        self.trades = deque(maxlen=500)
        self.equity_hist = deque(maxlen=600)
        self.last = {"analysis": None, "decision": None, "order": None, "explain": None}
        self.latency_ms = None
        self.last_price = None
        self.last_tick_ts = None
        self.last_decision_t = None
        self.last_checked_t = 0
        self._load_trades()
        self._init_broker()

    # ------------------------------------------------------------ setup
    def _load_trades(self):
        if TRADES_FILE.exists():
            for line in TRADES_FILE.read_text().splitlines()[-500:]:
                try:
                    self.trades.append(json.loads(line))
                except ValueError:
                    pass

    def _init_broker(self):
        if cfg.get("mode") == "live":
            if os.environ.get("MAHLA_ALLOW_LIVE") != "1":
                raise RuntimeError("Live trading blocked: export MAHLA_ALLOW_LIVE=1 to enable")
            self.broker = LiveBroker(cfg)
        else:
            self.broker = PaperBroker(cfg)
        self.breaker = CircuitBreaker(cfg, alerts)
        self.breaker.reset(self.broker.equity(0))
        self.equity_hist.clear()

    def apply_mode_change(self):
        with self.lock:
            if self.broker.position:
                raise RuntimeError("Close the open position before changing mode")
            self.stop()
            self._init_broker()

    # ------------------------------------------------------------ control
    def start(self):
        with self.lock:
            if self.running:
                return
            self.running = True
            self.thread = threading.Thread(target=self._loop, daemon=True)
            self.thread.start()
            alerts.info(f"Bot started ({cfg.get('mode')} mode, {cfg.get('symbol')} {cfg.get('timeframe')})")

    def stop(self):
        if self.running:
            self.running = False
            alerts.info("Bot stopped")

    def reset_breaker(self):
        with self.lock:
            self.breaker.reset(self.broker.equity(self.last_price or 0))
            alerts.info("Circuit breaker reset by user")

    def _loop(self):
        while self.running:
            try:
                self.tick()
            except Exception as e:
                alerts.error(f"tick failed: {type(e).__name__}: {e}")
            end = time.time() + cfg.get("poll_seconds")
            while self.running and time.time() < end:
                time.sleep(0.5)

    # ------------------------------------------------------------ one cycle
    def tick(self):
        with self.lock:
            try:
                res = get_candles(cfg, 300)
            except Exception as e:
                self.breaker.record_data_error(e)
                alerts.warn(f"data error: {e}")
                return
            self.breaker.record_data_ok()
            self.latency_ms = round(res.latency_ms, 1)
            self.last_tick_ts = time.time()
            candles = res.candles
            closed = candles[:-1] if res.source == "exchange" else candles  # drop forming candle
            if len(closed) < 5:
                return
            price = candles[-1]["c"]
            self.last_price = price
            now_t = candles[-1]["t"]
            slow_link = res.latency_ms > cfg.get("max_latency_ms")
            if slow_link:
                alerts.warn(f"high API latency {res.latency_ms:.0f} ms - new entries paused")

            self._manage_position(closed, price, now_t)
            equity = self.broker.equity(price)
            self.equity_hist.append({"t": now_t, "equity": round(equity, 2)})
            self.breaker.update(equity, now_t)

            if self.breaker.tripped:
                if self.broker.position:
                    self._on_trade(self.broker.close(price, now_t, "circuit_breaker"))
                return
            if self.broker.position or slow_link or closed[-1]["t"] == self.last_decision_t:
                return
            self.last_decision_t = closed[-1]["t"]

            analysis, decision, order = self.pipeline.plan(closed, equity)
            self.last.update(analysis=analysis, decision=decision, order=order)
            if order:
                pos = self.broker.open(order, price, now_t, {
                    "regime": decision["regime"], "strategy": decision["strategy"], "profile": decision["profile"]})
                alerts.trade(f"OPEN {order['side'].upper()} {order['qty']:.6f} {cfg.get('symbol')} @ {pos['entry']:.2f} "
                             f"SL {order['sl']:.2f} TP {order['tp']:.2f} | {decision['reason']}")
                if cfg.get("crew_enabled"):
                    threading.Thread(target=self._explain, args=(analysis, decision, order), daemon=True).start()
                else:
                    self.last["explain"] = None

    def _manage_position(self, closed, price, now_t):
        p = self.broker.position
        if not p:
            return
        trade = None
        for c in closed:  # candles that completed since the last poll (catches spikes between polls)
            if c["t"] > max(p["t"], self.last_checked_t):
                p["bars"] += 1
                trade = self.broker.check_exit(c["h"], c["l"], c["c"], c["t"])
                if trade:
                    break
        self.last_checked_t = closed[-1]["t"]
        if not trade:
            trade = self.broker.check_exit(price, price, price, now_t)
        if not trade and p["bars"] >= cfg.get("max_hold_bars"):
            trade = self.broker.close(price, now_t, "time_stop")
        if trade:
            self._on_trade(trade)

    def _on_trade(self, trade):
        if not trade:
            return
        reward = reward_from_trade(trade)
        trade["reward"] = round(reward, 4)
        if cfg.get("learning_enabled"):
            self.learner.update(trade["regime"], trade["strategy"], trade["profile"], reward)
        self.breaker.record_trade(trade["net_pnl"])
        self.trades.append(trade)
        with open(TRADES_FILE, "a") as f:
            f.write(json.dumps(trade) + "\n")
        alerts.trade(f"CLOSE {trade['side'].upper()} via {trade['reason']} | PnL {trade['net_pnl']:+.2f} "
                     f"({trade['r_multiple']:+.2f}R) | reward {reward:+.2f} | balance {trade['balance']:.2f}")

    def _explain(self, analysis, decision, order):
        self.last["explain"] = self.advisor.explain(analysis, decision, order)
        alerts.info("Crew review: " + str(self.last["explain"])[:300])

    # ------------------------------------------------------------ reporting
    def status(self):
        with self.lock:
            price = self.last_price or 0
            equity = self.broker.equity(price) if price else self.broker.balance
            p = self.broker.position
            wins = sum(1 for t in self.trades if t["net_pnl"] > 0)
            return {
                "running": self.running, "mode": cfg.get("mode"), "symbol": cfg.get("symbol"),
                "timeframe": cfg.get("timeframe"), "data_source": cfg.get("data_source"),
                "equity": round(equity, 2), "balance": round(self.broker.balance, 2),
                "initial_balance": cfg.get("initial_balance"), "price": price,
                "latency_ms": self.latency_ms, "last_tick": self.last_tick_ts,
                "position": ({k: p[k] for k in ("side", "qty", "entry", "sl", "tp", "strategy", "profile", "bars")}
                             if p else None),
                "breaker": self.breaker.status(equity),
                "trades": len(self.trades), "win_rate": round(wins / len(self.trades), 3) if self.trades else None,
                "last": self.last, "crew_available": self.advisor.available,
            }


engine = Engine()
