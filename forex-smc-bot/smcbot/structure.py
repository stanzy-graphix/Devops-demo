"""Market structure: swing points, break of structure (BOS), change of character (CHoCH)
and liquidity sweeps.

The tracker is fed one *closed* bar at a time and never looks ahead: a swing point at bar
``c`` is only known once ``swing_len`` further bars have closed.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Swing:
    index: int
    price: float
    kind: str                 # "high" or "low"
    broken: bool = False      # a bar has closed beyond it
    pierced_at: int | None = None
    liquidity_done: bool = False


@dataclass
class Break:
    direction: int            # +1 bullish, -1 bearish
    kind: str                 # "BOS" (continuation) or "CHoCH" (reversal)
    swing: Swing
    index: int


@dataclass
class Sweep:
    direction: int            # +1 = sell-side liquidity taken (bullish), -1 = buy-side taken
    swing: Swing
    index: int


class StructureTracker:
    def __init__(self, swing_len: int = 3, reclaim_bars: int = 3, max_liquidity: int = 10):
        self.n = swing_len
        self.reclaim_bars = reclaim_bars
        self.max_liquidity = max_liquidity
        self.o: list[float] = []
        self.h: list[float] = []
        self.l: list[float] = []
        self.c: list[float] = []
        self.trend = 0                      # +1 bullish, -1 bearish, 0 undefined
        self.last_high: Swing | None = None
        self.last_low: Swing | None = None
        self.liquidity: list[Swing] = []    # recent swings whose liquidity is still resting

    def update(self, o: float, h: float, l: float, c: float) -> list:
        i = len(self.h)
        self.o.append(o); self.h.append(h); self.l.append(l); self.c.append(c)
        events: list = []

        # Structure breaks: a close beyond the most recent confirmed swing.
        sh, sl = self.last_high, self.last_low
        if sh and not sh.broken and c > sh.price:
            sh.broken = True
            events.append(Break(1, "CHoCH" if self.trend == -1 else "BOS", sh, i))
            self.trend = 1
        elif sl and not sl.broken and c < sl.price:
            sl.broken = True
            events.append(Break(-1, "CHoCH" if self.trend == 1 else "BOS", sl, i))
            self.trend = -1

        # Liquidity sweeps: price trades through a swing, then closes back inside.
        for s in self.liquidity:
            if s.liquidity_done:
                continue
            pierced = l < s.price if s.kind == "low" else h > s.price
            if s.pierced_at is None and pierced:
                s.pierced_at = i
            if s.pierced_at is not None:
                reclaimed = c > s.price if s.kind == "low" else c < s.price
                if reclaimed:
                    s.liquidity_done = True
                    events.append(Sweep(1 if s.kind == "low" else -1, s, i))
                elif i - s.pierced_at + 1 >= self.reclaim_bars:
                    s.liquidity_done = True
        self.liquidity = [s for s in self.liquidity if not s.liquidity_done][-self.max_liquidity:]

        # Confirm the swing candidate that now has swing_len bars on its right.
        n, cand = self.n, i - self.n
        if cand >= n:
            if self.h[cand] == max(self.h[cand - n:i + 1]) and self.h[cand] > max(self.h[cand - n:cand]):
                self.last_high = Swing(cand, self.h[cand], "high")
                self.liquidity.append(self.last_high)
            if self.l[cand] == min(self.l[cand - n:i + 1]) and self.l[cand] < min(self.l[cand - n:cand]):
                self.last_low = Swing(cand, self.l[cand], "low")
                self.liquidity.append(self.last_low)
        return events
