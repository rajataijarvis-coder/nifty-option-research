"""Measured evidence: turning backtested outcomes into a (capped, shrunk) scoring input.

This is the piece that makes "optimised" mean something. The scorecard in
``analytics/scoring.py`` ranks structures by a *model's* view. Here, past realised
outcomes — per regime, per family — adjust that ranking, with three deliberate safety
rails so a backtest cannot quietly become a curve-fit:

1. **Sample gate**: a cell only speaks if it has at least ``min_trades`` observations.
2. **Shrinkage**: the observed edge is multiplied by ``shrink`` (default 0.3), so a
   family that historically kept 40% of its credit nudges the score up ~12%, not 40%.
3. **Cap**: the adjustment is capped at ±``cap`` × shrink (default ±15%) regardless of how
   spectacular the sample looks.

Every applied adjustment is printed in the note, with the sample size behind it, so a
human can see exactly how much weight the evidence was given.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, Field

from ..schemas import Regime, ScoredCandidate
from .engine import BacktestResult
from .metrics import by_regime_family

DEFAULT_SHRINK = 0.3
DEFAULT_CAP = 0.5


class EvidenceCell(BaseModel):
    regime: str
    family: str
    trades: int
    mean_pnl: float
    win_rate: float
    credit_kept: float
    adjustment: float
    sufficient: bool


class Evidence(BaseModel):
    built_at: datetime
    source: str
    history_is_real: bool
    min_trades: int
    shrink: float = DEFAULT_SHRINK
    cap: float = DEFAULT_CAP
    cells: dict[str, EvidenceCell] = Field(default_factory=dict)

    @staticmethod
    def key(regime: str, family: str) -> str:
        return f"{regime}|{family}"

    def lookup(self, regime: str, family: str) -> EvidenceCell | None:
        return self.cells.get(f"{regime}|{family}")

    def write(self, path: Path | str) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.model_dump_json(indent=2))
        return path

    @classmethod
    def read(cls, path: Path | str) -> "Evidence":
        return cls(**json.loads(Path(path).read_text()))

    def summary_lines(self) -> list[str]:
        lines = [
            f"evidence source: {self.source} ({'real' if self.history_is_real else 'SYNTHETIC'}), "
            f"shrink {self.shrink:g}, cap ±{self.cap * self.shrink:.0%}, min trades {self.min_trades}"
        ]
        for cell in sorted(self.cells.values(), key=lambda c: -c.trades):
            if not cell.sufficient:
                continue
            lines.append(
                f"  {cell.regime}/{cell.family}: {cell.trades} trades, kept {cell.credit_kept:+.0%} of "
                f"credit -> score adjustment {cell.adjustment:+.1%}"
            )
        return lines


def adjustment_from_edge(
    credit_kept: float,
    shrink: float = DEFAULT_SHRINK,
    cap: float = DEFAULT_CAP,
    sufficient: bool = True,
) -> float:
    """The shrink-and-cap policy in one place: capped edge x shrink, zero if unsupported."""
    if not sufficient:
        return 0.0
    return shrink * max(-cap, min(cap, credit_kept))


def build_evidence(
    result: BacktestResult,
    min_trades: int = 20,
    shrink: float = DEFAULT_SHRINK,
    cap: float = DEFAULT_CAP,
    source: str | None = None,
) -> Evidence:
    cells: dict[str, EvidenceCell] = {}
    for stats in by_regime_family(result, min_trades=min_trades):
        regime, family = stats.key
        cells[f"{regime}|{family}"] = EvidenceCell(
            regime=regime,
            family=family,
            trades=stats.trades,
            mean_pnl=round(stats.mean_pnl, 2),
            win_rate=round(stats.win_rate, 4),
            credit_kept=round(stats.credit_kept, 4),
            adjustment=round(
                adjustment_from_edge(stats.credit_kept, shrink, cap, sufficient=not stats.insufficient), 4
            ),
            sufficient=not stats.insufficient,
        )
    return Evidence(
        built_at=datetime.now(),
        source=source or f"backtest:{result.history_source}",
        history_is_real=result.history_is_real,
        min_trades=min_trades,
        shrink=shrink,
        cap=cap,
        cells=cells,
    )


def adjustment_for(evidence: Evidence | None, regime: str, family: str) -> tuple[float, int]:
    """``(score_multiplier_delta, sample_size)``; ``(0, 0)`` when there is no usable evidence."""
    if evidence is None:
        return 0.0, 0
    cell = evidence.lookup(regime, family)
    if cell is None or not cell.sufficient:
        return 0.0, cell.trades if cell else 0
    return cell.adjustment, cell.trades


def apply_to_scored(
    scored: ScoredCandidate, regime: Regime, evidence: Evidence | None
) -> ScoredCandidate:
    adjustment, trades = adjustment_for(evidence, regime.label, scored.candidate.family)
    if adjustment == 0.0 and trades == 0:
        return scored
    updated = scored.model_copy(deep=True)
    updated.score = round(scored.score * (1.0 + adjustment), 2)
    updated.evidence_adjustment = round(adjustment, 4)
    updated.evidence_trades = trades
    return updated


def apply_to_all(
    scored: list[ScoredCandidate], regime: Regime, evidence: Evidence | None
) -> list[ScoredCandidate]:
    return [apply_to_scored(s, regime, evidence) for s in scored]


def load_optional(path: Path | str | None) -> Evidence | None:
    if not path:
        return None
    path = Path(path)
    if not path.exists():
        return None
    try:
        return Evidence.read(path)
    except Exception:  # noqa: BLE001 - a corrupt evidence file must not stop research
        return None
