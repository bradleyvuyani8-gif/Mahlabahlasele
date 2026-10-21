# Mahlabahlasele

A self-improving, multi-agent algorithmic trading system built to run in **Termux** on Android.
Paper trading is the default. Live trading is experimental and double-locked.

> Not financial advice. No strategy here is proven profitable. Run in paper mode, backtest on
> real data, and only risk money you can afford to lose.

## How it works

```
 candles ─► Market Analyst ─► Strategy Agent ─► Risk Manager ─► Broker ─► closed trade
 (exchange   trend, RSI, ATR   trend-following     SL / TP /       paper or     │
  / CSV /    volatility regime  + mean-reversion    position size   live         │
  synthetic)       ▲            chosen by learner        ▲                       │
                   │                  ▲                 │                       ▼
                   │                  └──── reward (R-multiple) ──── Learner ◄───┘
                   └──────────── Circuit breaker watches equity, losses, data errors
```

| Piece | File |
|---|---|
| Market Analyst Agent | `mahlabahlasele/agents/analyst.py` |
| Strategy Agent | `mahlabahlasele/agents/strategy.py` |
| Risk Management Agent | `mahlabahlasele/agents/risk.py` |
| CrewAI wrapper (optional commentary) | `mahlabahlasele/agents/crew.py` |
| Reward-based learning | `mahlabahlasele/learning.py` |
| Circuit breaker | `mahlabahlasele/circuit_breaker.py` |
| Backtester + walk-forward | `mahlabahlasele/backtest.py` |
| Live loop | `mahlabahlasele/engine.py` |
| FastAPI backend | `mahlabahlasele/api.py` |
| Dashboard | `dashboard/` |

### CrewAI: what it does here, and why
The three agents make decisions with plain, deterministic Python. That keeps decisions fast
(no LLM round-trip during volatile markets), testable, and able to run offline on a phone.
`agents/crew.py` wraps the same three roles as CrewAI agents that write a plain-English review of
each trade for the dashboard. It is advisory only and never changes an order. CrewAI pulls in heavy
native dependencies that usually do not install on Android; if it fails, the bot is unaffected.
If you want it, run it under `proot-distro` or on a PC, set an LLM key in the dashboard, and turn on
"CrewAI trade commentary".

### How the bot "always improves"
Every closed trade is scored with a **reward**: net profit divided by the money risked (an
R-multiple, clipped to -1.5..+3, fees included). The learner is a contextual bandit:

- **context**: market type, for example `up|calm` or `range|wild`
- **arms**: strategy (`trend` or `meanrev`) x risk style (`tight`, `normal`, `wide` stops)
- It picks among the strategies that currently have a signal using each arm's average reward plus
  an exploration bonus (UCB) and a decaying random-exploration rate (floor 5%).
- Arms with little data borrow from the strategy-level average, so it generalises early.
- State is saved atomically to `data/learning_state.json`, so learning survives restarts.
- The backtester uses the same learner, so you can **pre-train** on real history (API:
  `pretrain: true` with a non-synthetic source).

Be realistic: this learns *which of its existing strategies and stop styles to trust in each regime*.
It does not invent new strategies, and it needs on the order of hundreds of trades per regime before
the estimates mean much. Early on, expect noise.

### Backtest results shipped with this repo (read this)
Synthetic data (regime-switching random walk), walk-forward, 3 seeds, 5-minute candles, default
0.1% fees: out-of-sample returns of -5.8%, -1.8%, +5.6% on only 20, 4 and 25 trades. That is noise,
not edge. The synthetic series exist to prove the machinery works, not that the strategies profit.
Judge the strategies on **your own real historical data** (`data_source: csv`) with 100+ trades.
A cost gate (`max_cost_to_risk`) skips trades where fees plus slippage would eat the stop distance;
on 5-minute candles with 0.1% fees that blocks many trades, so try 15m or 1h candles.

## Install (Termux)

```bash
pkg install git          # if needed
git clone <your repo> mahlabahlasele && cd mahlabahlasele   # or unzip it
bash setup_termux.sh
./run.sh
```
Open `http://127.0.0.1:8000` in your phone browser and paste the token printed in Termux
(also saved in `data/token.txt`). In the dashboard: set parameters, run a backtest, then press
**Start bot**.

Run it in the background: `tmux new -s mahla` then `./run.sh`, detach with `Ctrl+b d`.

## Latency and connectivity
- Data fetches try three Binance mirrors with exponential backoff under an overall deadline, so a
  weak connection cannot stall the loop.
- If API latency exceeds `max_latency_ms` (default 3 s), **new entries pause**; open positions are
  still managed.
- Each poll checks all candles that closed since the last poll for stop/target hits, so spikes
  between polls are caught. Between polls, a stop can still gap past its level (slippage is modelled).
- `run.sh` holds a wake-lock and restarts the server after a crash. Also disable battery
  optimisation for Termux in Android settings, or the OS may kill it.
- Binance blocks some countries (HTTP 451). Use `data_source: csv` or `synthetic`, or edit `HOSTS`
  in `data.py` for another exchange.

## Safety features
- **Circuit breaker**: halts and flattens on drawdown limit, daily loss limit, N losing trades in a
  row, or N consecutive data failures. Drawdown/streak/data trips stay latched until you press
  Reset; the daily trip clears itself next UTC day.
- **Live trading is locked**: needs mode `live` *and* `MAHLA_ALLOW_LIVE=1` *and* `pip install ccxt`
  *and* API keys. `LiveBroker` is experimental and untested against a real exchange; its stops are
  software-side, not resting exchange orders, so they only work while the bot is running. Create keys with
  **trade permission only, withdrawals disabled**, and test with tiny size first.
- Dashboard API needs a token; server binds to `127.0.0.1` by default. Stored keys are never sent
  back to the browser.
- Logs: `logs/mahlabahlasele.log` (rotating), live feed on the dashboard, optional Telegram and
  Termux notifications for trades, warnings and breaker trips.

## Configuration
Defaults live in `mahlabahlasele/config.py`; override in `config.json` or via the dashboard
(saved to `data/runtime_config.json`). Set `allow_short: false` for spot markets. For forex set
`unit_per_lot: 100000` to see lot sizes (the order dict includes `lots`).

## Tests
```bash
python -m unittest discover -s tests -v
```
Covers indicators, broker SL/TP logic, circuit breaker, reward learning, persistence, backtester.
The FastAPI layer and live exchange paths were not exercised in the build environment (no network or
FastAPI install there). Run the server once and check the dashboard before relying on it.

## API (all `/api/*` need header `X-Token`)
`GET /status` `POST /bot/start` `POST /bot/stop` `POST /breaker/reset` `GET|POST /config`
`GET|POST /keys` `GET /trades` `GET /equity` `GET /learning` `GET /logs` `POST /backtest`

