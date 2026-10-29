#!/usr/bin/env python
"""Write (or refresh) the offline fixture from the deterministic generator.

Usage:  python scripts/make_fixture.py [--spot 25150] [--atm-iv 0.132]
Prints the JSON it writes, so the fixture can be reviewed before committing.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

TARGET = ROOT / "data" / "fixtures" / "nifty_chain_sample.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spot", type=float, default=25150.0)
    parser.add_argument("--atm-iv", type=float, default=0.132)
    parser.add_argument("--realized-vol", type=float, default=0.104)
    parser.add_argument("--seed", type=int, default=11)
    parser.add_argument("--out", type=Path, default=TARGET)
    args = parser.parse_args()

    payload = {
        "name": "nifty_chain_sample",
        "description": (
            "Deterministic offline market: params generate a Black-Scholes-consistent NIFTY chain "
            "with a mild skew, plus a seeded 120-day close series. Expiries are stored as day "
            "offsets so the fixture never goes stale."
        ),
        "vix": round(args.atm_iv * 101.5, 2),
        "generator": {
            "spot": args.spot,
            "atm_iv": args.atm_iv,
            "realized_vol": args.realized_vol,
            "strike_step": 50,
            "strikes_each_side": 20,
            "expiry_offsets": [7, 14, 30],
            "seed": args.seed,
        },
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2) + "\n")
    print(json.dumps(payload, indent=2))
    print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
