"""Evidence and ledger tests: the measured loop must stay honest and stay idempotent."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta

import pytest

from nifty_research.backtest.engine import BacktestConfig, run_backtest
from nifty_research.backtest.evidence import (
    EvidenceCell,
    Evidence,
    adjustment_for,
    apply_to_all,
    apply_to_scored,
    build_evidence,
    load_optional,
)
from nifty_research.backtest.history import from_csv, from_synthetic
from nifty_research.backtest.ledger import load_ledger, settle, track_record
from nifty_research.analytics.scoring import score_candidate
from nifty_research.analytics.strategies import build_candidates
from nifty_research.schemas import Hypothesis, Regime


def _result(max_cycles: int = 60, min_trades: int = 5):
    history = from_synthetic(days=900, seed=41)
    return run_backtest(BacktestConfig(max_cycles=max_cycles, min_trades=min_trades), history=history)


def test_evidence_is_gated_on_sample_size():
    result = _result()
    strict = build_evidence(result, min_trades=10_000)
    assert strict.cells
    assert all(not cell.sufficient for cell in strict.cells.values())
    assert all(cell.adjustment == 0.0 for cell in strict.cells.values())
    assert adjustment_for(strict, "range_low_vol", "iron_condor") == (0.0, 0)


def test_evidence_shrinks_and_caps_the_observed_edge():
    from datetime import datetime as _dt

    from nifty_research.backtest.evidence import adjustment_from_edge

    # cap 0.5 x shrink 0.3 = 15% ceiling, no matter how spectacular the sample looks
    assert adjustment_from_edge(credit_kept=2.0, shrink=0.3, cap=0.5) == 0.15
    assert adjustment_from_edge(credit_kept=-2.0, shrink=0.3, cap=0.5) == -0.15
    assert adjustment_from_edge(credit_kept=2.0, shrink=0.3, cap=0.5, sufficient=False) == 0.0
    assert adjustment_from_edge(credit_kept=0.4, shrink=0.3, cap=0.5) == pytest.approx(0.12)

    cell = EvidenceCell(
        regime="range_high_vol",
        family="iron_condor",
        trades=50,
        mean_pnl=100.0,
        win_rate=0.7,
        credit_kept=2.0,
        adjustment=adjustment_from_edge(2.0),
        sufficient=True,
    )
    evidence = Evidence(
        built_at=_dt(2026, 1, 1),
        source="test",
        history_is_real=True,
        min_trades=5,
        cells={"range_high_vol|iron_condor": cell},
    )
    adjustment, trades = adjustment_for(evidence, "range_high_vol", "iron_condor")
    assert adjustment == 0.15
    assert trades == 50
    # and build_evidence applies the same policy end to end
    built = build_evidence(_result(), min_trades=5)
    for built_cell in built.cells.values():
        assert abs(built_cell.adjustment) <= built.shrink * built.cap


def test_negative_edge_is_penalised_symmetrically():
    cell = EvidenceCell(
        regime="trend_up",
        family="short_straddle",
        trades=40,
        mean_pnl=-200.0,
        win_rate=0.2,
        credit_kept=-1.0,
        adjustment=0.3 * -0.5,  # capped and shrunk
        sufficient=True,
    )
    evidence = Evidence(
        built_at=datetime(2026, 1, 1),
        source="test",
        history_is_real=True,
        min_trades=20,
        cells={"trend_up|short_straddle": cell},
    )
    adjustment, _ = adjustment_for(evidence, "trend_up", "short_straddle")
    assert adjustment == -0.15


def test_scored_candidate_is_adjusted_and_flagged(market, regime, config):
    hypothesis = Hypothesis(name="t", families=["iron_condor"], sigma_multiple=1.0, wing_width_steps=2)
    scored = score_candidate(
        build_candidates(market.snapshot, regime, config, hypothesis)[0][0],
        market.snapshot,
        regime,
        config,
    )
    evidence = Evidence(
        built_at=datetime(2026, 1, 1),
        source="test",
        history_is_real=True,
        min_trades=20,
        cells={
            f"{regime.label}|iron_condor": EvidenceCell(
                regime=regime.label,
                family="iron_condor",
                trades=30,
                mean_pnl=50.0,
                win_rate=0.7,
                credit_kept=0.4,
                adjustment=0.12,
                sufficient=True,
            )
        },
    )
    adjusted = apply_to_scored(scored, regime, evidence)
    assert adjusted.evidence_adjustment == 0.12
    assert adjusted.evidence_trades == 30
    assert adjusted.score == round(scored.score * 1.12, 2)
    assert scored.evidence_adjustment == 0.0  # the original is untouched

    untouched = apply_to_scored(scored, regime, None)
    assert untouched is scored
    assert apply_to_all([scored], regime, None)[0] is scored


def test_evidence_round_trips_and_survives_a_corrupt_file(tmp_path):
    result = _result(max_cycles=60, min_trades=5)
    evidence = build_evidence(result, min_trades=5)
    path = evidence.write(tmp_path / "evidence.json")
    reloaded = Evidence.read(path)
    assert reloaded.cells.keys() == evidence.cells.keys()
    assert load_optional(path) is not None

    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert load_optional(bad) is None
    assert load_optional(tmp_path / "missing.json") is None


def _write_history_and_log(tmp_path, settle_close: float = 25_100.0):
    start = date(2026, 1, 5)
    rows = ["date,close"]
    for i in range(120):
        day = start + timedelta(days=i)
        if i < 110:
            rows.append(f"{day.isoformat()},25000.0")
        else:
            rows.append(f"{day.isoformat()},{settle_close}")
    history_path = tmp_path / "nifty_daily.csv"
    history_path.write_text("\n".join(rows))

    expiry = start + timedelta(days=115)  # beyond the last bar, so the settle uses the last close
    record = {
        "ts": "2026-01-05T10:00:00",
        "run_id": "run-test",
        "provider": "fixture",
        "regime": "range_low_vol",
        "family": "short_straddle",
        "name": "short_straddle_S25000C-S25000P",
        "hypothesis": "baseline",
        "score": 55.0,
        "pop": 0.6,
        "credit": 15_000.0,
        "ev_per_lot": 500.0,
        "accepted": True,
        "legs": [
            {"action": "SELL", "option_type": "CE", "strike": 25_000, "expiry": expiry.isoformat(), "price": 100.0},
            {"action": "SELL", "option_type": "PE", "strike": 25_000, "expiry": expiry.isoformat(), "price": 100.0},
        ],
    }
    log_path = tmp_path / "research_log.jsonl"
    log_path.write_text(json.dumps(record) + "\n")
    return history_path, log_path, record, expiry


def test_settle_refuses_synthetic_history(tmp_path):
    history_path, log_path, _record, _expiry = _write_history_and_log(tmp_path)
    result = settle(
        log_path=log_path,
        history=from_synthetic(days=200, seed=1),
        ledger_path=tmp_path / "ledger.jsonl",
        allow_synthetic=False,
    )
    assert result["settled"] == 0
    assert "synthetic" in result["reason"]
    assert not (tmp_path / "ledger.jsonl").exists()


def test_settle_computes_realised_pnl_from_real_closes(tmp_path):
    history_path, log_path, _record, _expiry = _write_history_and_log(tmp_path, settle_close=25_100.0)
    result = settle(
        log_path=log_path,
        history=from_csv(history_path),
        ledger_path=tmp_path / "ledger.jsonl",
    )
    assert result["settled"] == 1
    entries = load_ledger(tmp_path / "ledger.jsonl")
    # short 25000 straddle for 200 points total; index settles 100 above the strike:
    # the CE pays 100, the PE expires worthless -> 100 points kept = 100 x 75
    assert entries[0].realised_pnl == 7_500.0
    assert entries[0].credit == 15_000.0
    assert entries[0].model_valued_legs is False
    assert entries[0].synthetic is False
    assert result["summary"]["settled_total"] == 1
    assert result["summary"]["per_family"]["short_straddle"]["credit_kept"] == 0.5


def test_settle_is_idempotent(tmp_path):
    history_path, log_path, _record, _expiry = _write_history_and_log(tmp_path)
    ledger = tmp_path / "ledger.jsonl"
    first = settle(log_path=log_path, history=from_csv(history_path), ledger_path=ledger)
    second = settle(log_path=log_path, history=from_csv(history_path), ledger_path=ledger)
    assert first["settled"] == 1
    assert second["settled"] == 0
    assert second["skipped"] == 1
    assert len(load_ledger(ledger)) == 1


def test_settle_can_include_rejected_and_flags_calendars(tmp_path):
    history_path, log_path, record, expiry = _write_history_and_log(tmp_path)
    record["accepted"] = False
    record["family"] = "calendar"
    record["legs"].append(
        {
            "action": "BUY",
            "option_type": "CE",
            "strike": 25_000,
            "expiry": (expiry + timedelta(days=7)).isoformat(),
            "price": 150.0,
        }
    )
    log_path.write_text(json.dumps(record) + "\n")
    ledger = tmp_path / "ledger.jsonl"

    assert settle(log_path=log_path, history=from_csv(history_path), ledger_path=ledger)["settled"] == 0
    included = settle(
        log_path=log_path,
        history=from_csv(history_path),
        ledger_path=ledger,
        only_accepted=False,
    )
    assert included["settled"] == 1
    entries = load_ledger(ledger)
    assert entries[0].model_valued_legs is True  # the back leg outlives the front expiry
    assert entries[0].family == "calendar"


def test_track_record_reports_calibration(tmp_path):
    history_path, log_path, _record, _expiry = _write_history_and_log(tmp_path)
    settle(log_path=log_path, history=from_csv(history_path), ledger_path=tmp_path / "ledger.jsonl")
    record = track_record(load_ledger(tmp_path / "ledger.jsonl"))
    assert record["settled_total"] == 1
    assert record["total_pnl"] == 7_500.0
    # model expected 500 per lot-set, realised 7,500 -> the model was pessimistic here
    assert record["per_family"]["short_straddle"]["calibration"] == 15.0
    assert record["any_synthetic"] is False


def test_ledger_skips_records_without_legs(tmp_path):
    log_path = tmp_path / "research_log.jsonl"
    log_path.write_text(json.dumps({"run_id": "r", "family": "iron_condor", "accepted": True}) + "\n")
    history = from_synthetic(days=200, seed=2)
    result = settle(
        log_path=log_path,
        history=history,
        ledger_path=tmp_path / "ledger.jsonl",
        allow_synthetic=True,
    )
    assert result["settled"] == 0
