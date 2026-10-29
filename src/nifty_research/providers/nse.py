"""NSE live option chain (authoritative source when the network allows it).

NSE's public JSON endpoints require a browser-like session: the site sets cookies on
the landing page and the API rejects requests that arrive without them, and the
endpoints are IP-restricted to India in practice. Both failure modes surface as
``ProviderUnavailable`` so the resolver can fall back cleanly.
"""

from __future__ import annotations

import json
import time
from datetime import date, datetime

import httpx

from ..config import RunConfig
from ..schemas import Bar, ChainSnapshot, MarketState, OptionQuote
from .base import MarketDataProvider, ProviderUnavailable

BASE = "https://www.nseindia.com"
CHAIN_URL = BASE + "/api/option-chain-indices"
ALL_INDICES_URL = BASE + "/api/allIndices"

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": BASE + "/option-chain",
    "Connection": "keep-alive",
}


def _parse_expiry(value: str) -> date:
    for fmt in ("%d-%b-%Y", "%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    raise ProviderUnavailable(f"unrecognised expiry format: {value!r}")


class NseProvider(MarketDataProvider):
    name = "nse"

    def __init__(self, timeout: float = 20.0, retries: int = 2):
        self.timeout = timeout
        self.retries = retries

    def _session(self) -> httpx.Client:
        client = httpx.Client(headers=BROWSER_HEADERS, timeout=self.timeout, follow_redirects=True)
        try:
            client.get(BASE, headers={"Accept": "text/html"})
            time.sleep(0.8)  # NSE throttles the second request if it arrives too fast
        except httpx.HTTPError as exc:
            client.close()
            raise ProviderUnavailable(f"could not prime NSE session: {exc}") from exc
        return client

    def _get_json(self, client: httpx.Client, url: str, params: dict | None = None) -> dict:
        last: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                resp = client.get(url, params=params)
                if resp.status_code != 200:
                    raise ProviderUnavailable(f"HTTP {resp.status_code} from {url}")
                text = resp.text.strip()
                if not text.startswith("{") and not text.startswith("["):
                    raise ProviderUnavailable("non-JSON response (bot wall?)")
                return json.loads(text)
            except (httpx.HTTPError, json.JSONDecodeError, ProviderUnavailable) as exc:
                last = exc
                time.sleep(0.6 * (attempt + 1))
        raise ProviderUnavailable(str(last))

    def fetch(self, config: RunConfig) -> MarketState:
        symbol = config.symbol.upper()
        warnings: list[str] = []
        client = self._session()
        try:
            spot, quotes, expiries = self._chain(client, symbol)
            vix = self._vix(client, warnings)
        finally:
            client.close()

        if not quotes:
            raise ProviderUnavailable("NSE returned an empty option chain")

        as_of = datetime.now().replace(microsecond=0)
        snapshot = ChainSnapshot(
            symbol=symbol,
            spot=spot,
            as_of=as_of,
            expiry=expiries[0],
            expiries=expiries,
            quotes=quotes,
            source="nse",
            warnings=warnings,
        )
        history = self._history(spot, as_of, warnings)
        return MarketState(
            snapshot=snapshot, history=history, vix=vix, provider="nse", warnings=warnings
        )

    # ------------------------------------------------------------------ internals
    def _chain(self, client: httpx.Client, symbol: str) -> tuple[float, list[OptionQuote], list[date]]:
        payload = self._get_json(client, CHAIN_URL, {"symbol": symbol})
        records = payload.get("records") or {}
        underlying = float((records.get("underlyingValue") or 0) or 0)
        expiry_strings = records.get("expiryDates") or []
        expiries = sorted({_parse_expiry(e) for e in expiry_strings})

        quotes: list[OptionQuote] = []
        for row in records.get("data") or []:
            expiry_raw = row.get("expiryDate")
            if not expiry_raw:
                continue
            expiry = _parse_expiry(expiry_raw)
            strike = row.get("strikePrice")
            if strike is None:
                continue
            for option_type in ("CE", "PE"):
                leg = row.get(option_type)
                if not leg:
                    continue
                quotes.append(
                    OptionQuote(
                        strike=float(strike),
                        option_type=option_type,  # type: ignore[arg-type]
                        expiry=expiry,
                        ltp=float(leg.get("lastPrice") or 0.0),
                        iv=(float(leg["impliedVolatility"]) / 100.0) if leg.get("impliedVolatility") else None,
                        oi=int(leg.get("openInterest") or 0),
                        volume=int(leg.get("totalTradedVolume") or 0),
                    )
                )
        if not expiries and quotes:
            expiries = sorted({q.expiry for q in quotes})
        return underlying, quotes, expiries

    def _vix(self, client: httpx.Client, warnings: list[str]) -> float | None:
        try:
            payload = self._get_json(client, ALL_INDICES_URL)
        except ProviderUnavailable as exc:
            warnings.append(f"VIX unavailable from NSE: {exc}")
            return None
        for row in payload.get("data") or []:
            if str(row.get("index", "")).upper().replace(" ", "") in {"INDIAVIX", "INDIA VIX"}:
                try:
                    return float(row.get("last") or row.get("previousClose"))
                except (TypeError, ValueError):
                    return None
        warnings.append("INDIA VIX not present in NSE allIndices payload")
        return None

    def _history(self, spot: float, as_of: datetime, warnings: list[str]) -> list[Bar]:
        """Index history is not available from the option-chain endpoint.

        We try Yahoo first (same data, different host) and otherwise fall back to a
        short synthetic series *clearly flagged*, because the regime classifier needs
        a trend and a realised vol to have any meaning at all.
        """
        from .yahoo import YahooProvider

        try:
            return YahooProvider().fetch_history_base()
        except ProviderUnavailable as exc:
            warnings.append(f"index history unavailable ({exc}); using synthetic history")
            from .synthetic import build_history

            return build_history(spot, as_of, annual_vol=0.11)
