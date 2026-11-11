"""SMC entry model.

1. Bias       - higher-timeframe structure (last BOS/CHoCH) sets the trade direction.
2. Liquidity  - price sweeps a recent swing low (longs) / swing high (shorts).
3. Shift      - the entry timeframe then closes through structure (BOS or CHoCH) in the
                bias direction, leaving a fair value gap (displacement).
4. Entry      - limit order at the order block the move launched from, which must sit in
                the discount (longs) / premium (shorts) half of the breaking leg.
5. Risk       - stop beyond the order block, target at a fixed reward:risk multiple.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from .config import StrategyConfig
from .structure import Break, StructureTracker, Sweep


@dataclass
class Signal:
    side: int                 # +1 long, -1 short
    entry: float
    sl: float
    tp: float
    time: pd.Timestamp        # close time of the bar that produced the signal
    expiry_bars: int
    reason: str


@dataclass
class Decision:
    signals: list = field(default_factory=list)
    cancel_sides: set = field(default_factory=set)   # pending orders on these sides are invalid


class SMCStrategy:
    def __init__(self, cfg: StrategyConfig):
        self.cfg = cfg
        self.ltf = StructureTracker(cfg.swing_len, cfg.sweep_reclaim_bars)
        self.htf = StructureTracker(cfg.htf_swing_len)
        self._htf_bucket = None
        self._htf_bar: list[float] | None = None
        self.atr: float | None = None
        self._atr_n = 0
        self._last_sweep = {1: -10**9, -1: -10**9}

    # -- helpers -----------------------------------------------------------------
    def _update_atr(self, h: float, l: float) -> None:
        c = self.ltf.c
        tr = h - l if not c else max(h - l, abs(h - c[-1]), abs(l - c[-1]))
        self._atr_n += 1
        k = min(self._atr_n, self.cfg.atr_len)
        self.atr = tr if self.atr is None else self.atr + (tr - self.atr) / k

    def _update_htf(self, time: pd.Timestamp, o: float, h: float, l: float, c: float) -> None:
        """Aggregate entry-timeframe bars; only *completed* HTF bars reach the HTF tracker."""
        bucket = time.floor(self.cfg.htf)
        if bucket != self._htf_bucket:
            if self._htf_bar is not None:
                self.htf.update(*self._htf_bar)
            self._htf_bucket, self._htf_bar = bucket, [o, h, l, c]
        else:
            b = self._htf_bar
            b[1], b[2], b[3] = max(b[1], h), min(b[2], l), c

    def _in_session(self, time: pd.Timestamp) -> bool:
        s = self.cfg.sessions
        return not s or any(a <= time.hour < b for a, b in s)

    @property
    def bias(self) -> int:
        return self.htf.trend

    # -- main entry point ----------------------------------------------------------
    def on_bar(self, time: pd.Timestamp, o: float, h: float, l: float, c: float) -> Decision:
        """Feed one closed entry-timeframe bar (``time`` = bar open time, UTC)."""
        self._update_htf(time, o, h, l, c)
        self._update_atr(h, l)
        decision = Decision()
        for ev in self.ltf.update(o, h, l, c):
            if isinstance(ev, Sweep):
                self._last_sweep[ev.direction] = ev.index
            elif isinstance(ev, Break):
                decision.cancel_sides.add(-ev.direction)
                sig = self._setup(ev, time)
                if sig:
                    decision.signals.append(sig)
        return decision

    def _setup(self, ev: Break, time: pd.Timestamp) -> Signal | None:
        cfg, t, d, i = self.cfg, self.ltf, ev.direction, ev.index
        if cfg.use_htf_bias and self.bias != d:
            return None
        if not self._in_session(time):
            return None
        if cfg.require_sweep and i - self._last_sweep[d] > cfg.sweep_lookback:
            return None

        # Origin of the leg that broke structure: the extreme since the broken swing.
        start = ev.swing.index
        if d == 1:
            seg = t.l[start:i + 1]
            origin = start + seg.index(min(seg))
            leg_lo, leg_hi = t.l[origin], max(t.h[origin:i + 1])
        else:
            seg = t.h[start:i + 1]
            origin = start + seg.index(max(seg))
            leg_lo, leg_hi = min(t.l[origin:i + 1]), t.h[origin]

        # Displacement: a fair value gap (3-bar imbalance) inside the leg.
        ks = range(max(origin + 1, 2), i + 1)
        fvg = any(t.l[k] > t.h[k - 2] for k in ks) if d == 1 else any(t.h[k] < t.l[k - 2] for k in ks)
        if cfg.require_fvg and not fvg:
            return None

        # Order block: last opposite-coloured candle before the leg.
        ob = next((j for j in range(origin, max(origin - cfg.ob_lookback, -1), -1)
                   if (t.c[j] < t.o[j] if d == 1 else t.c[j] > t.o[j])), None)
        if ob is None:
            return None
        buf = cfg.sl_buffer_atr * self.atr
        if d == 1:
            top, bot = t.h[ob], min(t.l[ob:origin + 1])
            entry = top - cfg.entry_level * (top - bot)
            sl = bot - buf
        else:
            bot, top = t.l[ob], max(t.h[ob:origin + 1])
            entry = bot + cfg.entry_level * (top - bot)
            sl = top + buf

        risk = abs(entry - sl)
        mid = (leg_lo + leg_hi) / 2
        if (entry - mid) * d > 0:                 # longs in discount, shorts in premium
            return None
        if (t.c[i] - entry) * d <= 0:             # price already back inside the block
            return None
        if not cfg.min_stop_atr * self.atr <= risk <= cfg.max_stop_atr * self.atr:
            return None
        reason = f"{ev.kind} {'up' if d == 1 else 'down'}, OB@{ob}" + (", FVG" if fvg else "")
        return Signal(d, entry, sl, entry + d * cfg.rr * risk, time, cfg.expiry_bars, reason)
