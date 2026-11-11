"""Position sizing and currency helpers."""
from __future__ import annotations


def pip_size(instrument: str) -> float:
    return 0.01 if instrument.upper().endswith("JPY") else 0.0001


def quote_to_account(instrument: str, account_ccy: str, price: float, override: float = 0.0) -> float:
    """Value of one unit of the quote currency in the account currency."""
    base, quote = instrument.upper().split("_")
    if override:
        return override
    if quote == account_ccy:
        return 1.0
    if base == account_ccy:
        return 1.0 / price
    raise ValueError(
        f"Cannot convert {quote} to {account_ccy} from {instrument} alone; set quote_to_account_rate in the config."
    )


def position_size(equity: float, risk_fraction: float, entry: float, sl: float, q2a: float,
                  max_leverage: float = 0.0) -> int:
    """Units such that a stop-out loses ``risk_fraction`` of equity, capped by leverage."""
    stop = abs(entry - sl)
    if stop <= 0 or equity <= 0:
        return 0
    units = equity * risk_fraction / (stop * q2a)
    if max_leverage > 0:
        units = min(units, equity * max_leverage / (entry * q2a))
    return int(units)
