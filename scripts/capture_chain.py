#!/usr/bin/env python
"""Capture a real NSE option chain into a replayable fixture (run this from India).

NSE's option-chain endpoint is IP-restricted in practice, so measuring the *real*
market is a step you run where the market is reachable. The capture stores
days-to-expiry per quote rather than absolute dates, so a file captured today can be
replayed a month later and still be internally consistent.

Usage:
    python scripts/capture_chain.py --symbol NIFTY --out data/fixtures/nse_25100.json
Then:
    nifty-research run --provider fixture   # (point NR_FIXTURE at the file, or replace the default)
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from nifty_research.config import RunConfig  # noqa: E402
from nifty_research.providers.nse import NseProvider  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default="NIFTY")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--max-expiries", type=int, default=2, help="keep only the nearest N expiries")
    args = parser.parse_args()

    config = RunConfig(symbol=args.symbol)
    provider = NseProvider()
    try:
        state = provider.fetch(config)
    except Exception as exc:  # noqa: BLE001 - user-facing CLI
        print(f"capture failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        print("NSE blocks non-India IPs; run this from an Indian network or a proxy.", file=sys.stderr)
        return 2

    snapshot = state.snapshot
    as_of = snapshot.as_of.date()
    expiries = sorted(snapshot.expiries)[: args.max_expiries]
    quotes = [
        {
            "strike": q.strike,
            "option_type": q.option_type,
            "dte": (q.expiry - as_of).days,
            "ltp": q.ltp,
            "iv": q.iv,
            "oi": q.oi,
            "volume": q.volume,
        }
        for q in snapshot.quotes
        if q.expiry in expiries
    ]
    payload = {
        "name": f"nse_capture_{as_of.isoformat()}",
        "captured_at": datetime.now().replace(microsecond=0).isoformat(),
        "captured": {
            "symbol": snapshot.symbol,
            "spot": snapshot.spot,
            "vix": state.vix,
            "realized_vol": None,
            "history": [b.close for b in state.history][-120:],
            "quotes": quotes,
            "expiry_offsets": [int((e - as_of).days) for e in expiries],
        },
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2) + "\n")
    print(f"captured {len(quotes)} quotes across {len(expiries)} expiries -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
