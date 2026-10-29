"""Deterministic synthetic market generator.

This exists for three reasons, all of them honest ones:

1. The test suite needs a chain that behaves like a chain but never calls a network.
2. NSE's option-chain endpoint is IP-restricted, so a machine outside India (or a
   sandboxed CI runner) cannot fetch real quotes — without this, the agent simply
   would not run.
3. Parameter sweeps want a *fixed* market so that differences in score come from the
   strategy and not from the tape moving underneath the run.

Anything produced here is labelled ``synthetic`` and reported as such. Real
decisions must come from a real chain.
"""

from __future__ import annotations

import math
import random
from datetime import date, datetime, timedelta

from ..config import RunConfig
from ..schemas import Bar, ChainSnapshot, MarketState, OptionQuote
from .base import MarketDataProvider
from ..analytics.greeks import bs_price

DEFAULT_EXPIRY_OFFSETS = (7, 14, 30)


def smile_iv(atm_iv: float, spot: float, strike: float) -> float:
    """A crude but stable skew/smile: puts bid, wings bid, ATM cheapest."""
    m = (strike - spot) / spot
    iv = atm_iv * (1.0 + 1.0 * (-m) + 6.0 * m * m)
    return max(iv, 0.01)


def open_interest_profile(spot: float, strike: float, step: int, base_oi: int = 220_000) -> int:
    """OI decays with distance from spot, but deep wings stay thin — as in a real chain."""
    dist = abs(strike - spot) / max(step, 1)
    return int(base_oi * math.exp(-((dist / 4.5) ** 1.5)))


def build_snapshot(
    spot: float,
    atm_iv: float,
    as_of: datetime,
    strike_step: int,
    rate: float,
    expiry_offsets: tuple[int, ...] = DEFAULT_EXPIRY_OFFSETS,
    n_strikes_each_side: int = 20,
) -> ChainSnapshot:
    """Build a full, internally consistent chain (quotes priced off Black-Scholes)."""
    expiries = [as_of.date() + timedelta(days=off) for off in expiry_offsets]
    atm = round(spot / strike_step) * strike_step
    strikes = [atm + i * strike_step for i in range(-n_strikes_each_side, n_strikes_each_side + 1)]

    quotes: list[OptionQuote] = []
    for expiry in expiries:
        years = max((expiry - as_of.date()).days, 1) / 365.0
        for strike in strikes:
            iv = smile_iv(atm_iv, spot, strike)
            oi = open_interest_profile(spot, strike, strike_step)
            for option_type in ("CE", "PE"):
                price = bs_price(spot, strike, years, iv, rate, option_type)
                quotes.append(
                    OptionQuote(
                        strike=float(strike),
                        option_type=option_type.upper(),  # type: ignore[arg-type]
                        expiry=expiry,
                        ltp=round(max(price, 0.05), 2),
                        iv=round(iv, 4),
                        oi=oi,
                        volume=int(oi / 4),
                    )
                )
    return ChainSnapshot(
        symbol="NIFTY",
        spot=round(spot, 2),
        as_of=as_of,
        expiry=expiries[0],
        expiries=expiries,
        quotes=quotes,
        source="synthetic",
        warnings=["option quotes are modelled (Black-Scholes + smile), not real market data"],
    )


def build_history(
    spot: float, as_of: datetime, days: int = 120, annual_vol: float = 0.11, seed: int = 7
) -> list[Bar]:
    """A seeded daily close series whose realised vol matches ``annual_vol``."""
    rng = random.Random(seed)
    daily_sigma = annual_vol / math.sqrt(252.0)
    closes: list[float] = []
    value = spot / math.exp(0.0002 * days)
    for _ in range(days):
        value *= math.exp(rng.gauss(0.0002, daily_sigma))
        closes.append(value)
    scale = spot / closes[-1]
    closes = [c * scale for c in closes]
    out: list[Bar] = []
    for i, close in enumerate(closes):
        day = as_of.date() - timedelta(days=days - i)
        out.append(Bar(day=day, close=round(close, 2), high=round(close * 1.004, 2), low=round(close * 0.996, 2)))
    return out


class SyntheticProvider(MarketDataProvider):
    name = "synthetic"

    def __init__(self, spot: float = 25_150.0, atm_iv: float = 0.13, vix: float | None = 13.5):
        self.spot = spot
        self.atm_iv = atm_iv
        self.vix = vix

    def fetch(self, config: RunConfig) -> MarketState:
        as_of = datetime.now().replace(microsecond=0)
        snapshot = build_snapshot(
            spot=self.spot,
            atm_iv=self.atm_iv,
            as_of=as_of,
            strike_step=config.strike_step,
            rate=config.risk_free_rate,
        )
        history = build_history(self.spot, as_of, annual_vol=max(self.atm_iv * 0.85, 0.05))
        return MarketState(
            snapshot=snapshot,
            history=history,
            vix=self.vix,
            provider="synthetic",
            warnings=list(snapshot.warnings),
        )
