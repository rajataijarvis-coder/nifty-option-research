"""Structure builders: turn a chain + a hypothesis into concrete sell-side candidates.

Every builder picks strikes *from the chain that actually exists* and refuses to
invent liquidity. If a structure cannot be assembled from real quotes it is skipped
and the reason is recorded, never silently guessed.
"""

from __future__ import annotations

from datetime import date

from ..config import RunConfig
from ..schemas import Candidate, ChainSnapshot, Hypothesis, Leg, OptionQuote, Regime
from .greeks import bs_delta, expected_move


def _days(a: date, b: date) -> int:
    return (a - b).days


def _nearest_strike(strikes: list[float], target: float, tolerance: float | None = None) -> float | None:
    """Closest listed strike to ``target``.

    ``tolerance`` (in index points, normally one strike step) is what stops the builders
    from silently substituting a *different* structure when the chain does not reach as
    far as the hypothesis asked: beyond the tolerance the strike is treated as missing
    and the structure is skipped with a reason.
    """
    if not strikes:
        return None
    best = min(strikes, key=lambda k: (abs(k - target), k))
    if tolerance is not None and abs(best - target) > tolerance:
        return None
    return best


def _quote(snapshot: ChainSnapshot, strike: float, option_type: str, expiry: date) -> OptionQuote | None:
    return snapshot.quote(strike, option_type, expiry)  # type: ignore[arg-type]


def _leg(snapshot: ChainSnapshot, strike: float, option_type: str, expiry: date, action: str) -> Leg | None:
    q = _quote(snapshot, strike, option_type, expiry)
    if q is None or q.ltp <= 0:
        return None
    return Leg(
        action=action,  # type: ignore[arg-type]
        option_type=option_type,  # type: ignore[arg-type]
        strike=strike,
        expiry=expiry,
        qty=1,
        price=q.ltp,
        iv=q.iv,
        oi=q.oi,
        volume=q.volume,
    )


def _delta_strike(
    snapshot: ChainSnapshot,
    expiry: date,
    option_type: str,
    target_abs_delta: float,
    config: RunConfig,
    spot: float,
    atm_iv: float,
) -> float | None:
    """Strike whose |delta| is closest to the target (used for credit-spread wings)."""
    years = max(_days(expiry, snapshot.as_of.date()), 1) / 365.0
    best, best_err = None, None
    for strike in snapshot.strikes(expiry):
        iv = atm_iv
        q = _quote(snapshot, strike, option_type, expiry)
        if q is not None and q.iv:
            iv = q.iv
        d = abs(bs_delta(spot, strike, years, iv, config.risk_free_rate, option_type))
        err = abs(d - target_abs_delta)
        if best_err is None or err < best_err:
            best, best_err = strike, err
    return best


def _straddle(
    snapshot: ChainSnapshot,
    expiry: date,
    config: RunConfig,
    regime: Regime,
    hypothesis: Hypothesis,
    name: str,
    rationale: str,
    wing_steps: int,
) -> Candidate | None:
    atm = snapshot.atm_strike(step=config.strike_step, expiry=expiry)
    ce = _leg(snapshot, atm, "CE", expiry, "SELL")
    pe = _leg(snapshot, atm, "PE", expiry, "SELL")
    if not ce or not pe:
        return None
    legs = [ce, pe]
    if wing_steps > 0:
        strikes = snapshot.strikes(expiry)
        upper = _nearest_strike(strikes, atm + wing_steps * config.strike_step, config.strike_step)
        lower = _nearest_strike(strikes, atm - wing_steps * config.strike_step, config.strike_step)
        wc = _leg(snapshot, upper, "CE", expiry, "BUY") if upper else None
        wp = _leg(snapshot, lower, "PE", expiry, "BUY") if lower else None
        if not wc or not wp:
            return None
        legs += [wc, wp]
    return Candidate(
        name=name,
        family="iron_fly" if wing_steps > 0 else "short_straddle",
        legs=legs,
        rationale=rationale,
        params={"atm_strike": atm, "wing_steps": wing_steps},
        hypothesis=hypothesis.name,
    )


def _strangle(
    snapshot: ChainSnapshot,
    expiry: date,
    config: RunConfig,
    regime: Regime,
    hypothesis: Hypothesis,
    name: str,
    rationale: str,
    sigma_multiple: float,
    wing_steps: int,
) -> Candidate | None:
    spot = snapshot.spot
    atm_iv = snapshot.atm_iv(expiry=expiry) or regime.implied_vol
    years = max(_days(expiry, snapshot.as_of.date()), 1) / 365.0
    em = expected_move(spot, years, atm_iv) * sigma_multiple
    strikes = snapshot.strikes(expiry)
    upper = _nearest_strike(strikes, spot + em, config.strike_step)
    lower = _nearest_strike(strikes, spot - em, config.strike_step)
    if upper is None or lower is None or abs(upper - lower) < config.strike_step:
        return None
    ce = _leg(snapshot, upper, "CE", expiry, "SELL")
    pe = _leg(snapshot, lower, "PE", expiry, "SELL")
    if not ce or not pe:
        return None
    legs = [ce, pe]
    if wing_steps > 0:
        wu = _nearest_strike(strikes, upper + wing_steps * config.strike_step, config.strike_step)
        wl = _nearest_strike(strikes, lower - wing_steps * config.strike_step, config.strike_step)
        wc = _leg(snapshot, wu, "CE", expiry, "BUY") if wu else None
        wp = _leg(snapshot, wl, "PE", expiry, "BUY") if wl else None
        if not wc or not wp:
            return None
        legs += [wc, wp]
    return Candidate(
        name=name,
        family="iron_condor" if wing_steps > 0 else "short_strangle",
        legs=legs,
        rationale=rationale,
        params={
            "expected_move": round(em, 1),
            "sigma_multiple": sigma_multiple,
            "wing_steps": wing_steps,
        },
        hypothesis=hypothesis.name,
    )


def _credit_spread(
    snapshot: ChainSnapshot,
    expiry: date,
    config: RunConfig,
    regime: Regime,
    hypothesis: Hypothesis,
    option_type: str,
    name: str,
    rationale: str,
) -> Candidate | None:
    spot = snapshot.spot
    atm_iv = snapshot.atm_iv(expiry=expiry) or regime.implied_vol
    short_k = _delta_strike(snapshot, expiry, option_type, 0.25, config, spot, atm_iv)
    if short_k is None:
        return None
    strikes = snapshot.strikes(expiry)
    long_target = short_k + config.wing_width_steps * config.strike_step * (1 if option_type == "CE" else -1)
    long_k = _nearest_strike(strikes, long_target, config.strike_step)
    if long_k is None or long_k == short_k:
        return None
    short_leg = _leg(snapshot, short_k, option_type, expiry, "SELL")
    long_leg = _leg(snapshot, long_k, option_type, expiry, "BUY")
    if not short_leg or not long_leg:
        return None
    family = "call_credit_spread" if option_type == "CE" else "put_credit_spread"
    return Candidate(
        name=name,
        family=family,  # type: ignore[arg-type]
        legs=[short_leg, long_leg],
        rationale=rationale,
        params={"delta_target": 0.25, "width_steps": config.wing_width_steps},
        hypothesis=hypothesis.name,
    )


def _calendar(
    snapshot: ChainSnapshot,
    front: date,
    back: date,
    config: RunConfig,
    regime: Regime,
    hypothesis: Hypothesis,
    name: str,
    rationale: str,
) -> Candidate | None:
    atm_front = snapshot.atm_strike(step=config.strike_step, expiry=front)
    atm_back = snapshot.atm_strike(step=config.strike_step, expiry=back)
    strike = atm_front if abs(atm_front - snapshot.spot) <= abs(atm_back - snapshot.spot) else atm_back
    short_leg = _leg(snapshot, strike, "CE", front, "SELL")
    long_leg = _leg(snapshot, strike, "CE", back, "BUY")
    if not short_leg or not long_leg:
        return None
    return Candidate(
        name=name,
        family="calendar",
        legs=[short_leg, long_leg],
        rationale=rationale,
        params={"strike": strike, "front": str(front), "back": str(back)},
        hypothesis=hypothesis.name,
    )


def _leg_code(leg: Leg) -> str:
    return f"{'S' if leg.action == 'SELL' else 'B'}{leg.strike:g}{'C' if leg.option_type == 'CE' else 'P'}"


def structure_name(family: str, legs: list[Leg]) -> str:
    """Unique, readable id derived from the legs that were actually chosen.

    Naming from the legs — not from the hypothesis that requested them — is what stops
    two different structures from ever sharing a name. Without that, a narrower wing or a
    different sigma could silently stand in for its sibling in the accept list, the
    research log and the report.
    """
    front = min(leg.expiry for leg in legs)
    codes = "-".join(
        _leg_code(leg) for leg in sorted(legs, key=lambda l: (l.expiry, l.strike, l.option_type))
    )
    return f"{family}_{codes}_{front.isoformat()}"


# --------------------------------------------------------------------------- entry point
def build_candidates(
    snapshot: ChainSnapshot,
    regime: Regime,
    config: RunConfig,
    hypothesis: Hypothesis,
) -> tuple[list[Candidate], list[str]]:
    """Assemble every structure this hypothesis asks for. Returns (candidates, skips)."""
    from .regime import find_expiry_for_dte

    skips: list[str] = []
    front = find_expiry_for_dte(snapshot, config.target_dte_min, config.target_dte_max)
    back_options = sorted(e for e in (snapshot.expiries or [front]) if e > front)
    out: list[Candidate] = []

    for family in hypothesis.families:
        built: Candidate | None = None
        if family == "short_straddle":
            built = _straddle(
                snapshot, front, config, regime, hypothesis,
                name=f"short_straddle_{front.isoformat()}",
                rationale=(
                    f"ATM straddle into a {regime.label} tape: sells the whole expected move "
                    f"({snapshot.atm_iv(expiry=front) or 0:.1%} IV) when the regime does not justify it."
                ),
                wing_steps=0,
            )
        elif family == "iron_fly":
            built = _straddle(
                snapshot, front, config, regime, hypothesis,
                name=f"iron_fly_{front.isoformat()}",
                rationale="ATM straddle with defined wings — same thesis as the straddle, tail capped.",
                wing_steps=max(hypothesis.wing_width_steps, 1),
            )
        elif family == "short_strangle":
            built = _strangle(
                snapshot, front, config, regime, hypothesis,
                name=f"short_strangle_{hypothesis.sigma_multiple:g}sig_{front.isoformat()}",
                rationale=(
                    f"Shorts the {hypothesis.sigma_multiple:g}σ expected move "
                    f"({regime.label}), collecting premium outside the modelled range."
                ),
                sigma_multiple=hypothesis.sigma_multiple,
                wing_steps=0,
            )
        elif family == "iron_condor":
            built = _strangle(
                snapshot, front, config, regime, hypothesis,
                name=f"iron_condor_{hypothesis.sigma_multiple:g}sig_{front.isoformat()}",
                rationale=(
                    f"{hypothesis.sigma_multiple:g}σ strangle with wings: range thesis with a hard "
                    "floor on the loss."
                ),
                sigma_multiple=hypothesis.sigma_multiple,
                wing_steps=max(hypothesis.wing_width_steps, 1),
            )
        elif family == "put_credit_spread":
            built = _credit_spread(
                snapshot, front, config, regime, hypothesis, "PE",
                name=f"put_credit_spread_{front.isoformat()}",
                rationale="25-delta put spread: paid to be wrong about downside, losses defined.",
            )
        elif family == "call_credit_spread":
            built = _credit_spread(
                snapshot, front, config, regime, hypothesis, "CE",
                name=f"call_credit_spread_{front.isoformat()}",
                rationale="25-delta call spread: caps upside participation, collects a defined credit.",
            )
        elif family == "calendar":
            if not back_options:
                skips.append("calendar: chain has only one expiry")
                continue
            built = _calendar(
                snapshot, front, back_options[0], config, regime, hypothesis,
                name=f"calendar_{front.isoformat()}_{back_options[0].isoformat()}",
                rationale=(
                    "Sells the nearer expiry and owns the next one: expresses a "
                    "front-month vol-rich / back-month vol-cheap view."
                ),
            )
        else:  # pragma: no cover - Literal-typed, defensive only
            skips.append(f"unknown family {family}")
            continue

        if built is None:
            skips.append(f"{family}: strikes/legs not available in chain for {front}")
        else:
            # single choke point for identity: whatever the hypothesis called it, the
            # structure is named after the legs it actually ended up with
            built.name = structure_name(built.family, built.legs)
            out.append(built)
    return out, skips
