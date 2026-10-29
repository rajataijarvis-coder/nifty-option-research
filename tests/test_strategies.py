"""Structure-builder tests: candidates must be assembled from the chain that exists."""

from __future__ import annotations

from nifty_research.analytics.scoring import is_defined_risk, net_credit
from nifty_research.analytics.strategies import build_candidates
from nifty_research.schemas import Hypothesis

ALL_FAMILIES = [
    "short_straddle",
    "short_strangle",
    "iron_condor",
    "iron_fly",
    "put_credit_spread",
    "call_credit_spread",
    "calendar",
]


def _build(market, regime, config, **kwargs):
    hyp = Hypothesis(
        name="test",
        families=kwargs.get("families", ALL_FAMILIES),
        sigma_multiple=kwargs.get("sigma_multiple", 1.0),
        wing_width_steps=kwargs.get("wing_width_steps", 2),
    )
    return build_candidates(market.snapshot, regime, config, hyp)


def test_every_family_can_be_built_from_the_fixture_chain(market, regime, config):
    candidates, skips = _build(market, regime, config)
    families = {c.family for c in candidates}
    assert families == {
        "short_straddle",
        "short_strangle",
        "iron_condor",
        "iron_fly",
        "put_credit_spread",
        "call_credit_spread",
        "calendar",
    }
    assert skips == []


def test_every_leg_is_priced_from_a_real_quote(market, regime, config):
    candidates, _ = _build(market, regime, config)
    for cand in candidates:
        for leg in cand.legs:
            assert leg.price > 0
            assert market.snapshot.quote(leg.strike, leg.option_type, leg.expiry) is not None
            assert leg.oi > 0


def test_straddle_sits_at_the_money(market, regime, config):
    candidates, _ = _build(market, regime, config, families=["short_straddle"])
    cand = candidates[0]
    strikes = {leg.strike for leg in cand.legs}
    assert strikes == {market.snapshot.atm_strike(expiry=cand.front_expiry())}
    assert net_credit(cand, config.lot_size) > 0


def test_condor_shorts_are_further_out_than_the_straddle(market, regime, config):
    straddle = _build(market, regime, config, families=["short_straddle"])[0][0]
    condor = _build(market, regime, config, families=["iron_condor"])[0][0]
    spot = market.snapshot.spot
    short_straddle = max(abs(leg.strike - spot) for leg in straddle.legs)
    short_condor = min(
        abs(leg.strike - spot) for leg in condor.legs if leg.action == "SELL"
    )
    assert short_condor > short_straddle
    assert is_defined_risk(condor)
    assert not is_defined_risk(straddle)


def test_sigma_multiple_widens_the_shorts(market, regime, config):
    narrow = _build(market, regime, config, families=["short_strangle"], sigma_multiple=0.75)[0][0]
    wide = _build(market, regime, config, families=["short_strangle"], sigma_multiple=1.5)[0][0]
    spot = market.snapshot.spot
    narrow_k = min(abs(leg.strike - spot) for leg in narrow.legs)
    wide_k = min(abs(leg.strike - spot) for leg in wide.legs)
    assert wide_k > narrow_k


def test_credit_spreads_have_two_legs_and_defined_risk(market, regime, config):
    for family in ("put_credit_spread", "call_credit_spread"):
        cand = _build(market, regime, config, families=[family])[0][0]
        assert len(cand.legs) == 2
        assert is_defined_risk(cand)
        assert net_credit(cand, config.lot_size) > 0


def test_calendar_spans_two_expiries(market, regime, config):
    cand = _build(market, regime, config, families=["calendar"])[0][0]
    assert len({leg.expiry for leg in cand.legs}) == 2
    short = next(leg for leg in cand.legs if leg.action == "SELL")
    long = next(leg for leg in cand.legs if leg.action == "BUY")
    assert long.expiry > short.expiry


def test_missing_strike_is_skipped_not_invented(market, regime, config):
    # ask for wings wider than the chain contains -> the builder must skip, not fabricate
    candidates, skips = _build(market, regime, config, families=["iron_condor"], wing_width_steps=40)
    assert candidates == []
    assert skips and "iron_condor" in skips[0]


def test_two_different_structures_never_share_a_name(market, regime, config):
    """Regression: a name collision once let one structure stand in for another."""
    narrow, _ = _build(market, regime, config, families=["iron_condor"], wing_width_steps=1)
    wide, _ = _build(market, regime, config, families=["iron_condor"], wing_width_steps=3)
    assert narrow[0].name != wide[0].name

    all_candidates, _ = _build(
        market, regime, config, families=ALL_FAMILIES, sigma_multiple=1.0, wing_width_steps=2
    )
    names = [cand.name for cand in all_candidates]
    assert len(names) == len(set(names))


def test_name_is_derived_from_the_legs(market, regime, config):
    cand = _build(market, regime, config, families=["short_straddle"])[0][0]
    atm = int(market.snapshot.atm_strike(expiry=cand.front_expiry()))
    assert cand.name.startswith("short_straddle_")
    assert f"S{atm}C" in cand.name and f"S{atm}P" in cand.name
    assert cand.front_expiry().isoformat() in cand.name
