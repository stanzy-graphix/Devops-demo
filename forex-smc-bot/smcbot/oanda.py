"""Minimal OANDA v20 REST client (only what the bot needs)."""
from __future__ import annotations

import pandas as pd
import requests

HOSTS = {"practice": "https://api-fxpractice.oanda.com", "live": "https://api-fxtrade.oanda.com"}
GRANULARITY_SECONDS = {"M1": 60, "M5": 300, "M15": 900, "M30": 1800, "H1": 3600, "H4": 14400, "D": 86400}


class OandaClient:
    def __init__(self, token: str, account_id: str, environment: str = "practice", timeout: float = 20):
        self.base = HOSTS[environment]
        self.account_id = account_id
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"Authorization": f"Bearer {token}", "Content-Type": "application/json"})

    def _request(self, method: str, path: str, **kw) -> dict:
        r = self.session.request(method, self.base + path, timeout=self.timeout, **kw)
        if not r.ok:
            raise RuntimeError(f"OANDA {method} {path} -> {r.status_code}: {r.text[:300]}")
        return r.json()

    def candles(self, instrument: str, granularity: str, count: int = 500, price: str = "B") -> pd.DataFrame:
        """Completed candles (bid by default), oldest first, indexed by UTC open time."""
        key = {"B": "bid", "M": "mid", "A": "ask"}[price]
        data = self._request("GET", f"/v3/instruments/{instrument}/candles",
                             params={"granularity": granularity, "count": min(count, 5000), "price": price})
        rows = [(c["time"], *(float(c[key][k]) for k in "ohlc")) for c in data["candles"] if c["complete"]]
        df = pd.DataFrame(rows, columns=["time", "open", "high", "low", "close"])
        df.index = pd.DatetimeIndex(pd.to_datetime(df.pop("time"), utc=True), name="time")
        return df

    def nav(self) -> float:
        return float(self._request("GET", f"/v3/accounts/{self.account_id}/summary")["account"]["NAV"])

    def spread(self, instrument: str) -> float:
        p = self._request("GET", f"/v3/accounts/{self.account_id}/pricing", params={"instruments": instrument})["prices"][0]
        return float(p["asks"][0]["price"]) - float(p["bids"][0]["price"])

    def open_trades(self, instrument: str) -> list:
        trades = self._request("GET", f"/v3/accounts/{self.account_id}/openTrades")["trades"]
        return [t for t in trades if t["instrument"] == instrument]

    def pending_limit_orders(self, instrument: str) -> list:
        orders = self._request("GET", f"/v3/accounts/{self.account_id}/pendingOrders")["orders"]
        return [o for o in orders if o.get("instrument") == instrument and o["type"] == "LIMIT"]

    def cancel_order(self, order_id: str) -> None:
        self._request("PUT", f"/v3/accounts/{self.account_id}/orders/{order_id}/cancel")

    def limit_order(self, instrument: str, units: int, price: float, sl: float, tp: float,
                    expires: pd.Timestamp, tag: str = "smcbot") -> dict:
        """Limit order with attached stop loss and take profit. ``units`` < 0 sells."""
        digits = 3 if instrument.endswith("JPY") else 5
        fmt = lambda x: f"{x:.{digits}f}"
        order = {
            "type": "LIMIT", "instrument": instrument, "units": str(units), "price": fmt(price),
            "timeInForce": "GTD", "gtdTime": expires.strftime("%Y-%m-%dT%H:%M:%S.000000000Z"),
            "positionFill": "DEFAULT",
            "stopLossOnFill": {"price": fmt(sl)}, "takeProfitOnFill": {"price": fmt(tp)},
            "clientExtensions": {"tag": tag},
        }
        return self._request("POST", f"/v3/accounts/{self.account_id}/orders", json={"order": order})
