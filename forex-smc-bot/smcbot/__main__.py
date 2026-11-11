"""Command line: python -m smcbot {backtest,fetch,live}"""
from __future__ import annotations

import argparse
import logging

from .config import load_config


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="smcbot", description="Forex Smart Money Concepts trading bot")
    sub = p.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("backtest", help="backtest on historical candles")
    b.add_argument("--config")
    src = b.add_mutually_exclusive_group(required=True)
    src.add_argument("--data", help="CSV with time,open,high,low,close (UTC)")
    src.add_argument("--synthetic", type=int, metavar="BARS", help="random-walk data, for smoke tests only")
    b.add_argument("--trades-out", help="write the trade list to this CSV")
    b.add_argument("--equity-out", help="write the equity curve to this CSV")

    f = sub.add_parser("fetch", help="download candles from OANDA to CSV")
    f.add_argument("--config")
    f.add_argument("--count", type=int, default=5000)
    f.add_argument("--out", required=True)

    l = sub.add_parser("live", help="run against OANDA (signals only unless --execute)")
    l.add_argument("--config")
    l.add_argument("--execute", action="store_true", help="actually place orders")
    l.add_argument("--confirm-live", action="store_true", help="required to trade a real-money account")

    args = p.parse_args(argv)
    cfg = load_config(args.config)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if args.cmd == "backtest":
        from .backtest import run_backtest, trades_frame
        from .data import load_csv, synthetic
        df = load_csv(args.data) if args.data else synthetic(args.synthetic)
        res = run_backtest(df, cfg)
        print(f"{cfg.instrument}  {df.index[0]} -> {df.index[-1]}  ({len(df)} bars)")
        for k, v in res.stats.items():
            print(f"  {k:<18}{v:>12.2f}" if isinstance(v, float) else f"  {k:<18}{v:>12}")
        if args.trades_out:
            trades_frame(res.trades).to_csv(args.trades_out, index=False)
        if args.equity_out:
            res.equity.to_csv(args.equity_out)
    elif args.cmd == "fetch":
        from .live import client_from_env
        df = client_from_env(cfg.live.environment).candles(cfg.instrument, cfg.live.granularity, args.count)
        df.to_csv(args.out)
        print(f"Wrote {len(df)} {cfg.live.granularity} candles to {args.out}")
    else:
        from .live import LiveRunner, client_from_env
        if cfg.live.environment == "live" and args.execute and not args.confirm_live:
            raise SystemExit("Refusing to trade a real-money account without --confirm-live.")
        mode = "EXECUTING on " + cfg.live.environment if args.execute else "signals only (dry run)"
        logging.getLogger("smcbot").info("Starting %s %s, %s", cfg.instrument, cfg.live.granularity, mode)
        LiveRunner(cfg, client_from_env(cfg.live.environment), args.execute).run_forever()


if __name__ == "__main__":
    main()
