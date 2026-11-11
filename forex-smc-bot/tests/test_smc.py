import pandas as pd
import pytest

from smcbot.backtest import run_backtest
from smcbot.config import Config, StrategyConfig, load_config
from smcbot.data import synthetic
from smcbot.risk import position_size, quote_to_account
from smcbot.strategy import SMCStrategy
from smcbot.structure import Break, StructureTracker, Sweep

# Swing high, swing low, a sweep of that low (bar 10, bearish = order block), then a
# displacement leg with a fair value gap that closes above the bar-7 swing high (bar 13).
BARS = [
    (1.1000, 1.1010, 1.0990, 1.1005), (1.1005, 1.1020, 1.1000, 1.1015), (1.1015, 1.1040, 1.1010, 1.1035),
    (1.1035, 1.1036, 1.1015, 1.1020), (1.1020, 1.1025, 1.1000, 1.1005), (1.1005, 1.1010, 1.0980, 1.0990),
    (1.0990, 1.1010, 1.0988, 1.1005), (1.1005, 1.1030, 1.1000, 1.1025), (1.1025, 1.1026, 1.1005, 1.1010),
    (1.1010, 1.1012, 1.0990, 1.0995), (1.0995, 1.0998, 1.0970, 1.0985), (1.0985, 1.1005, 1.0984, 1.1003),
    (1.1003, 1.1030, 1.1002, 1.1028), (1.1028, 1.1050, 1.1020, 1.1045),
]
CFG = StrategyConfig(swing_len=2, use_htf_bias=False, sessions=[])


def frame(bars):
    idx = pd.date_range("2024-01-02 08:00", periods=len(bars), freq="15min", tz="UTC")
    return pd.DataFrame(bars, columns=["open", "high", "low", "close"], index=idx)


def feed(strategy, df):
    out = []
    for t, o, h, l, c in df.itertuples(name=None):
        out += strategy.on_bar(t, o, h, l, c).signals
    return out


def test_structure_events():
    t = StructureTracker(2)
    events = [(i, e) for i, b in enumerate(BARS) for e in t.update(*b)]
    sweeps = [(i, e) for i, e in events if isinstance(e, Sweep)]
    breaks = [(i, e) for i, e in events if isinstance(e, Break)]
    assert [(i, e.direction, e.swing.price) for i, e in sweeps] == [(10, 1, 1.0980)]
    assert [(i, e.direction, e.kind, e.swing.price) for i, e in breaks] == [(13, 1, "BOS", 1.1030)]
    assert t.trend == 1
    # A later close under the last swing low flips structure: change of character.
    more = [(1.1045, 1.1048, 1.1030, 1.1035), (1.1035, 1.1060, 1.1032, 1.1055), (1.1055, 1.1056, 1.1040, 1.1042),
            (1.1042, 1.1044, 1.1034, 1.1036), (1.1036, 1.1037, 1.0950, 1.0960)]
    kinds = [e.kind for b in more for e in t.update(*b) if isinstance(e, Break)]
    assert kinds == ["CHoCH"] and t.trend == -1


def test_long_setup_at_order_block():
    (sig,) = feed(SMCStrategy(CFG), frame(BARS))
    assert sig.side == 1
    assert sig.entry == pytest.approx(1.0998)          # top of the bearish candle at the leg origin
    assert 1.0960 < sig.sl < 1.0970                     # below the swept low, with a buffer
    assert sig.tp == pytest.approx(sig.entry + 2 * (sig.entry - sig.sl))


def test_filters_block_setup():
    assert not feed(SMCStrategy(StrategyConfig(swing_len=2, use_htf_bias=True, sessions=[])), frame(BARS))  # no HTF bias yet
    assert not feed(SMCStrategy(StrategyConfig(swing_len=2, use_htf_bias=False, sessions=[[20, 22]])), frame(BARS))
    no_sweep = BARS[:10] + [(1.0995, 1.0998, 1.0982, 1.0985)] + BARS[11:]
    assert not feed(SMCStrategy(CFG), frame(no_sweep))
    assert feed(SMCStrategy(StrategyConfig(swing_len=2, use_htf_bias=False, sessions=[], require_sweep=False)), frame(no_sweep))


def backtest(extra):
    cfg = Config(strategy=CFG)
    cfg.backtest.spread_pips = 0.0
    return run_backtest(frame(BARS + extra), cfg)


def test_backtest_win_and_sizing():
    res = backtest([(1.1045, 1.1046, 1.0995, 1.1010), (1.1010, 1.1100, 1.1005, 1.1090)])
    (tr,) = res.trades
    assert tr.outcome == "tp" and tr.r == pytest.approx(2.0)
    assert tr.pnl == pytest.approx(200, rel=0.01)       # 1% risk x 2R on 10k
    assert res.stats["final_equity"] == pytest.approx(10_000 + tr.pnl)


def test_backtest_is_pessimistic_within_a_bar():
    # The fill bar also reaches the target: no credit. Next bar spans stop and target: stop wins.
    res = backtest([(1.1045, 1.1100, 1.0995, 1.1010), (1.1010, 1.1100, 1.0950, 1.1000)])
    (tr,) = res.trades
    assert tr.outcome == "sl" and tr.pnl == pytest.approx(-100, rel=0.01)


def test_unfilled_order_expires():
    flat = [(1.1045, 1.1050, 1.1040, 1.1045)] * 30 + [(1.1045, 1.1046, 1.0990, 1.1000)]
    assert backtest(flat).trades == []


def test_no_lookahead():
    df = synthetic(4000, seed=3)
    cfg = StrategyConfig(use_htf_bias=False, require_sweep=False)
    full, part = feed(SMCStrategy(cfg), df), feed(SMCStrategy(cfg), df.iloc[:2500])
    assert part and full[:len(part)] == part


def test_risk_helpers():
    assert position_size(10_000, 0.01, 1.5, 1.0, 1.0) == 200
    assert position_size(10_000, 0.01, 1.1000, 1.0999, 1.0, max_leverage=30) == int(300_000 / 1.1)
    assert quote_to_account("USD_JPY", "USD", 150.0) == pytest.approx(1 / 150)
    with pytest.raises(ValueError):
        quote_to_account("EUR_GBP", "USD", 0.85)


def test_example_config_loads():
    cfg = load_config("config.example.toml")
    assert cfg.instrument == "EUR_USD" and cfg.strategy.rr == 2.0
