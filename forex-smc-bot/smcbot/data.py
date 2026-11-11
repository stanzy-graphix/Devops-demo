"""Candle data loading."""
from __future__ import annotations

import numpy as np
import pandas as pd

COLS = ["open", "high", "low", "close"]


def load_csv(path: str) -> pd.DataFrame:
    """Load OHLC candles. Needs a time column (time/date/datetime/timestamp) plus
    open/high/low/close; column names are case-insensitive. Times are treated as UTC."""
    df = pd.read_csv(path)
    df.columns = [c.strip().lower() for c in df.columns]
    tcol = next((c for c in ("time", "datetime", "date", "timestamp") if c in df.columns), None)
    if tcol is None or not set(COLS) <= set(df.columns):
        raise ValueError(f"{path}: need a time column and {COLS}; found {list(df.columns)}")
    df.index = pd.DatetimeIndex(pd.to_datetime(df[tcol], utc=True), name="time")
    df = df[COLS].astype(float).sort_index()
    return df[~df.index.duplicated()]


def synthetic(bars: int = 20_000, freq: str = "15min", start_price: float = 1.10, seed: int = 0) -> pd.DataFrame:
    """Random-walk candles with volatility clustering. For exercising the code only -
    results on this data say nothing about real-market performance."""
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2022-01-03", periods=bars // 96 + 2, tz="UTC")
    times = (idx.repeat(96) + pd.to_timedelta(np.tile(np.arange(96), len(idx)) * 15, unit="min"))[:bars]
    if freq != "15min":
        times = pd.date_range("2022-01-03", periods=bars, freq=freq, tz="UTC")
    vol = np.empty(bars); v = 1.0
    for i in range(bars):
        v = 0.97 * v + 0.03 + 0.12 * rng.standard_normal() * v
        vol[i] = v = min(max(v, 0.3), 4.0)
    steps = rng.standard_normal((bars, 4)) * (vol[:, None] * 0.00035)
    close = start_price * np.exp(np.cumsum(steps.sum(axis=1) / start_price))
    open_ = np.concatenate([[start_price], close[:-1]])
    path = open_[:, None] * np.exp(np.cumsum(steps, axis=1) / start_price)
    hi = np.maximum(path.max(axis=1), np.maximum(open_, close))
    lo = np.minimum(path.min(axis=1), np.minimum(open_, close))
    return pd.DataFrame({"open": open_, "high": hi, "low": lo, "close": close}, index=pd.DatetimeIndex(times, name="time"))
