# Forex SMC Bot

A forex trading bot built on Smart Money Concepts (SMC), with a backtester and an OANDA
connection for paper or live trading.

> **Risk warning.** This is software, not financial advice. The strategy has **not** been
> shown to be profitable: it has only been checked on synthetic data, where it loses money
> to the spread, as any strategy must. Backtest it on real candles and run it on a practice
> account before even considering real money. Leveraged forex can lose your whole deposit.

## The strategy

All logic runs on closed candles, one bar at a time, so the backtest and the live bot use
exactly the same code and cannot look ahead.

| Step | SMC concept | Rule |
|------|-------------|------|
| 1 | **HTF bias** | Direction of the last break of structure on the higher timeframe (default 4h). Trade only that way. |
| 2 | **Liquidity sweep** | Price trades through a recent swing low (for longs) and closes back above it within 3 bars. |
| 3 | **BOS / CHoCH** | Within 30 bars of the sweep, an entry-timeframe candle closes beyond the last swing high. |
| 4 | **Displacement** | The breaking leg contains a fair value gap (3-candle imbalance). |
| 5 | **Order block** | Last opposite-coloured candle at the origin of that leg. It must lie in the discount half of the leg (premium for shorts). |
| 6 | **Entry** | Limit order at the edge of the order block, cancelled after 24 bars or if structure flips. |
| 7 | **Exit** | Stop beyond the order block plus 0.1 ATR; target at 2R. |

Shorts mirror longs. Signals are only taken during the configured sessions (07:00-17:00 UTC by default).

Risk controls: fixed-fraction sizing (1% of equity per trade), a leverage cap, one
position/order at a time, and a daily loss limit (3%) after which no new trades open.

Every number above is a setting in `config.example.toml` / `smcbot/config.py`.

## Setup

```bash
cd forex-smc-bot
pip install -r requirements.txt
cp config.example.toml config.toml
python -m pytest
```

## Backtest

```bash
python -m smcbot backtest --config config.toml --data EURUSD_M15.csv --trades-out trades.csv
```

The CSV needs `time,open,high,low,close` columns with UTC times on the entry timeframe
(the higher timeframe is built from it). With OANDA credentials you can download candles:

```bash
python -m smcbot fetch --config config.toml --count 5000 --out EURUSD_M15.csv
```

`--synthetic 30000` runs on random-walk data instead; that only proves the code runs.

The backtester is pessimistic on purpose: spread is charged, orders fill no earlier than the
bar after the signal, and when one bar touches both stop and target the stop wins.

## Paper and live trading (OANDA)

```bash
export OANDA_API_TOKEN=...      # never commit these
export OANDA_ACCOUNT_ID=...

python -m smcbot live --config config.toml              # logs signals, places nothing
python -m smcbot live --config config.toml --execute    # places orders on the practice account
```

Real-money trading needs `environment = "live"` in the config **and** both `--execute` and
`--confirm-live`. Orders are sent as limit orders with stop loss and take profit attached,
so open trades stay protected if the bot stops.

Limits to know about:

- The OANDA code path has not been run against a real account yet. Start with the dry run.
- One instrument per process; run several processes for several pairs.
- The daily loss limit resets if the bot restarts mid-day.
- Cross pairs whose quote currency is not the account currency need `quote_to_account_rate` set.

## Layout

| File | Purpose |
|------|---------|
| `smcbot/structure.py` | Swings, BOS/CHoCH, liquidity sweeps |
| `smcbot/strategy.py` | Entry model: bias, FVG, order block, signal |
| `smcbot/risk.py` | Position sizing |
| `smcbot/backtest.py` | Backtester and statistics |
| `smcbot/oanda.py`, `smcbot/live.py` | Broker client and live runner |

## MetaTrader 5

`mt5/SMC_Bot.mq5` is the same strategy as a single-file Expert Advisor.

1. In MT5: **File > Open Data Folder**, then copy `SMC_Bot.mq5` into `MQL5/Experts/`.
2. Open it in MetaEditor (F4) and press **Compile** (F7).
3. Drag **SMC_Bot** from the Navigator onto the chart of the entry timeframe (e.g. EURUSD M15)
   and enable **Algo Trading**.

Session hours are in **broker server time**, not UTC (defaults 9-19, which is 07:00-17:00 UTC
on a GMT+2 server). The EA has not been compiled or run yet; test it in the Strategy Tester
and on a demo account first.

## TradingView

`tradingview/SMC_Bot.pine` is the same strategy in Pine Script v6.

1. Open a chart on the entry timeframe (e.g. EURUSD 15m), open the **Pine Editor**, paste the
   file, then **Save** and **Add to chart**.
2. The **Strategy Tester** tab shows the backtest; set the spread/commission in the strategy's
   Properties to match your broker.
3. For automation, create an alert on the strategy with **alert() function calls only**. Each
   signal sends JSON (`symbol, side, entry, sl, tp, risk_pct`) that a webhook can forward to a
   broker. Webhook alerts need a paid TradingView plan, and TradingView cannot send orders to
   MT5 by itself.

Position size assumes the pair's quote currency is your account currency. The script has not
been compiled or run on TradingView yet.
