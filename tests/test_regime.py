"""Regime classification tests, driven by hand-built index histories."""

from __future__ import annotations

import math
from datetime import datetime, timedelta

from nifty_research.analytics.greeks import realized_vol
from nifty_research.analytics.regime import analyze, find_expiry_for_dte
from nifty_research.providers.synthetic import build_history, build_snapshot
from nifty_research.schemas import Bar


def _bars(closes: list[float], end: datetime) -> list[Bar]:
    return [
        Bar(day=(end - timedelta(days=len(closes) - i)).date(), close=c) for i, c in enumerate(closes)
    ]


def _snapshot(spot: float, atm_iv: float, end: datetime):
    return build_snapshot(spot=spot, atm_iv=atm_iv, as_of=end, strike_step=50, rate=0.065)


def test_rising_index_is_trend_up():
    end = datetime.now()
    closes = [20_000.0 * (1.002**i) for i in range(120)]
    regime = analyze(_snapshot(closes[-1], 0.12, end), _bars(closes, end))
    assert regime.label == "trend_up"
    assert regime.trend_strength > 0.35


def test_falling_index_is_trend_down():
    end = datetime.now()
    closes = [30_000.0 * (0.998**i) for i in range(120)]
    regime = analyze(_snapshot(closes[-1], 0.12, end), _bars(closes, end))
    assert regime.label == "trend_down"
    assert regime.trend_strength < -0.35


def _range_bound_closes(n: int = 120, base: float = 25_000.0, amplitude: float = 0.004, omega: float = 2.3):
    """High-frequency oscillation around a constant level: noise without net trend.

    A slow sine would leave the series mid-swing with a real (if temporary) trend and a
    spot far from the 50-day average; a fast one averages out over both the 20- and
    50-day windows, so the only signal left is realised vol — which is what these two
    tests are actually about.
    """
    return [base * (1.0 + amplitude * math.sin(i * omega)) for i in range(n)]


def test_range_with_iv_not_richer_than_realised_vol_is_low_vol():
    end = datetime.now()
    closes = _range_bound_closes()
    rv = realized_vol(closes, window=20)
    regime = analyze(_snapshot(closes[-1], rv * 0.95, end), _bars(closes, end))
    assert abs(regime.trend_strength) < 0.35
    assert regime.iv_rv_edge < 0.005
    assert regime.label == "range_low_vol"


def test_range_with_rich_implied_vol_is_range_high_vol():
    end = datetime.now()
    closes = _range_bound_closes()
    rv = realized_vol(closes, window=20)
    regime = analyze(_snapshot(closes[-1], rv * 1.6, end), _bars(closes, end))
    assert abs(regime.trend_strength) < 0.35
    assert regime.iv_rv_edge > 0.005
    assert regime.label == "range_high_vol"


def test_vol_spike_at_the_end_is_a_shock():
    end = datetime.now()
    calm = [25_000.0 * (1.0 + 0.0005 * i) for i in range(80)]
    spike = []
    value = calm[-1]
    for i in range(18):
        value *= 1.035 if i % 2 == 0 else 0.965
        spike.append(value)
    regime = analyze(_snapshot(spike[-1], 0.28, end), _bars(calm + spike, end))
    assert regime.label == "vol_shock"


def test_expiry_selection_respects_the_dte_window():
    end = datetime.now()
    snapshot = _snapshot(25_150.0, 0.13, end)
    expiry = find_expiry_for_dte(snapshot, 3, 14)
    assert 3 <= (expiry - end.date()).days <= 14


def test_regime_reports_its_own_reasoning():
    end = datetime.now()
    snapshot = _snapshot(25_150.0, 0.13, end)
    history = build_history(25_150.0, end, annual_vol=0.11)
    regime = analyze(snapshot, history, vix=13.5)
    assert regime.notes
    assert regime.levels["atm_iv_pct"] > 0
    assert 0 < regime.pcr
