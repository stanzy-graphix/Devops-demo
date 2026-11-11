"""Configuration dataclasses, loadable from a TOML file."""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field, fields


@dataclass
class StrategyConfig:
    swing_len: int = 3            # bars each side that define a swing point (entry timeframe)
    htf: str = "4h"               # higher timeframe used for directional bias (pandas offset)
    htf_swing_len: int = 3
    use_htf_bias: bool = True     # only trade in the direction of HTF structure
    require_sweep: bool = True    # require a liquidity sweep before the structure break
    sweep_lookback: int = 30      # bars the sweep may precede the break by
    sweep_reclaim_bars: int = 3   # bars price has to close back beyond a pierced level
    require_fvg: bool = True      # require a fair value gap inside the breaking leg
    ob_lookback: int = 5          # bars to search back from the leg origin for the order block
    entry_level: float = 0.0      # 0 = proximal edge of the order block, 0.5 = its midpoint
    rr: float = 2.0               # take profit as a multiple of risk
    sl_buffer_atr: float = 0.1    # stop buffer beyond the order block, in ATRs
    min_stop_atr: float = 0.5     # skip setups whose stop is tighter than this
    max_stop_atr: float = 3.0     # ...or wider than this
    atr_len: int = 14
    expiry_bars: int = 24         # cancel an unfilled limit order after this many bars
    sessions: list = field(default_factory=lambda: [[7, 17]])  # UTC hour ranges; [] = always


@dataclass
class RiskConfig:
    risk_per_trade: float = 0.01  # fraction of equity risked per trade
    max_leverage: float = 30.0    # hard cap on position notional / equity
    max_open: int = 1             # open positions + pending orders allowed at once
    max_daily_loss: float = 0.03  # stop opening trades after losing this fraction in a UTC day
    breakeven_at_r: float = 0.0   # move stop to entry once price has moved this many R (0 = off)


@dataclass
class BacktestConfig:
    initial_balance: float = 10_000.0
    spread_pips: float = 1.0
    commission_per_lot: float = 0.0  # account currency, per 100k units, per side


@dataclass
class LiveConfig:
    environment: str = "practice"  # "practice" or "live"
    granularity: str = "M15"       # OANDA candle granularity for the entry timeframe
    warmup_bars: int = 1500


@dataclass
class Config:
    instrument: str = "EUR_USD"
    account_currency: str = "USD"
    quote_to_account_rate: float = 0.0  # needed only when quote ccy and base ccy both differ from account ccy
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    backtest: BacktestConfig = field(default_factory=BacktestConfig)
    live: LiveConfig = field(default_factory=LiveConfig)


def _build(cls, data: dict):
    known = {f.name for f in fields(cls)}
    unknown = set(data) - known
    if unknown:
        raise ValueError(f"Unknown {cls.__name__} option(s): {sorted(unknown)}")
    return cls(**data)


def load_config(path: str | None) -> Config:
    if not path:
        return Config()
    with open(path, "rb") as fh:
        raw = tomllib.load(fh)
    sections = {"strategy": StrategyConfig, "risk": RiskConfig, "backtest": BacktestConfig, "live": LiveConfig}
    kwargs = {name: _build(cls, raw.pop(name, {})) for name, cls in sections.items()}
    return _build(Config, {**raw, **kwargs})
