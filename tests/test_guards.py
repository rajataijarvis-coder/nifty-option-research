"""Guard-rail tests: the rules that decide what gets published."""

from __future__ import annotations

from nifty_research.analytics import guards
from nifty_research.analytics.scoring import score_candidate
from nifty_research.analytics.strategies import build_candidates
from nifty_research.config import RunConfig
from nifty_research.schemas import Hypothesis


def _condor(market, regime, config, **overrides):
    hyp = Hypothesis(name="t", families=["iron_condor"], sigma_multiple=1.0, wing_width_steps=2)
    cand = build_candidates(market.snapshot, regime, config, hyp)[0][0]
    scored = score_candidate(cand, market.snapshot, regime, config)
    cfg = _clone(config, **overrides)
    return scored, guards.evaluate(scored, cfg, regime)


def _clone(config: RunConfig, **overrides) -> RunConfig:
    kwargs = config.to_dict()
    kwargs["out_dir"] = config.out_dir
    kwargs["log_path"] = config.log_path
    kwargs["positions_path"] = config.positions_path
    kwargs.update(overrides)
    return RunConfig(**kwargs)


def _codes(result) -> set[str]:
    return {v.code for v in result.violations}


def test_thin_liquidity_is_caught(market, regime, config):
    _, result = _condor(market, regime, config, min_leg_oi=10**9)
    assert "LIQUIDITY_THIN" in _codes(result)
    assert not result.passed


def test_impossible_pop_requirement_is_caught(market, regime, config):
    _, result = _condor(market, regime, config, min_pop=0.999)
    assert "POP_BELOW_MIN" in _codes(result)


def test_tight_max_loss_cap_is_caught(market, regime, config):
    _, result = _condor(market, regime, config, max_loss_per_lot=1.0, min_leg_oi=0)
    assert "MAX_LOSS_EXCEEDED" in _codes(result)


def test_dte_window_is_enforced(market, regime, config):
    _, result = _condor(market, regime, config, target_dte_min=30, target_dte_max=45)
    assert "DTE_OUT_OF_WINDOW" in _codes(result)


def test_relaxation_is_monotone_and_bounded(market, regime, config):
    relaxed1 = config.with_relaxation(1)
    relaxed3 = config.with_relaxation(3)
    assert relaxed1.min_pop < config.min_pop
    assert relaxed3.min_pop < relaxed1.min_pop
    assert relaxed3.min_leg_oi < relaxed1.min_leg_oi
    assert relaxed3.max_loss_per_lot > config.max_loss_per_lot
    assert config.with_relaxation(0) is config


def test_relaxation_can_flip_a_marginal_rejection(market, regime, config):
    stem = RunConfig(
        provider="fixture",
        out_dir=config.out_dir,
        log_path=config.log_path,
        min_leg_oi=8_000,  # deep wings in the fixture chain are thinner than this
        min_pop=0.0,
        min_credit_ratio=0.0,
        max_loss_per_lot=10**9,
    )
    scored = score_candidate(
        build_candidates(
            market.snapshot, regime, stem, Hypothesis(name="t", families=["iron_condor"], wing_width_steps=2)
        )[0][0],
        market.snapshot,
        regime,
        stem,
    )
    assert not guards.evaluate(scored, stem, regime).passed
    assert guards.evaluate(scored, stem.with_relaxation(3), regime).passed


def test_naked_short_is_refused_in_a_vol_shock(market, config):
    from nifty_research.schemas import Regime

    shock = Regime(
        label="vol_shock",
        trend_strength=0.0,
        realized_vol=0.3,
        implied_vol=0.25,
        iv_rv_edge=-0.05,
        pcr=1.2,
        vix=25.0,
    )
    hyp = Hypothesis(name="t", families=["short_straddle"], sigma_multiple=1.0, wing_width_steps=0)
    cand = build_candidates(market.snapshot, shock, config, hyp)[0][0]
    scored = score_candidate(cand, market.snapshot, shock, RunConfig(provider="fixture", min_pop=0.0))
    result = guards.evaluate(scored, RunConfig(provider="fixture", min_pop=0.0), shock)
    assert "NAKED_SHORT_IN_VOL_SHOCK" in _codes(result)


def test_report_histogram_counts_codes(market, regime, config):
    _, _, report = guards.evaluate_all(
        [
            score_candidate(
                build_candidates(
                    market.snapshot, regime, config, Hypothesis(name="t", families=["iron_condor"], wing_width_steps=2)
                )[0][0],
                market.snapshot,
                regime,
                config,
            )
        ],
        _clone(config, min_leg_oi=10**9),
        regime,
    )
    hist = guards.violation_histogram(report)
    assert hist.get("LIQUIDITY_THIN", 0) == 1
