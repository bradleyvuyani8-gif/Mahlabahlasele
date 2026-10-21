"""Strategy Agent: trend-following and mean-reversion signals; the learner decides which to trust."""
from ..learning import STRATEGIES


class StrategyAgent:
    role = "Strategy Agent"

    def __init__(self, learner):
        self.learner = learner

    def signals(self, a, cfg):
        out = {}
        # Trend following: ride established momentum, avoid already-exhausted moves.
        if a["trend"] == "up" and a["price"] > a["sma_fast"] and a["rsi"] < 78:
            out["trend"] = ("buy", min(1.0, a["trend_strength"] / 2), "uptrend, price above fast SMA")
        elif a["trend"] == "down" and a["price"] < a["sma_fast"] and a["rsi"] > 22:
            out["trend"] = ("sell", min(1.0, -a["trend_strength"] / 2), "downtrend, price below fast SMA")
        # Mean reversion: fade stretched moves back toward the mean.
        z = a["zscore"]
        if z is not None:
            if z < -1.5 and a["rsi"] < cfg.get("rsi_oversold") + 5:
                out["meanrev"] = ("buy", min(1.0, abs(z) / 3), f"oversold (z={z:.2f}, RSI={a['rsi']:.0f})")
            elif z > 1.5 and a["rsi"] > cfg.get("rsi_overbought") - 5:
                out["meanrev"] = ("sell", min(1.0, abs(z) / 3), f"overbought (z={z:.2f}, RSI={a['rsi']:.0f})")
        if not cfg.get("allow_short"):
            out = {k: v for k, v in out.items() if v[0] == "buy"}
        return {k: v for k, v in out.items() if v[1] >= cfg.get("signal_threshold") and k in STRATEGIES}

    def decide(self, a, cfg, frozen=False):
        sigs = self.signals(a, cfg)
        if not sigs:
            return {"action": "hold", "regime": a["regime"], "reason": "no qualifying signal"}
        if cfg.get("learning_enabled"):
            strat, profile, explored = self.learner.choose(a["regime"], list(sigs), frozen=frozen)
        else:
            strat, profile, explored = max(sigs, key=lambda k: sigs[k][1]), "normal", False
        side, strength, why = sigs[strat]
        return {"action": side, "strategy": strat, "profile": profile, "strength": round(strength, 3),
                "regime": a["regime"], "explored": explored,
                "reason": f"{strat}: {why}" + (" [exploring]" if explored else "")}
