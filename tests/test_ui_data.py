"""UI data-layer tests.

The dashboard itself is verified by launching it (see `scripts/verify_ui.sh`), but
everything it displays comes from these functions, so those are tested properly here.
"""

from __future__ import annotations

import json
from datetime import date

import pytest

pd = pytest.importorskip("pandas")

from nifty_research.backtest.engine import BacktestConfig, run_backtest
from nifty_research.backtest.evidence import build_evidence
from nifty_research.backtest.history import from_synthetic
from nifty_research.backtest.ledger import LedgerEntry, load_ledger
from nifty_research.ui import data as ui


@pytest.fixture(scope="module")
def backtest_result():
    history = from_synthetic(days=600, seed=53)
    return run_backtest(BacktestConfig(max_cycles=25, min_trades=3), history=history)


def test_log_frame_handles_missing_and_populated_logs(tmp_path):
    empty = ui.log_frame(tmp_path / "nothing.jsonl")
    assert empty.empty
    assert "regime" in empty.columns

    log = tmp_path / "log.jsonl"
    log.write_text(
        json.dumps(
            {
                "ts": "2026-01-01T10:00:00",
                "run_id": "r1",
                "regime": "trend_up",
                "family": "iron_condor",
                "name": "iron_condor_x",
                "hypothesis": "baseline",
                "accepted": True,
                "score": 50.0,
                "pop": 0.7,
                "credit": 1000.0,
                "max_loss": 500.0,
                "ev_per_lot": 20.0,
                "cvar_5": -400.0,
                "iv_edge": 0.02,
                "undefined_risk": False,
            }
        )
        + "\n"
    )
    frame = ui.log_frame(log)
    assert len(frame) == 1
    assert bool(frame.loc[0, "accepted"]) is True
    assert frame.loc[0, "family"] == "iron_condor"


def test_cells_and_family_frames_match_headline(backtest_result):
    cells = ui.cells_frame(backtest_result, min_trades=3)
    assert set(cells.columns) >= {"regime", "family", "trades", "win_rate", "credit_kept", "sample"}
    assert cells["trades"].sum() == len(backtest_result.trades)

    families = ui.family_frame(backtest_result, min_trades=3)
    assert families["trades"].sum() == len(backtest_result.trades)

    regimes = ui.regime_frame(backtest_result, min_trades=3)
    assert regimes["trades"].sum() == len(backtest_result.trades)


def test_equity_frame_is_cumulative_and_one_row_per_cycle(backtest_result):
    equity = ui.equity_frame(backtest_result)
    assert len(equity) == backtest_result.cycles_evaluated
    assert equity["cycle"].is_monotonic_increasing
    expected_last = round(equity["top_pick_weekly"].sum(), 2)
    assert equity["top_pick_cumulative"].iloc[-1] == pytest.approx(expected_last, abs=1.0)
    assert "all_candidates_cumulative" in equity.columns


def test_trades_frame_has_a_row_per_trade(backtest_result):
    trades = ui.trades_frame(backtest_result)
    assert len(trades) == len(backtest_result.trades)
    assert {"family", "realised_pnl", "model_ev", "regime", "mae"} <= set(trades.columns)


def test_headline_frame_exposes_the_metrics_shown_on_screen(backtest_result):
    frame = ui.headline_frame(backtest_result)
    metrics = set(frame["metric"])
    assert {"top_pick_total_pnl", "top_pick_win_rate", "calibration", "cycles"} <= metrics


def test_evidence_frame_includes_the_adjustment_column(backtest_result):
    evidence = build_evidence(backtest_result, min_trades=3)
    frame = ui.evidence_frame(evidence)
    assert "score_adjustment" in frame.columns
    assert len(frame) == len(evidence.cells)
    assert ui.evidence_frame(None).empty


def test_ledger_frame_and_note_files(tmp_path):
    assert ui.ledger_frame([]).empty
    entry = LedgerEntry(
        run_id="r1",
        name="n",
        family="iron_condor",
        regime="trend_up",
        expiry_day=date(2026, 1, 8),
        settled_day=date(2026, 1, 8),
        spot_settle=25_100.0,
        credit=1000.0,
        model_ev=50.0,
        pop=0.7,
        score=51.0,
        realised_pnl=-250.0,
        history_source="csv:nifty_daily.csv",
    )
    frame = ui.ledger_frame([entry])
    assert frame.loc[0, "realised_pnl"] == -250.0
    assert frame.loc[0, "family"] == "iron_condor"

    out = tmp_path / "out"
    out.mkdir()
    (out / "run-20260101T000000Z-aaaa.md").write_text("# note")
    (out / "latest.md").write_text("# note")
    files = ui.note_files(out)
    assert [f.name for f in files] == ["run-20260101T000000Z-aaaa.md"]
    assert ui.read_text(out / "latest.md") == "# note"
    assert ui.read_text(out / "missing.md") == ""
