"""Optional CrewAI layer.

The deterministic agents above make every trading decision (fast, testable, works offline in
Termux). This module wraps the SAME three roles as CrewAI agents that write a plain-English
review of each decision for the dashboard. It is advisory only: it never changes an order, so an
LLM timeout can't add latency or risk. Needs `pip install crewai` + an LLM key (usually not
installable on Android; run it under proot-distro or leave crew_enabled=false).
"""
import json
import os

try:
    from crewai import Agent, Crew, Process, Task
    HAVE_CREW = True
except Exception:  # not installed / unsupported platform
    HAVE_CREW = False

from ..config import load_secrets


def template_explain(analysis, decision, order):
    if not analysis:
        return "Warming up."
    base = (f"Regime {analysis['regime']} (RSI {analysis['rsi']:.0f}, ATR {analysis['atr_pct']:.2%}). "
            f"Decision: {decision['action'].upper()} - {decision['reason']}.")
    if order:
        base += (f" Risk plan: {order['qty']:.6f} units, SL {order['sl']:.2f}, TP {order['tp']:.2f}, "
                 f"R:R {order['rr']}, risking {order['risk_amount']:.2f}.")
    return base


class CrewAdvisor:
    def __init__(self):
        self._crew = None

    @property
    def available(self):
        return HAVE_CREW

    def build(self):
        key = load_secrets().get("llm_api_key")
        if key:
            os.environ.setdefault("OPENAI_API_KEY", key)
        analyst = Agent(role="Market Analyst", goal="Describe the current market regime accurately",
                        backstory="Quant analyst fluent in SMA, RSI and ATR.", allow_delegation=False)
        strategist = Agent(role="Strategy Agent", goal="Judge whether the proposed trade fits the regime",
                           backstory="Blends trend-following with mean-reversion.", allow_delegation=False)
        risk = Agent(role="Risk Management Agent", goal="Protect capital; challenge sizing, stops and targets",
                     backstory="Former risk officer; capital preservation comes first.", allow_delegation=False)
        tasks = [
            Task(description="Summarise this market data in 2 sentences: {market}",
                 expected_output="Two sentences.", agent=analyst),
            Task(description="Given the summary, review this decision in 2 sentences: {decision}",
                 expected_output="Two sentences.", agent=strategist),
            Task(description="Review this order's stop, target and size in 2 sentences: {order}",
                 expected_output="Two sentences with a verdict.", agent=risk),
        ]
        return Crew(agents=[analyst, strategist, risk], tasks=tasks, process=Process.sequential)

    def explain(self, analysis, decision, order):
        fallback = template_explain(analysis, decision, order)
        if not HAVE_CREW:
            return fallback
        try:
            if self._crew is None:
                self._crew = self.build()
            out = self._crew.kickoff(inputs={"market": json.dumps(analysis, default=str),
                                             "decision": json.dumps(decision, default=str),
                                             "order": json.dumps(order, default=str)})
            return str(out)
        except Exception as e:
            return f"{fallback} (crew review unavailable: {e})"
