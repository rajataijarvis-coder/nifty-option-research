"""Scoring: expiry-payoff geometry plus a probability-weighted expectation.

Two deliberate modelling choices, both stated in the output rather than buried here:

1. **Payoff** is computed on a real expiry grid, leg by leg. Legs that expire before
   the analysis horizon are marked to intrinsic; legs that outlive it are marked with
   Black-Scholes at their remaining time. This makes calendars and diagonals score
   correctly instead of being treated as vanilla spreads.
2. **Expectation** prices the options at *chain IV* but draws terminal outcomes from a
   blended vol (``rv_weight * realised + (1 - rv_weight) * implied``). That blend is the
   premium-selling edge being researched: you are paid at implied and settle nearer
   realised. Set ``rv_weight = 0`` and every structure converges to zero EV — which is
   the honest null result, and a useful sanity check.
"""

from __future__ import annotations

from ..config import RunConfig
from ..schemas import Candidate, ChainSnapshot, Leg, Regime, ScoreComponents, ScoredCandidate
from .greeks import bs_price, intrinsic, terminal_quantiles

GRID_POINTS = 401


def leg_value(leg: Leg, spot_t: float, horizon, config: RunConfig, fallback_iv: float) -> float:
    """Value of one leg at ``horizon`` (a date) with the index at ``spot_t``."""
    if leg.expiry <= horizon:
        return intrinsic(spot_t, leg.strike, leg.option_type)
    years = max((leg.expiry - horizon).days, 0) / 365.0
    sigma = leg.iv or fallback_iv
    if years <= 0 or sigma <= 0:
        return intrinsic(spot_t, leg.strike, leg.option_type)
    return bs_price(spot_t, leg.strike, years, sigma, config.risk_free_rate, leg.option_type)


def structure_pnl(
    candidate: Candidate,
    spot_t: float,
    horizon,
    config: RunConfig,
    fallback_iv: float,
    lot_size: int,
) -> float:
    """P&L of one lot-set of the structure at ``horizon``, in currency units."""
    total = 0.0
    for leg in candidate.legs:
        value = leg_value(leg, spot_t, horizon, config, fallback_iv)
        total += leg.sign * leg.qty * lot_size * (value - leg.price)
    return total


def net_credit(candidate: Candidate, lot_size: int) -> float:
    return sum(-leg.sign * leg.qty * lot_size * leg.price for leg in candidate.legs)


def is_covered(legs: list[Leg], option_type: str) -> bool:
    """True when every short of this type is capped by a long."""
    shorts = [l for l in legs if l.option_type == option_type and l.action == "SELL"]
    longs = [l for l in legs if l.option_type == option_type and l.action == "BUY"]
    for short in shorts:
        covered = False
        for long in longs:
            if long.qty < short.qty:
                continue
            if long.expiry > short.expiry and long.strike == short.strike:
                covered = True  # calendar: the far leg keeps the position hedgeable
            elif long.expiry >= short.expiry:
                more_otm = long.strike > short.strike if option_type == "CE" else long.strike < short.strike
                if more_otm:
                    covered = True
            if covered:
                break
        if not covered:
            return False
    return True


def is_defined_risk(candidate: Candidate) -> bool:
    return is_covered(candidate.legs, "CE") and is_covered(candidate.legs, "PE")


def _breakevens(grid: list[float], pnls: list[float], spot: float) -> list[float]:
    out: list[float] = []
    for i in range(1, len(grid)):
        a, b = pnls[i - 1], pnls[i]
        if a == 0.0:
            out.append(grid[i - 1])
        elif a * b < 0:
            x0, x1 = grid[i - 1], grid[i]
            frac = abs(a) / (abs(a) + abs(b))
            out.append(x0 + frac * (x1 - x0))
    if pnls and pnls[-1] == 0.0:
        out.append(grid[-1])
    return [round(x, 2) for x in sorted(out)]


def expected_value(candidate: Candidate, snapshot: ChainSnapshot, regime: Regime, config: RunConfig) -> dict:
    horizon = candidate.front_expiry()
    sigma = config.rv_weight * regime.realized_vol + (1.0 - config.rv_weight) * regime.implied_vol
    sigma = max(sigma, 0.01)
    years = max((horizon - snapshot.as_of.date()).days, 1) / 365.0
    draws = terminal_quantiles(snapshot.spot, years, sigma, config.risk_free_rate, n=512)
    pnls = [
        structure_pnl(candidate, s_t, horizon, config, regime.implied_vol, config.lot_size)
        for _, s_t in draws
    ]
    total_w = sum(w for w, _ in draws) or 1.0
    win_w = sum(w for (w, _), p in zip(draws, pnls) if p > 0)
    ev = sum(w * p for (w, _), p in zip(draws, pnls)) / total_w
    order = sorted(pnls)
    k = max(1, int(0.05 * len(order)))
    cvar_5 = sum(order[:k]) / k
    return {
        "pop": round(win_w / total_w, 4),
        "ev": round(ev, 2),
        "cvar_5": round(cvar_5, 2),
        "expectation_vol": round(sigma, 4),
        "horizon": horizon,
        "years": round(years, 4),
    }


def score_candidate(
    candidate: Candidate,
    snapshot: ChainSnapshot,
    regime: Regime,
    config: RunConfig,
) -> ScoredCandidate:
    lot = config.lot_size
    horizon = candidate.front_expiry()
    spot = snapshot.spot

    credit = net_credit(candidate, lot)

    grid = [spot * (0.6 + 0.8 * i / (GRID_POINTS - 1)) for i in range(GRID_POINTS)]
    pnls = [structure_pnl(candidate, s, horizon, config, regime.implied_vol, lot) for s in grid]
    max_profit = max(pnls)
    # reported as the magnitude you can lose (positive rupees), which is how a risk
    # number is read; the underlying payoff minimum is -(max_loss)
    max_loss = abs(min(pnls))
    bes = _breakevens(grid, pnls, spot)

    defined = is_defined_risk(candidate)
    short_lots = sum(leg.qty for leg in candidate.legs if leg.action == "SELL")
    margin_proxy = max_loss if defined else config.naked_margin_pct * spot * lot * max(short_lots, 1)
    risk_denominator = max(margin_proxy, 1.0)

    stats = expected_value(candidate, snapshot, regime, config)
    ev = stats["ev"]

    short_premium = sum(
        -leg.sign * leg.qty * leg.price for leg in candidate.legs if leg.action == "SELL"
    )
    if short_premium > 0:
        cand_iv = (
            sum((leg.iv or regime.implied_vol) * leg.price for leg in candidate.legs if leg.action == "SELL")
            / short_premium
        )
    else:
        cand_iv = regime.implied_vol
    iv_edge = cand_iv - regime.realized_vol

    leg_liquidity = [
        min(1.0, (leg.oi + 0.25 * leg.volume) / max(config.min_leg_oi, 1)) for leg in candidate.legs
    ]
    liquidity = min(leg_liquidity) if leg_liquidity else 0.0

    rom = credit / risk_denominator
    ev_to_risk = ev / risk_denominator

    s_ev = _clip(ev_to_risk / 0.30, -1.0, 1.0)
    s_pop = _clip((stats["pop"] - 0.50) / 0.35, -1.0, 1.0)
    s_edge = _clip(iv_edge / 0.05, -1.0, 1.0)
    s_liq = liquidity
    s_rom = _clip(rom / 0.15, 0.0, 1.0)
    score = 100.0 * (0.35 * s_ev + 0.25 * s_pop + 0.15 * s_edge + 0.15 * s_liq + 0.10 * s_rom)

    dte = max((horizon - snapshot.as_of.date()).days, 0)
    return ScoredCandidate(
        candidate=candidate,
        credit=round(credit, 2),
        max_loss=round(max_loss, 2),
        max_profit=round(max_profit, 2),
        breakevens=bes,
        pop=stats["pop"],
        cvar_5=stats["cvar_5"],
        ev_per_lot=ev,
        margin_proxy=round(margin_proxy, 2),
        undefined_risk=not defined,
        dte=dte,
        rom=round(rom, 4),
        liquidity=round(liquidity, 4),
        iv_edge=round(iv_edge, 4),
        score=round(score, 2),
        components=ScoreComponents(
            ev_per_lot=ev,
            ev_to_risk=round(ev_to_risk, 4),
            pop=stats["pop"],
            iv_edge=round(iv_edge, 4),
            liquidity=round(liquidity, 4),
            rom=round(rom, 4),
        ),
        assumptions={
            "expectation_vol": stats["expectation_vol"],
            "rv_weight": config.rv_weight,
            "horizon": horizon.isoformat(),
            "years": stats["years"],
            "pricing": "chain IV / Black-Scholes",
            "lot_size": lot,
            "grid_low": round(grid[0], 1),
            "grid_high": round(grid[-1], 1),
        },
    )


def _clip(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def rank(scored: list[ScoredCandidate]) -> list[ScoredCandidate]:
    """Sort by score, then by POP, then by the smaller max loss."""
    return sorted(scored, key=lambda s: (-s.score, -s.pop, s.max_loss))
