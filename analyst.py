"""Market Analyst Agent: turns raw candles into a structured read of trend, momentum and volatility."""
from statistics import median

from ..indicators import atr, rsi, sma, zscore


class MarketAnalyst:
    role = "Market Analyst"

    def analyze(self, candles, cfg):
        fast_n, slow_n = cfg.get("sma_fast"), cfg.get("sma_slow")
        rsi_n, atr_n = cfg.get("rsi_period"), cfg.get("atr_period")
        if len(candles) < max(slow_n, rsi_n + 1, atr_n + 1, 25) + 60:
            return None
        closes = [c["c"] for c in candles]
        highs = [c["h"] for c in candles]
        lows = [c["l"] for c in candles]
        price = closes[-1]
        fast, slow = sma(closes, fast_n), sma(closes, slow_n)
        a = atr(highs, lows, closes, atr_n)
        if not a or a <= 0:
            return None
        atr_pct = a / price
        # volatility regime: current ATR% vs its own recent median
        hist = []
        for k in range(60, 0, -4):
            sub = slice(0, len(closes) - k + 1)
            v = atr(highs[sub], lows[sub], closes[sub], atr_n)
            if v:
                hist.append(v / closes[sub][-1])
        vol = "wild" if hist and atr_pct > 1.5 * median(hist) else "calm"
        # trend strength: SMA spread measured in units of typical volatility
        strength = ((fast - slow) / price) / atr_pct
        trend = "up" if strength > 0.5 else "down" if strength < -0.5 else "range"
        return {
            "price": price, "sma_fast": fast, "sma_slow": slow, "rsi": rsi(closes, rsi_n),
            "atr": a, "atr_pct": atr_pct, "zscore": zscore(closes, 20),
            "trend": trend, "trend_strength": strength, "volatility": vol,
            "regime": f"{trend}|{vol}", "t": candles[-1]["t"],
        }
