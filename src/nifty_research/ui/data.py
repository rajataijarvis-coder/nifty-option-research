"""UI data layer: turn the agent's outputs into tables a human can read.

Kept separate from the Streamlit app on purpose — these functions are plain Python and
unit-tested, so the numbers on the screen are the same numbers the CLI prints.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from ..backtest.engine import BacktestResult
from ..backtest.evidence import Evidence
from ..backtest.ledger import LedgerEntry
from ..backtest.metrics import by_family, by_regime, by_regime_family, headline, top_pick_series

LOG_COLUMNS = [
    "ts",
    "run_id",
    "regime",
    "family",
    "name",
    "hypothesis",
    "accepted",
    "score",
    "pop",
    "credit",
    "max_loss",
    "ev_per_lot",
    "cvar_5",
    "iv_edge",
    "undefined_risk",
]


def log_frame(log_path: Path | str) -> pd.DataFrame:
    """Research log as a DataFrame; empty (with columns) when there is nothing yet."""
    path = Path(log_path)
    if not path.exists():
        return pd.DataFrame(columns=LOG_COLUMNS)
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    if not rows:
        return pd.DataFrame(columns=LOG_COLUMNS)
    frame = pd.DataFrame(rows)
    for column in LOG_COLUMNS:
        if column not in frame.columns:
            frame[column] = None
    frame["accepted"] = frame["accepted"].fillna(False).astype(bool)
    return frame


def trades_frame(result: BacktestResult) -> pd.DataFrame:
    if not result.trades:
        return pd.DataFrame()
    frame = pd.DataFrame([trade.model_dump() for trade in result.trades])
    return frame


def cells_frame(result: BacktestResult, min_trades: int = 20) -> pd.DataFrame:
    rows = []
    for cell in by_regime_family(result, min_trades=min_trades):
        rows.append(
            {
                "regime": cell.key[0],
                "family": cell.key[1],
                "trades": cell.trades,
                "win_rate": round(cell.win_rate, 3),
                "mean_pnl": round(cell.mean_pnl, 2),
                "total_pnl": round(cell.total_pnl, 2),
                "credit_kept": round(cell.credit_kept, 3),
                "mean_model_ev": round(cell.mean_model_ev, 2),
                "calibration": round(cell.calibration, 3) if cell.calibration is not None else None,
                "worst_trade": round(cell.worst_trade, 2),
                "cvar_5": round(cell.cvar_5, 2),
                "sample": "insufficient" if cell.insufficient else "ok",
            }
        )
    return pd.DataFrame(rows)


def family_frame(result: BacktestResult, min_trades: int = 20) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "family": cell.key[1],
                "trades": cell.trades,
                "win_rate": round(cell.win_rate, 3),
                "mean_pnl": round(cell.mean_pnl, 2),
                "total_pnl": round(cell.total_pnl, 2),
                "credit_kept": round(cell.credit_kept, 3),
                "calibration": round(cell.calibration, 3) if cell.calibration is not None else None,
                "sample": "insufficient" if cell.insufficient else "ok",
            }
            for cell in by_family(result, min_trades=min_trades)
        ]
    )


def regime_frame(result: BacktestResult, min_trades: int = 20) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "regime": cell.key[0],
                "trades": cell.trades,
                "win_rate": round(cell.win_rate, 3),
                "mean_pnl": round(cell.mean_pnl, 2),
                "credit_kept": round(cell.credit_kept, 3),
                "sample": "insufficient" if cell.insufficient else "ok",
            }
            for cell in by_regime(result, min_trades=min_trades)
        ]
    )


def equity_frame(result: BacktestResult) -> pd.DataFrame:
    """Cumulative P&L of the agent's weekly top pick vs selling every candidate equally."""
    top = pd.DataFrame(top_pick_series(result))
    if top.empty:
        return pd.DataFrame(columns=["cycle", "top_pick_cumulative"])
    frame = top[["cycle", "cumulative_pnl", "regime", "family", "realised_pnl"]].rename(
        columns={
            "cumulative_pnl": "top_pick_cumulative",
            "realised_pnl": "top_pick_weekly",
        }
    )
    baseline_cumulative = 0.0
    per_cycle_baseline: dict[int, float] = {}
    trades = trades_frame(result)
    if not trades.empty:
        grouped = trades.groupby("cycle")["realised_pnl"].mean()
        for cycle in frame["cycle"]:
            baseline_cumulative += float(grouped.get(cycle, 0.0))
            per_cycle_baseline[int(cycle)] = round(baseline_cumulative, 2)
    frame["all_candidates_cumulative"] = frame["cycle"].map(per_cycle_baseline)
    return frame


def headline_frame(result: BacktestResult) -> pd.DataFrame:
    head = headline(result)
    return pd.DataFrame([{"metric": key, "value": value} for key, value in head.items()])


def evidence_frame(evidence: Evidence | None) -> pd.DataFrame:
    if evidence is None:
        return pd.DataFrame(
            columns=["regime", "family", "trades", "credit_kept", "adjustment", "sample"]
        )
    return pd.DataFrame(
        [
            {
                "regime": cell.regime,
                "family": cell.family,
                "trades": cell.trades,
                "mean_pnl": round(cell.mean_pnl, 2),
                "win_rate": round(cell.win_rate, 3),
                "credit_kept": round(cell.credit_kept, 3),
                "score_adjustment": round(cell.adjustment, 4),
                "sample": "ok" if cell.sufficient else "insufficient",
            }
            for cell in sorted(evidence.cells.values(), key=lambda c: (c.regime, c.family))
        ]
    )


def ledger_frame(entries: list[LedgerEntry]) -> pd.DataFrame:
    if not entries:
        return pd.DataFrame()
    return pd.DataFrame(
        [
            {
                "run_id": e.run_id,
                "name": e.name,
                "family": e.family,
                "regime": e.regime,
                "expiry_day": e.expiry_day,
                "spot_settle": e.spot_settle,
                "credit": e.credit,
                "realised_pnl": e.realised_pnl,
                "model_ev": e.model_ev,
                "model_valued_legs": e.model_valued_legs,
                "history_source": e.history_source,
            }
            for e in entries
        ]
    )


def note_files(out_dir: Path | str) -> list[Path]:
    out_dir = Path(out_dir)
    if not out_dir.exists():
        return []
    return sorted(out_dir.glob("run-*.md"), reverse=True)


def read_text(path: Path | str) -> str:
    path = Path(path)
    return path.read_text() if path.exists() else ""
