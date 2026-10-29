"""Scoring tests: geometry, probability-weighted expectation, and the null result."""

from __future__ import annotations

from datetime import timedelta

from nifty_research.analytics.greeks import bs_price
from nifty_research.analytics.scoring import score_candidate, structure_pnl
from nifty_research.analytics.strategies import build_candidates
from nifty_research.schemas import Candidate, Hypothesis, Leg, Regime


def _score_family(market, regime, config, family, **kwargs):
    hyp = Hypothesis(
        name="t", families=[family], sigma_multiple=kwargs.get("sigma_multiple", 1.0),
        wing_width_steps=kwargs.get("wing_width_steps", 2),
    )
    cand = build_candidates(market.snapshot, regime, config, hyp)[0][0]
    return cand, score_candidate(cand, market.snapshot, regime, config)


def test_short_strangle_collects_credit_and_carries_tail_risk(market, regime, config):
    cand, scored = _score_family(market, regime, config, "short_strangle")
    assert scored.credit > 0
    assert scored.undefined_risk is True
    assert 0.0 < scored.pop < 1.0
    assert scored.cvar_5 < 0  # the 5% tail is a loss for a naked short
    assert -100 <= scored.score <= 100


def test_iron_condor_max_loss_matches_its_wings(market, regime, config):
    cand, scored = _score_family(market, regime, config, "iron_condor")
    shorts = [leg for leg in cand.legs if leg.action == "SELL"]
    longs = [leg for leg in cand.legs if leg.action == "BUY"]
    assert len(shorts) == 2 and len(longs) == 2
    width = min(abs(l.strike - s.strike) for l in longs for s in shorts if l.option_type == s.option_type)
    expected_max_loss = width * config.lot_size - scored.credit
    assert scored.max_loss == pytest_approx(expected_max_loss, abs=2.0)
    assert scored.undefined_risk is False


def pytest_approx(value, abs=1e-6):  # tiny local helper keeps the import list short
    import pytest

    return pytest.approx(value, abs=abs)


def test_expiry_payoff_at_the_money_straddle_is_the_credit(market, regime, config):
    cand, _ = _score_family(market, regime, config, "short_straddle")
    pnl_at_spot = structure_pnl(
        cand, market.snapshot.spot, cand.front_expiry(), config, regime.implied_vol, config.lot_size
    )
    from nifty_research.analytics.scoring import net_credit

    assert pnl_at_spot == pytest_approx(net_credit(cand, config.lot_size), abs=1e-6)


def _atm_straddle_priced_at_iv(market, regime, config, sigma: float) -> Candidate:
    """A straddle whose quotes are exactly Black-Scholes at ``sigma`` — the model's own null."""
    snapshot = market.snapshot
    as_of = snapshot.as_of.date()
    expiry = as_of + timedelta(days=7)
    atm = snapshot.atm_strike(expiry=expiry)
    years = 7 / 365
    legs = [
        Leg(
            action="SELL",
            option_type=ot,  # type: ignore[arg-type]
            strike=atm,
            expiry=expiry,
            price=bs_price(snapshot.spot, atm, years, sigma, config.risk_free_rate, ot),
            iv=sigma,
            oi=200_000,
            volume=50_000,
        )
        for ot in ("CE", "PE")
    ]
    return Candidate(name="null_straddle", family="short_straddle", legs=legs)


def test_null_result_ev_is_zero_up_to_grid_tolerance(market, config):
    """With rv_weight=0 the agent prices and settles at the same vol -> EV must be ~0.

    The residual is quadrature error from the 512-point terminal grid (the payoff is
    unbounded in the tails), so the assertion is relative to the credit collected:
    below 0.5% is a null result, not an edge.
    """
    snapshot = market.snapshot
    sigma = 0.14
    flat_regime = Regime(
        label="range_low_vol",
        trend_strength=0.0,
        realized_vol=sigma,
        implied_vol=sigma,
        iv_rv_edge=0.0,
        pcr=1.0,
        vix=14.0,
    )
    null_config = RunConfigLike(config, rv_weight=0.0)
    cand = _atm_straddle_priced_at_iv(market, flat_regime, config, sigma)
    scored = score_candidate(cand, snapshot, flat_regime, null_config)
    assert scored.credit > 0
    assert abs(scored.ev_per_lot) < 0.005 * scored.credit


def test_selling_rich_implied_vol_has_positive_expected_value(market, config):
    """IV above realised vol is the edge the agent is looking for."""
    snapshot = market.snapshot
    rich_regime = Regime(
        label="range_high_vol",
        trend_strength=0.0,
        realized_vol=0.10,
        implied_vol=0.16,
        iv_rv_edge=0.06,
        pcr=1.0,
        vix=16.0,
    )
    cand = _atm_straddle_priced_at_iv(market, rich_regime, config, 0.16)
    scored = score_candidate(cand, snapshot, rich_regime, config)
    assert scored.ev_per_lot > 0


def test_richer_expectation_vol_lowers_short_premium_ev(market, regime, config):
    """Monotonicity: the higher the vol you expect to realise, the worse a short looks."""
    hyp = Hypothesis(name="t", families=["short_strangle"], sigma_multiple=1.0, wing_width_steps=0)
    cand = build_candidates(market.snapshot, regime, config, hyp)[0][0]
    low_expectation = RunConfigLike(config, rv_weight=1.0)  # settle at realised vol (cheap)
    high_expectation = RunConfigLike(config, rv_weight=0.0)  # settle at implied vol (rich)
    cheap = score_candidate(cand, market.snapshot, regime, low_expectation)
    rich = score_candidate(cand, market.snapshot, regime, high_expectation)
    assert cheap.ev_per_lot >= rich.ev_per_lot


def RunConfigLike(config, **overrides):
    from nifty_research.config import RunConfig

    kwargs = config.to_dict()
    kwargs["out_dir"] = config.out_dir
    kwargs["log_path"] = config.log_path
    kwargs["positions_path"] = config.positions_path
    kwargs.update(overrides)
    return RunConfig(**{k: v for k, v in kwargs.items()})
