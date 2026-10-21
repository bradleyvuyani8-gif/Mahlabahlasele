"""Backtester. Uses the exact same Pipeline, broker, breaker and learner as live trading, so
what you test is what runs. Decisions are made on a bar's close and filled at the NEXT bar's
open (no look-ahead). If SL and TP both fall inside one bar, the stop is assumed hit first."""
import math

from .alerts import NullAlerts
from .broker import PaperBroker
from .circuit_breaker import CircuitBreaker
from .config import StaticConfig
from .learning import Learner, reward_from_trade
from .pipeline import Pipeline


def _metrics(initial, curve, trades, candles, start_i):
    final = curve[-1] if curve else initial
    peak, max_dd = initial, 0.0
    for e in curve:
        peak = max(peak, e)
        max_dd = max(max_dd, (peak - e) / peak)
    wins = [t for t in trades if t["net_pnl"] > 0]
    gp = sum(t["net_pnl"] for t in wins)
    gl = -sum(t["net_pnl"] for t in trades if t["net_pnl"] <= 0)
    rs = [t["r_multiple"] for t in trades]
    mean_r = sum(rs) / len(rs) if rs else 0.0
    sd_r = math.sqrt(sum((r - mean_r) ** 2 for r in rs) / len(rs)) if len(rs) > 1 else 0.0
    bh = (candles[-1]["c"] / candles[start_i]["c"] - 1) if candles else 0.0
    step = max(1, len(curve) // 300)
    return {
        "initial": initial, "final": round(final, 2), "return_pct": round((final / initial - 1) * 100, 2),
        "buy_hold_pct": round(bh * 100, 2), "trades": len(trades),
        "win_rate": round(len(wins) / len(trades), 3) if trades else None,
        "profit_factor": round(gp / gl, 2) if gl > 0 else None,
        "avg_r": round(mean_r, 3), "r_sharpe": round(mean_r / sd_r, 3) if sd_r else None,
        "max_drawdown_pct": round(max_dd * 100, 2), "equity_curve": [round(x, 2) for x in curve[::step]],
    }


def backtest(candles, cfg_dict, learner=None, train=True, frozen=False):
    cfg = StaticConfig(cfg_dict)
    learner = learner or Learner(path=None)
    pipe = Pipeline(cfg, learner)
    broker = PaperBroker(cfg)
    breaker = CircuitBreaker(cfg, NullAlerts())
    initial = cfg.get("initial_balance")
    breaker.reset(initial)
    warm = max(cfg.get("sma_slow"), cfg.get("rsi_period"), 40) + 70
    if len(candles) < warm + 20:
        raise ValueError(f"need at least {warm + 20} candles, got {len(candles)}")
    trades, curve = [], []

    def settle(trade):
        if not trade:
            return
        r = reward_from_trade(trade)
        trade["reward"] = r
        if train and cfg.get("learning_enabled"):
            learner.update(trade["regime"], trade["strategy"], trade["profile"], r)
        breaker.record_trade(trade["net_pnl"])
        trades.append(trade)

    for i in range(warm, len(candles) - 1):
        c = candles[i]
        if broker.position:
            broker.position["bars"] += 1
            t = broker.check_exit(c["h"], c["l"], c["c"], c["t"])
            if not t and broker.position["bars"] >= cfg.get("max_hold_bars"):
                t = broker.close(c["c"], c["t"], "time_stop")
            settle(t)
        equity = broker.equity(c["c"])
        curve.append(equity)
        breaker.update(equity, c["t"])
        if breaker.tripped:
            if broker.position:
                settle(broker.close(c["c"], c["t"], "circuit_breaker"))
            continue
        if broker.position:
            continue
        window = candles[max(0, i - 299): i + 1]
        analysis, decision, order = pipe.plan(window, equity, frozen=frozen)
        if order:
            nxt = candles[i + 1]
            broker.open(order, nxt["o"], nxt["t"], {"regime": decision["regime"],
                        "strategy": decision["strategy"], "profile": decision["profile"]})
    if broker.position:
        last = candles[-1]
        settle(broker.close(last["c"], last["t"], "end_of_data"))
        curve.append(broker.balance)
    out = _metrics(initial, curve, trades, candles, warm)
    out["tripped"] = breaker.reason
    out["learning"] = learner.summary()
    return out


def walk_forward(candles, cfg_dict, learner=None, split=0.7):
    """Train (learn from rewards) on the first part, then test on unseen data with learning frozen."""
    cut = int(len(candles) * split)
    learner = learner or Learner(path=None)
    train = backtest(candles[:cut], cfg_dict, learner, train=True)
    test = backtest(candles[cut - 200:], cfg_dict, learner, train=False, frozen=True)  # overlap = warm-up only
    return {"train": train, "test_out_of_sample": test}
