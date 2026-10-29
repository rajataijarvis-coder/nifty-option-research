"""Regime classification: where the index *is* right now, and what that implies."""

from __future__ import annotations

from ..schemas import Bar, ChainSnapshot, Regime
from .greeks import realized_vol


def _sma(values: list[float], window: int) -> float | None:
    if len(values) < window:
        return None
    return sum(values[-window:]) / window


def find_expiry_for_dte(snapshot: ChainSnapshot, dte_min: int, dte_max: int):
    """Pick the expiry whose days-to-expiry sits inside the tradable window."""
    as_of = snapshot.as_of.date()
    candidates = sorted(snapshot.expiries or [snapshot.expiry])
    in_window = [e for e in candidates if dte_min <= (e - as_of).days <= dte_max]
    if in_window:
        return in_window[0]
    later = [e for e in candidates if (e - as_of).days >= dte_min]
    if later:
        return later[0]
    return candidates[-1]


def analyze(
    snapshot: ChainSnapshot,
    history: list[Bar],
    vix: float | None = None,
    expiry=None,
) -> Regime:
    """Classify the index position into one of five trading regimes.

    The label is derived from three independent measurements — trend position
    (spot vs 20/50-day averages and the slope of the 20-day), realised vol, and the
    implied-vs-realised edge — plus VIX and put/call OI as confirmation. Every
    component is reported so the label can be argued with.
    """
    closes = [b.close for b in history]
    notes: list[str] = []
    spot = snapshot.spot

    sma20 = _sma(closes, 20)
    sma50 = _sma(closes, 50)
    sma20_prev = _sma(closes[:-5], 20) if len(closes) > 25 else None

    pos20 = (spot - sma20) / sma20 if sma20 else 0.0
    pos50 = (spot - sma50) / sma50 if sma50 else 0.0
    slope20 = (sma20 - sma20_prev) / sma20_prev if (sma20 and sma20_prev) else 0.0

    raw_trend = 12.0 * pos50 + 25.0 * slope20
    trend_strength = max(-1.0, min(1.0, raw_trend))

    rv = realized_vol(closes, window=20)
    rv_long = realized_vol(closes, window=60)
    iv = snapshot.atm_iv(expiry=expiry)
    if iv is None:
        iv = (vix / 100.0) if vix else max(rv, 0.10)
        notes.append("ATM IV unavailable in chain; fell back to VIX/realised vol proxy")
    iv_rv_edge = iv - rv

    pe_oi = snapshot.total_oi("PE", expiry=expiry)
    ce_oi = snapshot.total_oi("CE", expiry=expiry)
    pcr = (pe_oi / ce_oi) if ce_oi else 1.0

    window = closes[-20:] if len(closes) >= 20 else closes
    levels = {
        "sma20_pct_from_spot": (pos20 * 100.0) if sma20 else 0.0,
        "sma50_pct_from_spot": (pos50 * 100.0) if sma50 else 0.0,
        "range_high_20": max(window) if window else spot,
        "range_low_20": min(window) if window else spot,
        "atm_iv_pct": iv * 100.0,
    }

    vol_shock = False
    if rv_long and rv > 1.6 * rv_long:
        vol_shock = True
        notes.append(f"realised vol {rv:.1%} is {rv / rv_long:.1f}x its 60-day baseline -> shock")
    if vix is not None and vix > 22.0:
        vol_shock = True
        notes.append(f"VIX {vix:.1f} above the 22 shock threshold")

    if vol_shock:
        label = "vol_shock"
    elif trend_strength >= 0.35:
        label = "trend_up"
    elif trend_strength <= -0.35:
        label = "trend_down"
    elif iv_rv_edge < 0.005:
        label = "range_low_vol"
    else:
        label = "range_high_vol"

    if sma20 and sma50:
        notes.append(
            f"spot {spot:,.0f} is {pos20 * 100:+.2f}% vs 20dma and {pos50 * 100:+.2f}% vs 50dma "
            f"(20dma slope {slope20 * 100:+.2f}%)"
        )
    notes.append(f"realised vol (20d) {rv:.2%}, ATM IV {iv:.2%}, edge {iv_rv_edge:+.2%}")
    notes.append(f"put/call OI ratio {pcr:.2f}")

    return Regime(
        label=label,
        trend_strength=round(trend_strength, 4),
        realized_vol=round(rv, 4),
        implied_vol=round(iv, 4),
        iv_rv_edge=round(iv_rv_edge, 4),
        pcr=round(pcr, 3),
        vix=vix,
        levels={k: round(v, 2) for k, v in levels.items()},
        notes=notes,
    )
