"""Bar-by-bar backtester.

Candles are assumed to be bid prices; the ask is bid + spread. Longs enter at the ask and
exit at the bid, shorts the reverse. Fills are deliberately pessimistic:
  * an order created on bar t can fill from bar t+1 onwards;
  * if a bar touches both stop and target, the stop is assumed to have been hit first;
  * a bar that fills a limit order can stop it out but cannot hit its target;
  * stops that are gapped through fill at the bar open.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .config import Config
from .risk import pip_size, position_size, quote_to_account
from .strategy import Signal, SMCStrategy


@dataclass
class Trade:
    side: int
    signal_time: pd.Timestamp
    entry: float
    sl: float
    tp: float
    reason: str
    expires: int
    units: int = 0
    risk_dist: float = 0.0
    entry_time: pd.Timestamp | None = None
    exit_time: pd.Timestamp | None = None
    exit: float | None = None
    pnl: float = 0.0
    r: float = 0.0
    outcome: str = ""


@dataclass
class Result:
    trades: list
    equity: pd.Series
    stats: dict


def run_backtest(df: pd.DataFrame, cfg: Config) -> Result:
    strat = SMCStrategy(cfg.strategy)
    risk, bt = cfg.risk, cfg.backtest
    spread = bt.spread_pips * pip_size(cfg.instrument)
    balance = bt.initial_balance
    pending: list[Trade] = []
    open_: list[Trade] = []
    closed: list[Trade] = []
    curve = []
    day, day_start = None, balance

    def close(tr: Trade, price: float, time, outcome: str) -> None:
        nonlocal balance
        q2a = quote_to_account(cfg.instrument, cfg.account_currency, price, cfg.quote_to_account_rate)
        fees = 2 * bt.commission_per_lot * tr.units / 100_000
        tr.exit, tr.exit_time, tr.outcome = price, time, outcome
        tr.pnl = tr.units * (price - tr.entry) * tr.side * q2a - fees
        tr.r = (price - tr.entry) * tr.side / tr.risk_dist
        balance += tr.pnl
        open_.remove(tr)
        closed.append(tr)

    def check_exit(tr: Trade, o, h, l, time, allow_tp=True) -> None:
        if tr.side == 1:                                   # exits at bid
            if l <= tr.sl:
                close(tr, min(tr.sl, o), time, "sl")
            elif allow_tp and h >= tr.tp:
                close(tr, tr.tp, time, "tp")
        else:                                              # exits at ask
            if h + spread >= tr.sl:
                close(tr, max(tr.sl, o + spread), time, "sl")
            elif allow_tp and l + spread <= tr.tp:
                close(tr, tr.tp, time, "tp")

    for i, (time, o, h, l, c) in enumerate(df[["open", "high", "low", "close"]].itertuples(name=None)):
        if time.date() != day:
            day, day_start = time.date(), balance

        for tr in list(open_):
            check_exit(tr, o, h, l, time)

        for tr in list(pending):
            if i > tr.expires:
                pending.remove(tr)
                continue
            if tr.side == 1:
                hit, fill = l + spread <= tr.entry, min(tr.entry, o + spread)
            else:
                hit, fill = h >= tr.entry, max(tr.entry, o)
            if not hit:
                continue
            pending.remove(tr)
            q2a = quote_to_account(cfg.instrument, cfg.account_currency, fill, cfg.quote_to_account_rate)
            tr.units = position_size(balance, risk.risk_per_trade, fill, tr.sl, q2a, risk.max_leverage)
            if tr.units <= 0 or (fill - tr.sl) * tr.side <= 0:
                continue
            tr.entry, tr.entry_time, tr.risk_dist = fill, time, abs(fill - tr.sl)
            open_.append(tr)
            check_exit(tr, o, h, l, time, allow_tp=False)

        if risk.breakeven_at_r > 0:                        # takes effect from the next bar
            for tr in open_:
                best = h if tr.side == 1 else l + spread
                if (best - tr.entry) * tr.side >= risk.breakeven_at_r * tr.risk_dist:
                    tr.sl = tr.entry

        decision = strat.on_bar(time, o, h, l, c)
        pending = [p for p in pending if p.side not in decision.cancel_sides]
        halted = balance <= day_start * (1 - risk.max_daily_loss)
        if halted:
            pending.clear()
        for s in decision.signals:
            if not halted and len(open_) + len(pending) < risk.max_open:
                pending.append(_order(s, i))

        floating = sum(t.units * ((c if t.side == 1 else c + spread) - t.entry) * t.side
                       * quote_to_account(cfg.instrument, cfg.account_currency, c, cfg.quote_to_account_rate)
                       for t in open_)
        curve.append(balance + floating)

    equity = pd.Series(curve, index=df.index, name="equity")
    return Result(closed, equity, _stats(closed, equity, bt.initial_balance))


def _order(s: Signal, i: int) -> Trade:
    return Trade(s.side, s.time, s.entry, s.sl, s.tp, s.reason, i + s.expiry_bars)


def _stats(trades: list, equity: pd.Series, start: float) -> dict:
    n = len(trades)
    wins = [t.pnl for t in trades if t.pnl > 0]
    losses = [-t.pnl for t in trades if t.pnl <= 0]
    dd = (equity / equity.cummax() - 1).min() if len(equity) else 0.0
    end = float(equity.iloc[-1]) if len(equity) else start
    return {
        "trades": n,
        "win_rate": len(wins) / n if n else 0.0,
        "profit_factor": sum(wins) / sum(losses) if losses and sum(losses) > 0 else float("inf") if wins else 0.0,
        "avg_r": sum(t.r for t in trades) / n if n else 0.0,
        "net_profit": end - start,
        "return_pct": (end / start - 1) * 100,
        "max_drawdown_pct": float(dd) * 100,
        "final_equity": end,
    }


def trades_frame(trades: list) -> pd.DataFrame:
    cols = ["side", "signal_time", "entry_time", "exit_time", "entry", "sl", "tp", "exit", "units", "pnl", "r", "outcome", "reason"]
    return pd.DataFrame([{k: getattr(t, k) for k in cols} for t in trades], columns=cols)
