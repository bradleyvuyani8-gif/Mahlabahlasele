"""Dependency-free technical indicators (pure Python so they install anywhere, incl. Termux)."""
import math


def sma(values, n):
    if len(values) < n:
        return None
    return sum(values[-n:]) / n


def rsi(closes, n=14):
    """Wilder's RSI."""
    if len(closes) < n + 1:
        return None
    gains = losses = 0.0
    for i in range(1, n + 1):
        d = closes[i] - closes[i - 1]
        gains += max(d, 0.0)
        losses += max(-d, 0.0)
    ag, al = gains / n, losses / n
    for i in range(n + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        ag = (ag * (n - 1) + max(d, 0.0)) / n
        al = (al * (n - 1) + max(-d, 0.0)) / n
    if al == 0:
        return 100.0
    return 100.0 - 100.0 / (1.0 + ag / al)


def true_ranges(highs, lows, closes):
    out = []
    for i in range(1, len(closes)):
        out.append(max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1])))
    return out


def atr(highs, lows, closes, n=14):
    trs = true_ranges(highs, lows, closes)
    if len(trs) < n:
        return None
    return sum(trs[-n:]) / n


def zscore(values, n=20):
    if len(values) < n:
        return None
    w = values[-n:]
    mean = sum(w) / n
    var = sum((x - mean) ** 2 for x in w) / n
    sd = math.sqrt(var)
    return 0.0 if sd == 0 else (values[-1] - mean) / sd
