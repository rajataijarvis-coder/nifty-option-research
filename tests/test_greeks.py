"""Option-maths tests: the numbers the whole agent stands on."""

from __future__ import annotations

import math

import pytest

from nifty_research.analytics.greeks import (
    bs_delta,
    bs_price,
    expected_move,
    implied_vol,
    prob_between,
    realized_vol,
    terminal_quantiles,
)


def test_put_call_parity():
    spot, strike, years, sigma, rate = 25_150.0, 25_200.0, 7 / 365, 0.14, 0.065
    call = bs_price(spot, strike, years, sigma, rate, "CE")
    put = bs_price(spot, strike, years, sigma, rate, "PE")
    assert call - put == pytest.approx(spot - strike * math.exp(-rate * years), abs=1e-6)


def test_call_and_put_are_equal_at_the_forward_strike():
    # ATM spot is NOT the forward: C - P = S - K e^{-rT}, so equality needs K = S e^{rT}.
    spot, years, sigma, rate = 25_150.0, 7 / 365, 0.14, 0.065
    forward_default = 25_150.0  # where ATM spot sits relative to the forward
    call = bs_price(spot, forward_default, years, sigma, rate, "CE")
    put = bs_price(spot, forward_default, years, sigma, rate, "PE")
    assert call - put == pytest.approx(spot * (1 - math.exp(-rate * years)), abs=1e-6)

    strike = spot * math.exp(rate * years)
    assert bs_price(spot, strike, years, sigma, rate, "CE") == pytest.approx(
        bs_price(spot, strike, years, sigma, rate, "PE"), rel=1e-9
    )


def test_implied_vol_round_trip():
    spot, strike, years, sigma, rate = 25_150.0, 24_800.0, 14 / 365, 0.23, 0.065
    price = bs_price(spot, strike, years, sigma, rate, "PE")
    solved = implied_vol(price, spot, strike, years, rate, "PE")
    assert solved == pytest.approx(sigma, abs=1e-4)


def test_implied_vol_rejects_below_intrinsic():
    assert implied_vol(1.0, 25_150.0, 24_000.0, 7 / 365, 0.065, "CE") is None


def test_delta_signs_and_bounds():
    spot, years, sigma, rate = 25_150.0, 7 / 365, 0.14, 0.065
    assert bs_delta(spot, spot, years, sigma, rate, "CE") == pytest.approx(0.52, abs=0.05)
    assert bs_delta(spot, spot, years, sigma, rate, "PE") == pytest.approx(-0.48, abs=0.05)
    deep_call = bs_delta(spot, spot * 0.8, years, sigma, rate, "CE")
    assert 0.99 <= deep_call <= 1.0


def test_expected_move_scales_with_sqrt_time():
    one = expected_move(25_150.0, 7 / 365, 0.14)
    four = expected_move(25_150.0, 28 / 365, 0.14)
    assert four == pytest.approx(2 * one, rel=1e-9)


def test_prob_between_one_sigma_is_about_two_thirds():
    assert prob_between(25_150.0, 25_150 - 460, 25_150 + 460, 7 / 365, 0.14, 0.0) == pytest.approx(
        0.68, abs=0.03
    )


def test_realized_vol_of_flat_series_is_zero():
    assert realized_vol([25_000.0] * 30, window=20) == 0.0


def test_realized_vol_recovers_known_sigma():
    # alternating reciprocal moves -> exactly zero-mean log returns of size ln(1.01)
    closes = [25_000.0]
    for i in range(40):
        closes.append(closes[-1] * (1.01 if i % 2 == 0 else 1 / 1.01))
    assert realized_vol(closes, window=20) == pytest.approx(
        math.log(1.01) * math.sqrt(252), rel=0.03
    )


def test_terminal_quantile_grid_has_risk_neutral_drift():
    spot, years, sigma, rate = 25_150.0, 30 / 365, 0.14, 0.065
    mean = sum(w * s for w, s in terminal_quantiles(spot, years, sigma, rate, n=4096))
    assert mean == pytest.approx(spot * math.exp(rate * years), rel=0.01)
