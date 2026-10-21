"""Reward-driven self-improvement.

Every closed trade produces a reward (its R-multiple: net PnL divided by the money risked,
clipped). The learner is a contextual bandit:

  context = market regime (trend x volatility)      e.g. "up|calm"
  arm     = strategy x risk profile                  e.g. "trend|wide"

Arm values are updated from realised rewards, then used to choose between the strategies that
currently have a signal (UCB + decaying epsilon exploration). Sparse arms borrow strength from
the strategy-level average (shrinkage), so it generalises before every cell has data.

The state is persisted, so the bot keeps improving across restarts - and you can pre-train it
on real historical data with the backtester.
"""
import copy
import json
import math
import os
import random
import threading

from .config import DATA

STRATEGIES = ["trend", "meanrev"]
PROFILES = {"tight": (1.0, 1.5), "normal": (1.5, 2.5), "wide": (2.0, 4.0)}  # (SL, TP) in ATRs
REWARD_CLIP = (-1.5, 3.0)


def reward_from_trade(trade):
    r = trade["net_pnl"] / max(trade["risk_amount"], 1e-9)
    return max(REWARD_CLIP[0], min(REWARD_CLIP[1], r))


class Learner:
    def __init__(self, path=DATA / "learning_state.json", params=None, seed=None):
        self.path = path
        self.p = {"alpha": 0.1, "eps_start": 0.30, "eps_floor": 0.05, "eps_decay": 0.995,
                  "ucb_c": 0.5, "shrink_k": 3}
        if params:
            self.p.update(params)
        self.lock = threading.RLock()
        self.rng = random.Random(seed)
        self.state = {"arms": {}, "strat": {}, "total": 0, "history": []}
        self.load()

    # ---------------------------------------------------------- persistence
    def load(self):
        if self.path and os.path.exists(self.path):
            try:
                with open(self.path) as f:
                    self.state = json.load(f)
            except Exception:
                pass

    def save(self):
        if not self.path:
            return
        tmp = f"{self.path}.tmp"
        with self.lock, open(tmp, "w") as f:
            json.dump(self.state, f)
        os.replace(tmp, self.path)  # atomic: a crash can't corrupt the learned state

    def clone(self):
        c = Learner(path=None, params=self.p)
        c.state = copy.deepcopy(self.state)
        return c

    # ---------------------------------------------------------- core
    @property
    def epsilon(self):
        return max(self.p["eps_floor"], self.p["eps_start"] * self.p["eps_decay"] ** self.state["total"])

    def _stat(self, table, key):
        return table.setdefault(key, {"n": 0, "q": 0.0, "wins": 0})

    def _value(self, regime, strategy, profile):
        arm = self._stat(self.state["arms"], f"{regime}|{strategy}|{profile}")
        g = self._stat(self.state["strat"], strategy)
        k = self.p["shrink_k"]
        return (arm["n"] * arm["q"] + k * g["q"]) / (arm["n"] + k), arm["n"]

    def choose(self, regime, strategies, frozen=False):
        """Pick (strategy, profile, explored) among strategies that currently have a signal."""
        with self.lock:
            arms = [(s, p) for s in strategies for p in PROFILES]
            if frozen:
                best = max(arms, key=lambda a: self._value(regime, *a)[0])
                return best[0], best[1], False
            if self.rng.random() < self.epsilon:
                a = self.rng.choice(arms)
                return a[0], a[1], True
            total = sum(self._stat(self.state["arms"], f"{regime}|{s}|{p}")["n"] for s, p in arms) + 1
            def ucb(a):
                v, n = self._value(regime, *a)
                return v + self.p["ucb_c"] * math.sqrt(math.log(total + 1) / (n + 1))
            best = max(arms, key=ucb)
            return best[0], best[1], False

    def update(self, regime, strategy, profile, reward):
        with self.lock:
            for table, key in ((self.state["arms"], f"{regime}|{strategy}|{profile}"),
                               (self.state["strat"], strategy)):
                s = self._stat(table, key)
                s["n"] += 1
                s["wins"] += 1 if reward > 0 else 0
                step = max(1.0 / s["n"], self.p["alpha"])  # sample mean first, then recency-weighted
                s["q"] += step * (reward - s["q"])
            self.state["total"] += 1
            h = self.state["history"]
            h.append(round(reward, 4))
            del h[:-500]
        self.save()

    # ---------------------------------------------------------- reporting
    def summary(self):
        with self.lock:
            h = self.state["history"]
            recent = h[-30:]
            prev = h[-60:-30]
            avg = lambda x: round(sum(x) / len(x), 4) if x else None
            arms = [{"arm": k, **v, "q": round(v["q"], 4)} for k, v in self.state["arms"].items()]
            arms.sort(key=lambda a: a["q"], reverse=True)
            return {
                "trades_learned": self.state["total"],
                "epsilon": round(self.epsilon, 4),
                "avg_reward_recent": avg(recent),
                "avg_reward_previous": avg(prev),
                "improving": (avg(recent) > avg(prev)) if recent and prev else None,
                "strategies": {k: {**v, "q": round(v["q"], 4)} for k, v in self.state["strat"].items()},
                "top_arms": arms[:8],
                "reward_curve": h[-200:],
            }
