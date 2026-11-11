"""Live / paper runner against OANDA.

Without ``execute`` the bot only logs the signals it would trade. With ``execute`` it places
limit orders (stop loss and take profit attached) on the configured account.
"""
from __future__ import annotations

import logging
import os
import time

import pandas as pd

from .config import Config
from .oanda import GRANULARITY_SECONDS, OandaClient
from .risk import position_size, quote_to_account
from .strategy import Signal, SMCStrategy

log = logging.getLogger("smcbot")


class LiveRunner:
    def __init__(self, cfg: Config, client: OandaClient, execute: bool = False):
        self.cfg, self.client, self.execute = cfg, client, execute
        self.strategy = SMCStrategy(cfg.strategy)
        self.bar_seconds = GRANULARITY_SECONDS[cfg.live.granularity]
        self.last_time: pd.Timestamp | None = None
        self.day = None
        self.day_start_nav = 0.0

    def warmup(self) -> None:
        df = self.client.candles(self.cfg.instrument, self.cfg.live.granularity, self.cfg.live.warmup_bars)
        for t, o, h, l, c in df.itertuples(name=None):
            self.strategy.on_bar(t, o, h, l, c)       # build structure; historical signals are ignored
        self.last_time = df.index[-1]
        log.info("Warmed up on %d bars to %s | HTF bias %+d", len(df), self.last_time, self.strategy.bias)

    def step(self) -> None:
        """Process any candles that have closed since the last call."""
        df = self.client.candles(self.cfg.instrument, self.cfg.live.granularity, 10)
        for t, o, h, l, c in df[df.index > self.last_time].itertuples(name=None):
            self.last_time = t
            decision = self.strategy.on_bar(t, o, h, l, c)
            log.info("Bar %s close=%.5f bias=%+d", t, c, self.strategy.bias)
            if decision.cancel_sides:
                self._cancel(decision.cancel_sides)
            for sig in decision.signals:
                self._handle(sig)

    def _cancel(self, sides: set) -> None:
        if not self.execute:
            return
        for o in self.client.pending_limit_orders(self.cfg.instrument):
            if (1 if float(o["units"]) > 0 else -1) in sides:
                log.info("Cancelling order %s (structure flipped)", o["id"])
                self.client.cancel_order(o["id"])

    def _handle(self, sig: Signal) -> None:
        cfg = self.cfg
        side = "BUY" if sig.side == 1 else "SELL"
        log.info("SIGNAL %s limit %.5f sl %.5f tp %.5f (%s)", side, sig.entry, sig.sl, sig.tp, sig.reason)
        if not self.execute:
            return
        nav = self.client.nav()
        today = pd.Timestamp.now(tz="UTC").date()
        if today != self.day:
            self.day, self.day_start_nav = today, nav
        if nav <= self.day_start_nav * (1 - cfg.risk.max_daily_loss):
            log.warning("Daily loss limit reached; skipping signal")
            return
        inst = cfg.instrument
        if len(self.client.open_trades(inst)) + len(self.client.pending_limit_orders(inst)) >= cfg.risk.max_open:
            log.info("Max open trades/orders reached; skipping signal")
            return
        # Candles are bid prices: longs fill at the ask, shorts are stopped/closed at the ask.
        spread = self.client.spread(inst)
        entry = sig.entry
        sl = sig.sl + (spread if sig.side == -1 else 0.0)
        tp = sig.tp + (spread if sig.side == -1 else 0.0)
        q2a = quote_to_account(inst, cfg.account_currency, entry, cfg.quote_to_account_rate)
        units = position_size(nav, cfg.risk.risk_per_trade, entry, sl, q2a, cfg.risk.max_leverage)
        if units < 1:
            log.warning("Position size rounds to zero; skipping signal")
            return
        expires = pd.Timestamp.now(tz="UTC") + pd.Timedelta(seconds=sig.expiry_bars * self.bar_seconds)
        resp = self.client.limit_order(inst, sig.side * units, entry, sl, tp, expires)
        if "orderCreateTransaction" in resp and "orderCancelTransaction" not in resp:
            log.info("Placed %s %d %s, order id %s", side, units, inst, resp["orderCreateTransaction"]["id"])
        else:
            log.error("Order not accepted: %s", resp)

    def run_forever(self) -> None:
        self.warmup()
        while True:
            now = time.time()
            time.sleep(self.bar_seconds - now % self.bar_seconds + 5)   # just after the next bar closes
            try:
                self.step()
            except Exception:                                           # keep running through API hiccups
                log.exception("Step failed; retrying next bar")


def client_from_env(environment: str) -> OandaClient:
    token, account = os.environ.get("OANDA_API_TOKEN"), os.environ.get("OANDA_ACCOUNT_ID")
    if not token or not account:
        raise SystemExit("Set OANDA_API_TOKEN and OANDA_ACCOUNT_ID in the environment.")
    return OandaClient(token, account, environment)
