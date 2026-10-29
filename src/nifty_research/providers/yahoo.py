"""Yahoo Finance: index level, history and India VIX.

Yahoo does not publish an option chain for NIFTY, so in this mode the *spot, trend and
vol level* are real and the chain is modelled from them. That distinction is written
into ``snapshot.source`` and into the run warnings — a modelled chain must never be
mistaken for a tradable one.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx

from ..config import RunConfig
from ..schemas import Bar, MarketState
from .base import MarketDataProvider, ProviderUnavailable
from .synthetic import build_history, build_snapshot

HOSTS = ("query1.finance.yahoo.com", "query2.finance.yahoo.com")
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
    ),
    "Accept": "application/json",
}


def _chart(symbol: str, range_: str = "1y", interval: str = "1d", timeout: float = 15.0) -> dict:
    last_error: Exception | None = None
    for host in HOSTS:
        url = f"https://{host}/v8/finance/chart/{symbol}"
        try:
            resp = httpx.get(
                url, params={"range": range_, "interval": interval}, headers=HEADERS, timeout=timeout
            )
            if resp.status_code != 200:
                raise ProviderUnavailable(f"HTTP {resp.status_code} from {host}")
            payload = resp.json()
            result = (payload.get("chart") or {}).get("result")
            if not result:
                raise ProviderUnavailable(f"empty chart payload from {host}")
            return result[0]
        except (httpx.HTTPError, ValueError, ProviderUnavailable) as exc:
            last_error = exc
    raise ProviderUnavailable(f"yahoo chart failed for {symbol}: {last_error}")


def bars_from_chart(result: dict) -> list[Bar]:
    stamps = result.get("timestamp") or []
    quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
    closes = quote.get("close") or []
    highs = quote.get("high") or []
    lows = quote.get("low") or []
    out: list[Bar] = []
    for i, ts in enumerate(stamps):
        close = closes[i] if i < len(closes) else None
        if close is None:
            continue
        day = datetime.fromtimestamp(ts, tz=timezone.utc).date()
        out.append(
            Bar(
                day=day,
                close=round(float(close), 2),
                high=round(float(highs[i]), 2) if i < len(highs) and highs[i] else None,
                low=round(float(lows[i]), 2) if i < len(lows) and lows[i] else None,
            )
        )
    return out


class YahooProvider(MarketDataProvider):
    name = "yahoo"

    def __init__(self, index_symbol: str = "^NSEI", vix_symbol: str = "^INDIAVIX"):
        self.index_symbol = index_symbol
        self.vix_symbol = vix_symbol

    def fetch_history_base(self) -> list[Bar]:
        result = _chart(self.index_symbol, range_="1y", interval="1d")
        bars = bars_from_chart(result)
        if len(bars) < 30:
            raise ProviderUnavailable(f"only {len(bars)} index bars returned")
        return bars

    def fetch_vix(self) -> float | None:
        try:
            result = _chart(self.vix_symbol, range_="5d", interval="1d")
        except ProviderUnavailable:
            return None
        bars = bars_from_chart(result)
        return bars[-1].close if bars else None

    def fetch(self, config: RunConfig) -> MarketState:
        history = self.fetch_history_base()
        spot = history[-1].close
        vix = self.fetch_vix()
        atm_iv = (vix / 100.0) if vix else 0.13

        as_of = datetime.now().replace(microsecond=0)
        snapshot = build_snapshot(
            spot=spot,
            atm_iv=atm_iv,
            as_of=as_of,
            strike_step=config.strike_step,
            rate=config.risk_free_rate,
            expiry_offsets=(7, 14, 30),
        )
        snapshot.source = f"synthetic(yahoo spot {spot:,.0f})"
        warnings = [
            "spot/trend/VIX are live from Yahoo; option quotes are modelled — not tradable prices"
        ] + list(snapshot.warnings)
        snapshot.warnings = warnings
        return MarketState(
            snapshot=snapshot,
            history=history,
            vix=vix,
            provider="yahoo+modelled-chain",
            warnings=warnings,
        )
