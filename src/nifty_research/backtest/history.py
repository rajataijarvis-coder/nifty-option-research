"""Index history and the expiry calendar.

The backtest needs two things the live agent does not: a *path* of index closes, and a
roll calendar. Both are pluggable and both record where they came from, because the
difference between a backtest on real NIFTY closes and one on a seeded random walk is
the difference between evidence and decoration.

Sources:

* ``csv:<path>`` — columns ``date,close[,vix]`` (header names are matched loosely). This
  is how real history enters the system: fetch it once on a network where the data is
  reachable, or export it from any vendor, and commit it.
* ``yahoo`` — ^NSEI daily closes plus ^INDIAVIX, when the network allows.
* ``synthetic`` — seeded geometric Brownian motion. Deterministic, offline, and labelled
  as synthetic everywhere it appears.

The expiry calendar is derived from the trading days themselves (last trading day of each
ISO week), so a holiday short week or a change of expiry weekday does not need a code
change — and no lookahead is possible in the calendar either.
"""

from __future__ import annotations

import csv
import math
import random
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from ..schemas import Bar

TRADING_DAYS_PER_YEAR = 252


@dataclass
class IndexHistory:
    days: list[date]
    closes: list[float]
    vix: dict[date, float] = field(default_factory=dict)
    source: str = "unknown"
    warnings: list[str] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.days)

    @property
    def is_real(self) -> bool:
        return not self.source.startswith("synthetic")

    def index_of(self, day: date) -> int | None:
        try:
            return self.days.index(day)
        except ValueError:
            return None

    def bars(self, start: int, end: int) -> list[Bar]:
        """Bars in ``[start, end]`` inclusive — used for regime reads with no lookahead."""
        lo = max(0, start)
        return [
            Bar(day=self.days[i], close=self.closes[i])
            for i in range(lo, min(end, len(self.days) - 1) + 1)
        ]

    def bar_at_index(self, i: int) -> Bar:
        return Bar(day=self.days[i], close=self.closes[i])

    def vix_at(self, day: date) -> float | None:
        if day in self.vix:
            return self.vix[day]
        earlier = [d for d in self.vix if d <= day]
        return self.vix[max(earlier)] if earlier else None

    def close_on_or_after(self, day: date) -> tuple[date, float] | None:
        for i, d in enumerate(self.days):
            if d >= day:
                return d, self.closes[i]
        return None


# --------------------------------------------------------------------------- loaders
def from_csv(path: Path | str, date_column: str | None = None) -> IndexHistory:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"history csv not found: {path}")
    days: list[date] = []
    closes: list[float] = []
    vix: dict[date, float] = {}
    rows = list(csv.DictReader(path.read_text().splitlines()))
    if not rows:
        raise ValueError(f"{path} has no rows")
    fields = {name.lower().strip(): name for name in rows[0]}
    day_key = (date_column or "").lower() or next(
        (fields[k] for k in ("date", "day", "timestamp", "dt") if k in fields), None
    )
    close_key = next(
        (fields[k] for k in ("close", "adj close", "adj_close", "close_price", "last") if k in fields),
        None,
    )
    vix_key = next((fields[k] for k in ("vix", "india_vix", "india vix", "iv") if k in fields), None)
    if day_key is None or close_key is None:
        raise ValueError(f"{path}: need a date and a close column, found {sorted(fields.values())}")

    for row in rows:
        raw_day = (row.get(day_key) or "").strip()
        raw_close = (row.get(close_key) or "").strip()
        if not raw_day or not raw_close or raw_close.upper() in {"N/A", "NULL", "NAN"}:
            continue
        day = _parse_day(raw_day)
        close = float(raw_close.replace(",", ""))
        days.append(day)
        closes.append(close)
        if vix_key and (row.get(vix_key) or "").strip():
            try:
                vix[day] = float(row[vix_key])
            except ValueError:
                pass

    order = sorted(range(len(days)), key=lambda i: days[i])
    days = [days[i] for i in order]
    closes = [closes[i] for i in order]
    return IndexHistory(
        days=days,
        closes=closes,
        vix=vix,
        source=f"csv:{path.name}",
    )


def _parse_day(raw: str) -> date:
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d-%b-%Y", "%Y/%m/%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(raw[:11].strip(), fmt).date()
        except ValueError:
            continue
    raise ValueError(f"unrecognised date: {raw!r}")


def from_yahoo(index_symbol: str = "^NSEI", range_: str = "10y") -> IndexHistory:
    from ..providers.yahoo import YahooProvider, bars_from_chart, _chart

    provider = YahooProvider(index_symbol=index_symbol)
    bars = provider.fetch_history_base() if range_ == "1y" else _bars_range(index_symbol, range_)
    if len(bars) < 120:
        raise RuntimeError(f"only {len(bars)} index bars returned by yahoo")
    vix: dict[date, float] = {}
    try:
        vix_bars = bars_from_chart(_chart("^INDIAVIX", range_=range_, interval="1d"))
        vix = {bar.day: bar.close for bar in vix_bars}
    except Exception:  # noqa: BLE001 - VIX is a bonus, not a requirement
        pass
    return IndexHistory(
        days=[b.day for b in bars],
        closes=[b.close for b in bars],
        vix=vix,
        source=f"yahoo:{index_symbol}",
        warnings=[] if vix else ["no VIX history from yahoo; IV will be estimated from realised vol"],
    )


def _bars_range(symbol: str, range_: str) -> list[Bar]:
    from ..providers.yahoo import bars_from_chart, _chart

    return bars_from_chart(_chart(symbol, range_=range_, interval="1d"))


def from_synthetic(
    days: int = 1000,
    seed: int = 17,
    start_level: float = 21_000.0,
    annual_vol: float = 0.145,
    annual_drift: float = 0.09,
) -> IndexHistory:
    """Seeded GBM path with a rolling VIX-like series. Fully deterministic."""
    rng = random.Random(seed)
    daily_sigma = annual_vol / math.sqrt(TRADING_DAYS_PER_YEAR)
    daily_mu = annual_drift / TRADING_DAYS_PER_YEAR
    closes: list[float] = []
    level = start_level
    for _ in range(days):
        level *= math.exp(rng.gauss(daily_mu - 0.5 * daily_sigma**2, daily_sigma))
        closes.append(round(level, 2))

    today = datetime.now().date()
    calendar: list[date] = []
    cursor = today - timedelta(days=int(days * 1.45) + 10)
    while len(calendar) < days:
        if cursor.weekday() < 5:
            calendar.append(cursor)
        cursor += timedelta(days=1)

    vix: dict[date, float] = {}
    window = 20
    for i, day in enumerate(calendar):
        if i < window:
            continue
        recent = closes[i - window : i + 1]
        rets = [
            math.log(recent[j] / recent[j - 1])
            for j in range(1, len(recent))
            if recent[j - 1] > 0
        ]
        mean = sum(rets) / len(rets)
        var = sum((r - mean) ** 2 for r in rets) / max(len(rets) - 1, 1)
        rv = math.sqrt(var) * math.sqrt(TRADING_DAYS_PER_YEAR)
        vix[day] = round(rv * 115.0, 2)  # realistic variance risk premium, in vol points

    return IndexHistory(
        days=calendar,
        closes=closes,
        vix=vix,
        source="synthetic:gbm",
        warnings=["synthetic index path (seeded GBM) — not a real NIFTY history"],
    )


def load(spec: str = "auto", **kwargs) -> IndexHistory:
    """``auto`` tries Yahoo then falls back to synthetic, recording the fallback."""
    if spec.startswith("csv:"):
        return from_csv(spec[4:])
    if spec == "csv":
        raise ValueError("use csv:<path> to name the history file")
    if spec == "yahoo":
        return from_yahoo(**kwargs)
    if spec == "synthetic":
        # callers pass the same kwargs to every source; only yahoo understands range_
        return from_synthetic(**{k: v for k, v in kwargs.items() if k != "range_"})
    if spec == "auto":
        try:
            history = from_yahoo(**kwargs)
        except Exception as exc:  # noqa: BLE001 - offline is the normal case here
            history = from_synthetic(**{k: v for k, v in kwargs.items() if k != "range_"})
            history.warnings.append(f"yahoo history unavailable ({type(exc).__name__}: {exc})")
        return history
    raise ValueError(f"unknown history spec {spec!r}")


# --------------------------------------------------------------------------- calendar
def weekly_expiries(days: list[date], expiry_weekday: int | None = None) -> list[date]:
    """Last trading day of each ISO week (or of each week ending on ``expiry_weekday``)."""
    out: list[date] = []
    seen: set[tuple[int, int]] = set()
    ordered = sorted(days)
    for i, day in enumerate(ordered):
        key = day.isocalendar()[:2]
        is_last_of_week = i == len(ordered) - 1 or ordered[i + 1].isocalendar()[:2] != key
        if not is_last_of_week:
            continue
        if expiry_weekday is not None and day.weekday() != expiry_weekday:
            # keep the week's last trading day even then: an early close is still the expiry
            pass
        if key not in seen:
            seen.add(key)
            out.append(day)
    return out


def entries_for_expiries(
    history: IndexHistory, entry_offset_trading_days: int = 3
) -> list[tuple[int, int, date]]:
    """``[(entry_index, expiry_index, expiry_day)]`` for every roll in the history."""
    expiries = weekly_expiries(history.days)
    calendar_index = {day: i for i, day in enumerate(history.days)}
    rolls: list[tuple[int, int, date]] = []
    for expiry in expiries:
        expiry_idx = calendar_index[expiry]
        entry_idx = expiry_idx - entry_offset_trading_days
        if entry_idx < 60:  # need history for the 20/50-day trend read
            continue
        rolls.append((entry_idx, expiry_idx, expiry))
    return rolls
