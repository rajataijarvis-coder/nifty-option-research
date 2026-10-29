"""Backtest aggregation: per-regime, per-family measured performance.

Every cell carries its sample size, and `insufficient` is set when the cell is too thin to
say anything. Reporting a 100% win rate off three trades is how backtests lie.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import mean, median

from .engine import BacktestResult, TradeRecord


@dataclass
class CellStats:
    key: tuple[str, str]
    trades: int
    insufficient: bool
    win_rate: float
    mean_pnl: float
    median_pnl: float
    total_pnl: float
    std_pnl: float
    cvar_5: float
    mean_credit: float
    credit_kept: float  # realised P&L as a fraction of the credit collected
    mean_model_ev: float
    calibration: float | None  # realised / model-expected; 0.5 means the model is 2x optimistic
    mean_mae: float
    worst_trade: float


def _cvar(values: list[float], q: float = 0.05) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    k = max(1, int(q * len(ordered)))
    return mean(ordered[:k])


def cell_stats(key: tuple[str, str], trades: list[TradeRecord], min_trades: int) -> CellStats:
    pnls = [t.realised_pnl for t in trades]
    credits = [t.credit for t in trades]
    evs = [t.model_ev for t in trades]
    mean_ev = mean(evs) if evs else 0.0
    mean_pnl = mean(pnls) if pnls else 0.0
    mean_credit = mean(credits) if credits else 0.0
    calibration = None
    if abs(mean_ev) > 1e-9:
        calibration = mean_pnl / mean_ev
    return CellStats(
        key=key,
        trades=len(trades),
        insufficient=len(trades) < min_trades,
        win_rate=(sum(1 for p in pnls if p > 0) / len(pnls)) if pnls else 0.0,
        mean_pnl=mean_pnl,
        median_pnl=median(pnls) if pnls else 0.0,
        total_pnl=sum(pnls),
        std_pnl=(math.sqrt(sum((p - mean_pnl) ** 2 for p in pnls) / max(len(pnls) - 1, 1)) if len(pnls) > 1 else 0.0),
        cvar_5=_cvar(pnls),
        mean_credit=mean_credit,
        credit_kept=(mean_pnl / mean_credit) if abs(mean_credit) > 1e-9 else 0.0,
        mean_model_ev=mean_ev,
        calibration=calibration,
        mean_mae=mean([t.mae for t in trades]) if trades else 0.0,
        worst_trade=min(pnls) if pnls else 0.0,
    )


def by_regime_family(result: BacktestResult, min_trades: int = 20) -> list[CellStats]:
    buckets: dict[tuple[str, str], list[TradeRecord]] = {}
    for trade in result.trades:
        buckets.setdefault((trade.regime, trade.family), []).append(trade)
    cells = [cell_stats(key, rows, min_trades) for key, rows in buckets.items()]
    return sorted(cells, key=lambda c: (-c.trades, c.key))


def by_family(result: BacktestResult, min_trades: int = 20) -> list[CellStats]:
    buckets: dict[tuple[str, str], list[TradeRecord]] = {}
    for trade in result.trades:
        buckets.setdefault(("*", trade.family), []).append(trade)
    return sorted(
        (cell_stats(key, rows, min_trades) for key, rows in buckets.items()),
        key=lambda c: -c.mean_pnl,
    )


def by_regime(result: BacktestResult, min_trades: int = 20) -> list[CellStats]:
    buckets: dict[tuple[str, str], list[TradeRecord]] = {}
    for trade in result.trades:
        buckets.setdefault((trade.regime, "*"), []).append(trade)
    return sorted(
        (cell_stats(key, rows, min_trades) for key, rows in buckets.items()),
        key=lambda c: c.key[0],
    )


def top_pick_series(result: BacktestResult) -> list[dict]:
    """One trade per cycle: the agent's highest-scoring structure — its actual track record."""
    best: dict[int, TradeRecord] = {}
    for trade in result.trades:
        current = best.get(trade.cycle)
        if current is None or trade.score > current.score:
            best[trade.cycle] = trade
    out: list[dict] = []
    cumulative = 0.0
    for cycle in sorted(best):
        trade = best[cycle]
        cumulative += trade.realised_pnl
        out.append(
            {
                "cycle": cycle,
                "entry_day": trade.entry_day,
                "expiry_day": trade.expiry_day,
                "regime": trade.regime,
                "name": trade.name,
                "family": trade.family,
                "realised_pnl": trade.realised_pnl,
                "cumulative_pnl": round(cumulative, 2),
                "model_ev": trade.model_ev,
                "mae": trade.mae,
            }
        )
    return out


def all_candidates_series(result: BacktestResult) -> list[dict]:
    """Equal-weight-every-candidate baseline: what 'just sell everything' would have done."""
    per_cycle: dict[int, list[TradeRecord]] = {}
    for trade in result.trades:
        per_cycle.setdefault(trade.cycle, []).append(trade)
    out: list[dict] = []
    cumulative = 0.0
    for cycle in sorted(per_cycle):
        rows = per_cycle[cycle]
        pnl = mean([t.realised_pnl for t in rows])
        cumulative += pnl
        out.append({"cycle": cycle, "per_candidate_pnl": round(pnl, 2), "cumulative_pnl": round(cumulative, 2)})
    return out


def max_drawdown(series: list[float]) -> float:
    peak = 0.0
    worst = 0.0
    cumulative = 0.0
    for step in series:
        cumulative += step
        peak = max(peak, cumulative)
        worst = min(worst, cumulative - peak)
    return round(worst, 2)


def headline(result: BacktestResult, min_trades: int = 20) -> dict:
    top = top_pick_series(result)
    every = all_candidates_series(result)
    top_pnls = [t["realised_pnl"] for t in top]
    cycles = len(top)
    mean_pnl = mean(top_pnls) if top_pnls else 0.0
    std_pnl = (
        math.sqrt(sum((p - mean_pnl) ** 2 for p in top_pnls) / max(len(top_pnls) - 1, 1))
        if len(top_pnls) > 1
        else 0.0
    )
    model_ev_total = sum(t["model_ev"] for t in top)
    realised_total = sum(top_pnls)
    return {
        "history_source": result.history_source,
        "history_is_real": result.history_is_real,
        "cycles": cycles,
        "trades_evaluated": len(result.trades),
        "top_pick_total_pnl": round(realised_total, 2),
        "top_pick_mean_pnl": round(mean_pnl, 2),
        "top_pick_win_rate": round(sum(1 for p in top_pnls if p > 0) / cycles, 4) if cycles else 0.0,
        "top_pick_cvar_5": round(_cvar(top_pnls), 2),
        "top_pick_max_drawdown": max_drawdown([t["realised_pnl"] for t in top]),
        "top_pick_sharpe_like": round((mean_pnl / std_pnl) * math.sqrt(52), 2) if std_pnl > 0 else None,
        "top_pick_worst_trade": round(min(top_pnls), 2) if top_pnls else 0.0,
        "model_expected_total": round(model_ev_total, 2),
        "calibration": round(realised_total / model_ev_total, 3) if abs(model_ev_total) > 1e-9 else None,
        "all_candidates_total_pnl": round(every[-1]["cumulative_pnl"], 2) if every else 0.0,
        "min_trades_for_confidence": min_trades,
    }


def calibration_verdict(result: BacktestResult) -> str:
    """Plain-language read on whether the model's expected value is trustworthy."""
    head = headline(result)
    ratio = head["calibration"]
    if ratio is None:
        return "model expected value is ~0 across cycles; calibration undefined"
    if ratio < 0:
        return "model is systematically the wrong sign: expected gains realised as losses"
    if ratio < 0.5:
        return f"model is optimistic: realised only {ratio:.0%} of expected value"
    if ratio > 1.5:
        return f"model is conservative: realised {ratio:.0%} of expected value"
    return f"model is calibrated: realised {ratio:.0%} of expected value"
