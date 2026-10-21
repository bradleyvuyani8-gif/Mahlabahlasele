"""Paper broker (default) and an experimental live broker (ccxt). Single position at a time."""
from .config import load_secrets


class PaperBroker:
    def __init__(self, cfg):
        self.cfg = cfg
        self.balance = float(cfg.get("initial_balance"))
        self.position = None

    # ------------------------------------------------------------ helpers
    def _fill(self, side, price, adverse=True):
        s = self.cfg.get("slippage") if adverse else 0.0
        return price * (1 + s) if side == "buy" else price * (1 - s)

    def equity(self, mark):
        if not self.position:
            return self.balance
        p = self.position
        d = 1 if p["side"] == "buy" else -1
        return self.balance + (mark - p["entry"]) * p["qty"] * d

    def _execute(self, side, qty, price):
        """Hook for LiveBroker. Returns the fill price."""
        return price

    # ------------------------------------------------------------ trading
    def open(self, order, price, t, ctx):
        if self.position:
            return None
        fill = self._execute(order["side"], order["qty"], self._fill(order["side"], price))
        fee = order["qty"] * fill * self.cfg.get("fee_rate")
        self.balance -= fee
        self.position = {**order, "entry": fill, "t": t, "entry_fee": fee, "bars": 0, **ctx}
        return self.position

    def check_exit(self, high, low, close, t):
        p = self.position
        if not p:
            return None
        if p["side"] == "buy":
            if low <= p["sl"]:
                return self._close(p["sl"], t, "stop_loss", adverse=True)
            if high >= p["tp"]:
                return self._close(p["tp"], t, "take_profit", adverse=False)
        else:
            if high >= p["sl"]:
                return self._close(p["sl"], t, "stop_loss", adverse=True)
            if low <= p["tp"]:
                return self._close(p["tp"], t, "take_profit", adverse=False)
        return None

    def close(self, price, t, reason):
        return self._close(price, t, reason, adverse=True) if self.position else None

    def _close(self, price, t, reason, adverse):
        p = self.position
        opp = "sell" if p["side"] == "buy" else "buy"
        exit_px = self._execute(opp, p["qty"], self._fill(opp, price, adverse))
        d = 1 if p["side"] == "buy" else -1
        gross = (exit_px - p["entry"]) * p["qty"] * d
        fee = p["qty"] * exit_px * self.cfg.get("fee_rate")
        self.balance += gross - fee
        net = gross - fee - p["entry_fee"]
        trade = {"side": p["side"], "qty": p["qty"], "entry": p["entry"], "exit": exit_px,
                 "sl": p["sl"], "tp": p["tp"], "t_open": p["t"], "t_close": t, "reason": reason,
                 "net_pnl": net, "risk_amount": p["risk_amount"], "r_multiple": net / max(p["risk_amount"], 1e-9),
                 "regime": p["regime"], "strategy": p["strategy"], "profile": p["profile"],
                 "bars": p["bars"], "balance": self.balance}
        self.position = None
        return trade


class LiveBroker(PaperBroker):
    """EXPERIMENTAL. Market orders through ccxt; SL/TP are monitored by this software
    (not resting orders on the exchange), so they depend on the bot staying online."""

    def __init__(self, cfg):
        super().__init__(cfg)
        import ccxt  # optional dependency (requirements-live.txt)
        s = load_secrets()
        if not (s.get("exchange_api_key") and s.get("exchange_api_secret")):
            raise RuntimeError("Exchange API key/secret not set")
        self.ex = getattr(ccxt, cfg.get("exchange"))(
            {"apiKey": s["exchange_api_key"], "secret": s["exchange_api_secret"], "enableRateLimit": True})
        self.symbol = cfg.get("ccxt_symbol")
        if cfg.get("allow_short"):
            raise RuntimeError("Live mode: set allow_short=false (spot) or extend LiveBroker for futures")

    def _execute(self, side, qty, price):
        o = self.ex.create_market_order(self.symbol, side, qty)
        return float(o.get("average") or o.get("price") or price)
