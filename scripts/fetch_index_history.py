#!/usr/bin/env python
"""Fetch real NIFTY index history into a CSV the backtest can use.

Run this where the data is reachable (from India, or any network that is not IP-blocked
by the vendor) and commit the file — then `nifty-research backtest --history csv:...`
produces real evidence instead of a synthetic mechanical check.

    python scripts/fetch_index_history.py --out data/history/nifty_daily.csv --range 10y

Yahoo rate-limits aggressively from some networks; if you hit HTTP 429, wait and retry,
or export the same columns (date,close,vix) from any vendor.
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from nifty_research.backtest.history import from_yahoo  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / "data" / "history" / "nifty_daily.csv")
    parser.add_argument("--range", default="10y", help="yahoo range: 1y, 5y, 10y, max")
    parser.add_argument("--symbol", default="^NSEI")
    args = parser.parse_args()

    try:
        history = from_yahoo(index_symbol=args.symbol, range_=args.range)
    except Exception as exc:  # noqa: BLE001 - user-facing CLI
        print(f"fetch failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        print(
            "Yahoo blocks/rate-limits some networks. Retry later, or export date,close,vix from a "
            "vendor and put it at the --out path.",
            file=sys.stderr,
        )
        return 2

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["date", "close", "vix"])
        for day, close in zip(history.days, history.closes):
            writer.writerow([day.isoformat(), close, history.vix.get(day, "")])

    print(f"wrote {len(history)} bars ({history.days[0]} -> {history.days[-1]}) to {args.out}")
    print(f"vix bars: {len(history.vix)}")
    print(f"\nnow run:\n  nifty-research backtest --history csv:{args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
