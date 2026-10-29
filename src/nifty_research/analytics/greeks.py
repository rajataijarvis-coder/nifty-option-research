"""Option maths: Black-Scholes pricing/greeks, implied vol, and terminal probabilities.

Everything here is deterministic and dependency-free so the agent's numbers can be
reproduced and unit-tested without a network or a market data vendor.
"""

from __future__ import annotations

import math
from statistics import NormalDist

_N = NormalDist()
SQRT_2PI = math.sqrt(2.0 * math.pi)


def norm_cdf(x: float) -> float:
    return _N.cdf(x)


def norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / SQRT_2PI


def norm_ppf(q: float) -> float:
    return _N.inv_cdf(q)


def bs_price(
    spot: float,
    strike: float,
    years: float,
    sigma: float,
    rate: float,
    option_type: str,
) -> float:
    """European Black-Scholes price. ``option_type`` is ``"CE"`` or ``"PE"``."""
    if years <= 0 or sigma <= 0:
        return intrinsic(spot, strike, option_type)
    sqrt_t = math.sqrt(years)
    d1 = (math.log(spot / strike) + (rate + 0.5 * sigma * sigma) * years) / (sigma * sqrt_t)
    d2 = d1 - sigma * sqrt_t
    disc = math.exp(-rate * years)
    if option_type == "CE":
        return spot * norm_cdf(d1) - strike * disc * norm_cdf(d2)
    return strike * disc * norm_cdf(-d2) - spot * norm_cdf(-d1)


def intrinsic(spot: float, strike: float, option_type: str) -> float:
    if option_type == "CE":
        return max(0.0, spot - strike)
    return max(0.0, strike - spot)


def bs_delta(spot: float, strike: float, years: float, sigma: float, rate: float, option_type: str) -> float:
    if years <= 0 or sigma <= 0:
        if option_type == "CE":
            return 1.0 if spot > strike else 0.0
        return -1.0 if spot < strike else 0.0
    sqrt_t = math.sqrt(years)
    d1 = (math.log(spot / strike) + (rate + 0.5 * sigma * sigma) * years) / (sigma * sqrt_t)
    return norm_cdf(d1) if option_type == "CE" else norm_cdf(d1) - 1.0


def bs_vega(spot: float, strike: float, years: float, sigma: float, rate: float) -> float:
    """Vega per 1.00 change in vol (divide by 100 for per-vol-point)."""
    if years <= 0 or sigma <= 0:
        return 0.0
    sqrt_t = math.sqrt(years)
    d1 = (math.log(spot / strike) + (rate + 0.5 * sigma * sigma) * years) / (sigma * sqrt_t)
    return spot * norm_pdf(d1) * sqrt_t


def implied_vol(
    price: float,
    spot: float,
    strike: float,
    years: float,
    rate: float,
    option_type: str,
    lo: float = 1e-4,
    hi: float = 5.0,
    tol: float = 1e-7,
    max_iter: int = 200,
) -> float | None:
    """Bisection IV solve. Returns ``None`` when the price is outside arbitrage bounds."""
    if years <= 0:
        return None
    if price <= 0:
        return None
    lower = intrinsic(spot, strike, option_type) * math.exp(-rate * years)
    if price < lower - 1e-9:
        return None
    f_lo = bs_price(spot, strike, years, lo, rate, option_type) - price
    f_hi = bs_price(spot, strike, years, hi, rate, option_type) - price
    if f_lo * f_hi > 0:
        return None
    for _ in range(max_iter):
        mid = 0.5 * (lo + hi)
        f_mid = bs_price(spot, strike, years, mid, rate, option_type) - price
        if abs(f_mid) < tol:
            return mid
        if f_lo * f_mid <= 0:
            hi = mid
            f_hi = f_mid
        else:
            lo = mid
            f_lo = f_mid
    return 0.5 * (lo + hi)


def expected_move(spot: float, years: float, sigma: float) -> float:
    """One-sigma move in index points over ``years`` (lognormal, zero drift)."""
    return spot * sigma * math.sqrt(max(years, 0.0))


def prob_below(spot: float, level: float, years: float, sigma: float, rate: float) -> float:
    """Risk-neutral P(S_T <= level)."""
    if years <= 0 or sigma <= 0:
        return 1.0 if spot <= level else 0.0
    sqrt_t = math.sqrt(years)
    d = (math.log(level / spot) - (rate - 0.5 * sigma * sigma) * years) / (sigma * sqrt_t)
    return norm_cdf(d)


def prob_between(
    spot: float, lower: float, upper: float, years: float, sigma: float, rate: float
) -> float:
    return max(0.0, prob_below(spot, upper, years, sigma, rate) - prob_below(spot, lower, years, sigma, rate))


def realized_vol(closes: list[float], window: int = 20) -> float:
    """Annualised close-to-close realised vol from the last ``window`` returns."""
    if len(closes) < 3:
        return 0.0
    series = closes[-(window + 1):]
    rets = [
        math.log(series[i] / series[i - 1])
        for i in range(1, len(series))
        if series[i - 1] > 0 and series[i] > 0
    ]
    if len(rets) < 2:
        return 0.0
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var) * math.sqrt(252.0)


def terminal_quantiles(spot: float, years: float, sigma: float, rate: float, n: int = 512) -> list[tuple[float, float]]:
    """``[(probability_weight, S_T)]`` grid under a lognormal terminal distribution.

    The grid is used for POP / expected value / CVaR instead of Monte Carlo so runs
    are exactly reproducible (same inputs -> same numbers, every time).
    """
    if n < 8:
        n = 8
    out: list[tuple[float, float]] = []
    w = 1.0 / n
    if years <= 0 or sigma <= 0:
        return [(1.0, spot)]
    sqrt_t = math.sqrt(years)
    drift = (rate - 0.5 * sigma * sigma) * years
    for i in range(n):
        q = (i + 0.5) / n
        z = norm_ppf(q)
        out.append((w, spot * math.exp(drift + sigma * sqrt_t * z)))
    return out
