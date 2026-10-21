"""Risk Management Agent: stop-loss, take-profit and position size from volatility and account risk."""
from ..learning import PROFILES


class RiskAgent:
    role = "Risk Management Agent"

    def size(self, a, d, equity, cfg):
        sl_m, tp_m = PROFILES[d["profile"]]
        price, atr = a["price"], a["atr"]
        stop, target = atr * sl_m, atr * tp_m
        if stop <= 0 or target / stop < cfg.get("min_rr"):
            return None
        cost = price * 2 * (cfg.get("fee_rate") + cfg.get("slippage"))  # est. round-trip friction
        if cost > cfg.get("max_cost_to_risk") * stop:
            return None  # the edge would be eaten by costs before it can show up
        risk_frac = cfg.get("risk_per_trade") * (0.5 if a["volatility"] == "wild" else 1.0)
        qty = (equity * risk_frac) / stop
        qty = min(qty, equity * cfg.get("max_leverage") / price)  # leverage cap
        if qty * price < cfg.get("min_notional"):
            return None
        buy = d["action"] == "buy"
        return {
            "side": d["action"], "qty": qty, "lots": qty / cfg.get("unit_per_lot"),
            "sl": price - stop if buy else price + stop,
            "tp": price + target if buy else price - target,
            "risk_amount": qty * stop, "rr": round(target / stop, 2),
        }
