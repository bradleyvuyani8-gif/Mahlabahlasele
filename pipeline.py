"""One decision cycle shared by the live engine and the backtester: analyse -> decide -> size."""
from .agents.analyst import MarketAnalyst
from .agents.risk import RiskAgent
from .agents.strategy import StrategyAgent


class Pipeline:
    def __init__(self, cfg, learner):
        self.cfg = cfg
        self.analyst, self.risk = MarketAnalyst(), RiskAgent()
        self.strategy = StrategyAgent(learner)

    def plan(self, candles, equity, frozen=False):
        analysis = self.analyst.analyze(candles, self.cfg)
        if not analysis:
            return None, {"action": "hold", "reason": "warming up (not enough candles)"}, None
        decision = self.strategy.decide(analysis, self.cfg, frozen=frozen)
        order = None
        if decision["action"] in ("buy", "sell"):
            order = self.risk.size(analysis, decision, equity, self.cfg)
            if order is None:
                decision = {**decision, "action": "hold", "reason": decision["reason"] + " | rejected by risk"}
        return analysis, decision, order
