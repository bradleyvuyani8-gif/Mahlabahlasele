import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mahlabahlasele.alerts import NullAlerts
from mahlabahlasele.backtest import backtest, walk_forward
from mahlabahlasele.broker import PaperBroker
from mahlabahlasele.circuit_breaker import CircuitBreaker
from mahlabahlasele.config import DEFAULTS, StaticConfig
from mahlabahlasele.data import synthetic_series
from mahlabahlasele.indicators import atr, rsi, sma
from mahlabahlasele.learning import Learner, reward_from_trade

C = StaticConfig(DEFAULTS)


class Indicators(unittest.TestCase):
    def test_sma(self):
        self.assertEqual(sma([1, 2, 3, 4], 2), 3.5)

    def test_rsi_bounds(self):
        up = [float(i) for i in range(1, 40)]
        self.assertEqual(rsi(up, 14), 100.0)
        self.assertLess(rsi(list(reversed(up)), 14), 1)

    def test_atr(self):
        h, l, c = [10] * 20, [8] * 20, [9] * 20
        self.assertAlmostEqual(atr(h, l, c, 14), 2.0)


class Broker(unittest.TestCase):
    def order(self, side="buy"):
        return {"side": side, "qty": 1.0, "sl": 95.0 if side == "buy" else 105.0,
                "tp": 110.0 if side == "buy" else 90.0, "risk_amount": 5.0}

    def ctx(self):
        return {"regime": "up|calm", "strategy": "trend", "profile": "normal"}

    def test_stop_hits_first(self):
        b = PaperBroker(StaticConfig({**DEFAULTS, "slippage": 0, "fee_rate": 0}))
        b.open(self.order(), 100.0, 1, self.ctx())
        t = b.check_exit(111, 94, 100, 2)  # both levels inside one bar
        self.assertEqual(t["reason"], "stop_loss")
        self.assertAlmostEqual(t["net_pnl"], -5.0)

    def test_take_profit_short(self):
        b = PaperBroker(StaticConfig({**DEFAULTS, "slippage": 0, "fee_rate": 0}))
        b.open(self.order("sell"), 100.0, 1, self.ctx())
        t = b.check_exit(101, 89, 95, 2)
        self.assertEqual(t["reason"], "take_profit")
        self.assertAlmostEqual(t["net_pnl"], 10.0)


class Breaker(unittest.TestCase):
    def test_drawdown_trips_and_latches(self):
        cb = CircuitBreaker(StaticConfig({**DEFAULTS, "daily_loss_limit": 0.5}), NullAlerts())
        cb.update(1000, 0)
        self.assertTrue(cb.update(950, 1))
        self.assertFalse(cb.update(890, 2))
        self.assertFalse(cb.update(1000, 86_400_000 * 3))  # stays halted until manual reset
        cb.reset(1000)
        self.assertFalse(cb.tripped)

    def test_daily_limit_clears_next_day(self):
        cb = CircuitBreaker(StaticConfig({**DEFAULTS, "max_drawdown": 0.9}), NullAlerts())
        cb.update(1000, 0)
        self.assertFalse(cb.update(960, 1))
        self.assertTrue(cb.update(960, 86_400_000 + 5))

    def test_loss_streak(self):
        cb = CircuitBreaker(C, NullAlerts())
        for _ in range(5):
            cb.record_trade(-1)
        self.assertTrue(cb.tripped)


class Learning(unittest.TestCase):
    def test_reward_clipped(self):
        self.assertEqual(reward_from_trade({"net_pnl": 100, "risk_amount": 1}), 3.0)
        self.assertEqual(reward_from_trade({"net_pnl": -100, "risk_amount": 1}), -1.5)

    def test_learns_to_prefer_rewarded_arm(self):
        L = Learner(path=None, seed=1)
        for _ in range(40):
            L.update("up|calm", "trend", "wide", 1.5)
            L.update("up|calm", "meanrev", "wide", -1.0)
        picks = [L.choose("up|calm", ["trend", "meanrev"], frozen=True)[0] for _ in range(5)]
        self.assertEqual(set(picks), {"trend"})
        self.assertGreater(L.state["strat"]["trend"]["q"], L.state["strat"]["meanrev"]["q"])

    def test_persistence_roundtrip(self):
        import tempfile
        p = os.path.join(tempfile.mkdtemp(), "s.json")
        L = Learner(path=p)
        L.update("range|calm", "meanrev", "tight", 0.8)
        self.assertEqual(Learner(path=p).state["total"], 1)


class Backtest(unittest.TestCase):
    def test_runs_and_learns(self):
        candles = synthetic_series(2500, seed=3)
        L = Learner(path=None, seed=2)
        r = backtest(candles, DEFAULTS, L)
        self.assertGreater(r["trades"], 5)
        self.assertEqual(L.state["total"], r["trades"])
        self.assertTrue(r["max_drawdown_pct"] >= 0)

    def test_walk_forward_shape(self):
        out = walk_forward(synthetic_series(3000, seed=5), DEFAULTS, Learner(path=None, seed=4))
        self.assertIn("test_out_of_sample", out)

    def test_no_lookahead_candle_requirement(self):
        with self.assertRaises(ValueError):
            backtest(synthetic_series(100), DEFAULTS)


if __name__ == "__main__":
    unittest.main()
