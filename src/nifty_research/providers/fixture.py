"""Offline fixture provider — the test and replay path.

Two fixture shapes are supported, and both are date-relative so a fixture never rots:

* **generator**: parameters for the deterministic synthetic market (default fixture).
* **captured**: a real chain captured earlier, stored with *days-to-expiry* per quote
  rather than absolute dates (``scripts/capture_chain.py`` writes this shape), so it can
  be replayed any day and still be internally consistent.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from ..config import RunConfig
from ..schemas import Bar, ChainSnapshot, MarketState, OptionQuote
from .base import MarketDataProvider, ProviderUnavailable
from .synthetic import build_history, build_snapshot

DEFAULT_FIXTURE = Path(__file__).resolve().parents[3] / "data" / "fixtures" / "nifty_chain_sample.json"


class FixtureProvider(MarketDataProvider):
    name = "fixture"

    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path else DEFAULT_FIXTURE

    def fetch(self, config: RunConfig) -> MarketState:
        if not self.path.exists():
            raise ProviderUnavailable(f"fixture not found: {self.path}")
        try:
            payload = json.loads(self.path.read_text())
        except json.JSONDecodeError as exc:
            raise ProviderUnavailable(f"fixture is not valid JSON: {exc}") from exc

        as_of = datetime.now().replace(microsecond=0)
        if "captured" in payload:
            return self._from_captured(payload, as_of)
        return self._from_generator(payload, as_of, config)

    # ------------------------------------------------------------------ shapes
    def _from_generator(self, payload: dict, as_of: datetime, config: RunConfig) -> MarketState:
        gen = payload.get("generator") or {}
        spot = float(gen.get("spot", payload.get("spot", 25_150.0)))
        atm_iv = float(gen.get("atm_iv", 0.13))
        offsets = tuple(gen.get("expiry_offsets", [7, 14, 30]))
        snapshot = build_snapshot(
            spot=spot,
            atm_iv=atm_iv,
            as_of=as_of,
            strike_step=int(gen.get("strike_step", config.strike_step)),
            rate=config.risk_free_rate,
            expiry_offsets=offsets,
            n_strikes_each_side=int(gen.get("strikes_each_side", 20)),
        )
        rv = float(gen.get("realized_vol", max(atm_iv * 0.85, 0.05)))
        history = build_history(spot, as_of, annual_vol=rv, seed=int(gen.get("seed", 7)))
        warnings = [f"fixture({self.path.name}): " + w for w in snapshot.warnings]
        snapshot.warnings = warnings
        return MarketState(
            snapshot=snapshot,
            history=history,
            vix=float(payload.get("vix", atm_iv * 100)),
            provider=f"fixture({self.path.name})",
            warnings=warnings,
        )

    def _from_captured(self, payload: dict, as_of: datetime) -> MarketState:
        cap = payload["captured"]
        spot = float(cap["spot"])
        quotes: list[OptionQuote] = []
        expiry_dates: set = set()
        for row in cap["quotes"]:
            expiry = as_of.date() + timedelta(days=int(row["dte"]))
            expiry_dates.add(expiry)
            quotes.append(
                OptionQuote(
                    strike=float(row["strike"]),
                    option_type=str(row["option_type"]).upper(),  # type: ignore[arg-type]
                    expiry=expiry,
                    ltp=float(row["ltp"]),
                    iv=float(row["iv"]) if row.get("iv") else None,
                    oi=int(row.get("oi", 0)),
                    volume=int(row.get("volume", 0)),
                )
            )
        expiries = sorted(expiry_dates)
        if not expiries or not quotes:
            raise ProviderUnavailable("captured fixture has no usable quotes")
        snapshot = ChainSnapshot(
            symbol=cap.get("symbol", "NIFTY"),
            spot=spot,
            as_of=as_of,
            expiry=expiries[0],
            expiries=expiries,
            quotes=quotes,
            source=f"captured({self.path.name})",
            warnings=["replayed capture: expiries are relative to today, IVs are as captured"],
        )
        history_payload = cap.get("history") or []
        if history_payload:
            history = [
                Bar(
                    day=as_of.date() - timedelta(days=len(history_payload) - i),
                    close=float(c),
                )
                for i, c in enumerate(history_payload)
            ]
        else:
            history = build_history(spot, as_of, annual_vol=float(cap.get("realized_vol", 0.11)))
        return MarketState(
            snapshot=snapshot,
            history=history,
            vix=cap.get("vix"),
            provider=f"fixture-capture({self.path.name})",
            warnings=list(snapshot.warnings),
        )
