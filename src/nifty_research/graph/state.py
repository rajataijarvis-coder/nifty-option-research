"""Graph state."""

from __future__ import annotations

import operator
from datetime import date
from typing import Annotated, Any, TypedDict

from ..config import RunConfig
from ..schemas import (
    Candidate,
    Finding,
    GuardViolation,
    Hypothesis,
    MarketState,
    Regime,
    ResearchNote,
    ScoredCandidate,
)


class ResearchState(TypedDict, total=False):
    # inputs
    config: RunConfig
    run_id: str

    # market picture
    market: MarketState
    regime: Regime
    expiry: date
    book_context: dict[str, Any] | None

    # iteration state
    iteration: int
    relax_level: int
    hypotheses: list[Hypothesis]
    candidates: list[Candidate]
    scored: list[ScoredCandidate]
    accepted: list[ScoredCandidate]
    rejected: list[ScoredCandidate]
    guard_report: dict[str, list[GuardViolation]]
    findings: list[Finding]
    # accumulated across cycles (populated by rank_and_publish, consumed by finalize)
    _all_accepted: list[ScoredCandidate]
    _all_rejected: list[ScoredCandidate]
    _all_guard_report: dict[str, list[GuardViolation]]

    # outputs — these two accumulate across the whole run
    warnings: Annotated[list[str], operator.add]
    errors: Annotated[list[str], operator.add]
    note: ResearchNote | None
    note_path: str
    status: str
