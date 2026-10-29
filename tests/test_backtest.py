"""Backtest-engine tests: the calendar, the no-lookahead guarantee, and the arithmetic."""

from __future__ import annotations

from datetime import date, timedelta

from nifty_research.backtest.engine import BacktestConfig, TradeRecord, run_backtest
from nifty_research.backtest.history import (
    IndexHistory,
    entries_for_expiries,
    from_csv,
    from_synthetic,
    weekly_expiries,
)
from nifty_research.backtest.metrics import (
    all_candidates_series,
    by_family,
    headline,
    max_drawdown,
    top_pick_series,
)


def test_synthetic_history_is_deterministic():
    a = from_synthetic(days=300, seed=5)
    b = from_synthetic(days=300, seed=5)
    assert a.closes == b.closes
    assert a.source == "synthetic:gbm" and not a.is_real
    assert from_synthetic(days=300, seed=6).closes != a.closes


def test_weekly_expiries_are_one_per_iso_week_and_sorted():
    history = from_synthetic(days=200, seed=3)
    expiries = weekly_expiries(history.days)
    weeks = [d.isocalendar()[:2] for d in expiries]
    assert len(weeks) == len(set(weeks))
    assert expiries == sorted(expiries)
    assert all(d in set(history.days) for d in expiries)
    assert all(d.weekday() < 5 for d in expiries)


def test_entries_sit_the_configured_offset_before_expiry():
    history = from_synthetic(days=400, seed=7)
    rolls = entries_for_expiries(history, entry_offset_trading_days=3)
    assert rolls
    for entry_idx, expiry_idx, expiry_day in rolls:
        assert expiry_idx - entry_idx == 3
        assert history.days[expiry_idx] == expiry_day
        assert entry_idx >= 60  # enough history for the 20/50-day trend read


def test_backtest_produces_settled_trades_with_sane_numbers():
    history = from_synthetic(days=600, seed=11)
    result = run_backtest(BacktestConfig(max_cycles=30, min_trades=5), history=history)
    assert result.trades
    assert result.cycles_evaluated == 30
    for trade in result.trades:
        assert trade.credit != 0
        assert trade.expiry_day > trade.entry_day
        assert trade.dte >= 1
        assert 0.0 <= trade.pop <= 1.0
        assert trade.mae <= trade.mfe
        assert trade.family in {
            "short_straddle",
            "short_strangle",
            "iron_condor",
            "iron_fly",
            "put_credit_spread",
            "call_credit_spread",
            "calendar",
        }
    assert not result.history_is_real
    assert any("SYNTHETIC" in w for w in result.warnings)


def test_backtest_does_not_look_ahead():
    """Corrupting the future must not change any decision made at entry."""
    history = from_synthetic(days=600, seed=13)
    config = BacktestConfig(max_cycles=25, min_trades=5)
    baseline = run_backtest(config, history=history)

    corrupted = IndexHistory(
        days=list(history.days),
        closes=list(history.closes),
        vix=dict(history.vix),
        source=history.source,
    )
    first_expiry_idx = max(
        i for i, day in enumerate(history.days) if day <= baseline.trades[0].expiry_day
    )
    for i in range(first_expiry_idx + 1, len(corrupted.closes)):
        corrupted.closes[i] *= 1.9  # a 90% crash after the first cycle

    after = run_backtest(config, history=corrupted)
    by_cycle = {(t.cycle, t.name): t for t in after.trades}
    for trade in baseline.trades:
        if trade.expiry_day <= baseline.trades[0].expiry_day:
            other = by_cycle[(trade.cycle, trade.name)]
            for field in ("regime", "family", "credit", "pop", "model_ev", "score", "spot_entry"):
                assert getattr(other, field) == getattr(trade, field), f"{field} changed on future data"


def test_same_seed_gives_the_same_backtest():
    history = from_synthetic(days=500, seed=19)
    config = BacktestConfig(max_cycles=15, min_trades=3)
    a = run_backtest(config, history=history)
    b = run_backtest(config, history=history)
    assert [t.model_dump() for t in a.trades][:5] == [t.model_dump() for t in b.trades][:5]


def test_metrics_are_consistent_with_the_trades():
    history = from_synthetic(days=600, seed=23)
    result = run_backtest(BacktestConfig(max_cycles=40, min_trades=5), history=history)
    head = headline(result)
    top = top_pick_series(result)
    assert len(top) == result.cycles_evaluated
    assert head["top_pick_total_pnl"] == round(sum(t["realised_pnl"] for t in top), 2)
    assert 0.0 <= head["top_pick_win_rate"] <= 1.0
    assert head["top_pick_max_drawdown"] <= 0
    assert len(all_candidates_series(result)) == result.cycles_evaluated
    assert head["trades_evaluated"] == len(result.trades)


def test_calibration_is_exact_when_model_equals_reality():
    from nifty_research.backtest.metrics import cell_stats

    def trade(pnl: float, ev: float, credit: float) -> TradeRecord:
        return TradeRecord(
            cycle=1,
            entry_day=date(2026, 1, 1),
            expiry_day=date(2026, 1, 5),
            dte=4,
            regime="range_low_vol",
            trend_strength=0.0,
            spot_entry=25_000.0,
            spot_expiry=25_000.0,
            iv_used=0.13,
            iv_source="vix",
            family="iron_condor",
            name="x",
            hypothesis="h",
            credit=credit,
            max_loss=1000.0,
            pop=0.7,
            model_ev=ev,
            score=50.0,
            undefined_risk=False,
            realised_pnl=pnl,
            mae=-100.0,
            mfe=200.0,
            expiry_move_pct=0.0,
        )

    perfect = cell_stats(("r", "f"), [trade(100.0, 100.0, 400.0)] * 5, min_trades=5)
    assert perfect.calibration == 1.0
    assert perfect.credit_kept == 0.25
    assert perfect.insufficient is False
    optimistic = cell_stats(("r", "f"), [trade(50.0, 100.0, 400.0)] * 5, min_trades=5)
    assert optimistic.calibration == 0.5  # model is 2x optimistic
    assert cell_stats(("r", "f"), [trade(50.0, 100.0, 400.0)] * 2, min_trades=5).insufficient is True


def test_max_drawdown_tracks_the_worst_peak_to_trough():
    assert max_drawdown([100.0, -50.0, -80.0, 200.0]) == -130.0
    assert max_drawdown([10.0, 10.0]) == 0.0


def test_csv_history_loads_and_is_marked_real(tmp_path):
    path = tmp_path / "nifty_daily.csv"
    start = date(2026, 1, 1)
    rows = ["date,close,vix"]
    for i in range(200):
        day = start + timedelta(days=i)
        rows.append(f"{day.isoformat()},{25000 + i * 3},{13.5 + (i % 5) * 0.1}")
    path.write_text("\n".join(rows))

    history = from_csv(path)
    assert history.is_real
    assert history.source == "csv:nifty_daily.csv"
    assert len(history) == 200
    assert history.vix[date(2026, 1, 5)] > 13.0
    assert len(weekly_expiries(history.days)) > 20


def test_csv_history_tolerates_messy_headers(tmp_path):
    path = tmp_path / "vendor-export.csv"
    path.write_text("Date,Adj Close,India VIX\n2026-01-01,25000.5,13.2\n2026-01-02,25050.0,13.4\n")
    history = from_csv(path)
    assert history.closes == [25_000.5, 25_050.0]
    assert history.vix[date(2026, 1, 2)] == 13.4


def test_families_can_be_pinned_instead_of_regime_selected():
    history = from_synthetic(days=500, seed=29)
    result = run_backtest(
        BacktestConfig(families=["iron_condor"], max_cycles=10, min_trades=2), history=history
    )
    assert {t.family for t in result.trades} == {"iron_condor"}
    assert all(t.hypothesis == "backtest_families" for t in result.trades)


def test_family_aggregation_matches_trade_count():
    history = from_synthetic(days=600, seed=31)
    result = run_backtest(BacktestConfig(max_cycles=30, min_trades=5), history=history)
    assert sum(cell.trades for cell in by_family(result)) == len(result.trades)


def test_short_history_is_refused():
    history = from_synthetic(days=50, seed=1)
    try:
        run_backtest(BacktestConfig(), history=history)
    except ValueError as exc:
        assert "120" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected a refusal for insufficient history")


def test_load_dispatches_sources_without_leaking_kwargs():
    """The CLI passes the same kwargs to every source; only yahoo understands range_."""
    from nifty_research.backtest.history import load

    assert load("synthetic", range_="1y").source == "synthetic:gbm"
    auto = load("auto", range_="1y")  # yahoo is rate-limited here, so this must still work
    assert len(auto) > 120
    try:
        load("csv")
    except ValueError as exc:
        assert "csv:<path>" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected csv without a path to be refused")
