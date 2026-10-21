"""Halts trading when risk limits are breached. Drawdown / loss-streak / data-error trips stay
latched until a human resets them; the daily-loss trip clears itself at the next UTC day."""


class CircuitBreaker:
    def __init__(self, cfg, alerts):
        self.cfg, self.alerts = cfg, alerts
        self.reset(cfg.get("initial_balance"))

    def reset(self, equity):
        self.peak = equity
        self.day = None
        self.day_start = equity
        self.consec_losses = 0
        self.data_errors = 0
        self.tripped = False
        self.kind = None
        self.reason = None

    @property
    def state(self):
        return "OPEN (halted)" if self.tripped else "CLOSED (trading)"

    def _trip(self, kind, reason):
        if not self.tripped:
            self.tripped, self.kind, self.reason = True, kind, reason
            self.alerts.critical(f"CIRCUIT BREAKER: {reason}. Trading halted.")

    def drawdown(self, equity):
        return 0.0 if not self.peak else max(0.0, (self.peak - equity) / self.peak)

    def update(self, equity, ts_ms):
        day = int(ts_ms // 86_400_000)
        if day != self.day:
            self.day, self.day_start = day, equity
            if self.tripped and self.kind == "daily":
                self.tripped, self.kind, self.reason = False, None, None
                self.alerts.info("Daily loss limit cleared for new day; trading resumed.")
        self.peak = max(self.peak, equity)
        dd = self.drawdown(equity)
        if dd >= self.cfg.get("max_drawdown"):
            self._trip("drawdown", f"drawdown {dd:.1%} >= limit {self.cfg.get('max_drawdown'):.1%}")
        day_loss = (self.day_start - equity) / self.day_start if self.day_start else 0
        if day_loss >= self.cfg.get("daily_loss_limit"):
            self._trip("daily", f"daily loss {day_loss:.1%} >= limit {self.cfg.get('daily_loss_limit'):.1%}")
        return not self.tripped

    def record_trade(self, net_pnl):
        self.consec_losses = self.consec_losses + 1 if net_pnl <= 0 else 0
        if self.consec_losses >= self.cfg.get("max_consecutive_losses"):
            self._trip("streak", f"{self.consec_losses} consecutive losing trades")

    def record_data_ok(self):
        self.data_errors = 0

    def record_data_error(self, err):
        self.data_errors += 1
        if self.data_errors >= self.cfg.get("max_data_errors"):
            self._trip("data", f"{self.data_errors} consecutive data failures ({err})")

    def status(self, equity):
        return {"state": self.state, "tripped": self.tripped, "kind": self.kind, "reason": self.reason,
                "drawdown": round(self.drawdown(equity), 4), "peak": round(self.peak, 2),
                "consecutive_losses": self.consec_losses, "data_errors": self.data_errors}
