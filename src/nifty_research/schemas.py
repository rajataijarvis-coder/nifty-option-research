"""Typed contracts shared by every node in the graph."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

OptionType = Literal["CE", "PE"]
Action = Literal["SELL", "BUY"]
RegimeLabel = Literal[
    "trend_up",
    "trend_down",
    "range_low_vol",
    "range_high_vol",
    "vol_shock",
]
Family = Literal[
    "short_straddle",
    "short_strangle",
    "iron_condor",
    "iron_fly",
    "put_credit_spread",
    "call_credit_spread",
    "calendar",
]


# --------------------------------------------------------------------------- market data
class Bar(BaseModel):
    day: date
    close: float
    high: float | None = None
    low: float | None = None


class OptionQuote(BaseModel):
    strike: float
    option_type: OptionType
    expiry: date
    ltp: float
    iv: float | None = None  # annualised decimal, 0.14 == 14%
    oi: int = 0
    volume: int = 0


class ChainSnapshot(BaseModel):
    symbol: str
    spot: float
    as_of: datetime
    expiry: date
    expiries: list[date] = Field(default_factory=list)
    quotes: list[OptionQuote] = Field(default_factory=list)
    source: str = "unknown"
    stale: bool = False
    warnings: list[str] = Field(default_factory=list)

    def for_expiry(self, expiry: date) -> list[OptionQuote]:
        return [q for q in self.quotes if q.expiry == expiry]

    def strikes(self, expiry: date | None = None) -> list[float]:
        qs = self.for_expiry(expiry) if expiry else self.quotes
        return sorted({q.strike for q in qs})

    def atm_strike(self, step: int = 50, expiry: date | None = None) -> float:
        strikes = self.strikes(expiry)
        if not strikes:
            raise ValueError("chain has no strikes")
        return min(strikes, key=lambda k: (abs(k - self.spot), k))

    def quote(self, strike: float, option_type: OptionType, expiry: date | None = None):
        exp = expiry or self.expiry
        for q in self.quotes:
            if q.strike == strike and q.option_type == option_type and q.expiry == exp:
                return q
        return None

    def atm_iv(self, expiry: date | None = None) -> float | None:
        exp = expiry or self.expiry
        atm = self.atm_strike(expiry=exp)
        ivs = [
            q.iv
            for q in self.for_expiry(exp)
            if abs(q.strike - atm) < 1e-9 and q.iv is not None
        ]
        if not ivs:
            ivs = [q.iv for q in self.for_expiry(exp) if q.iv is not None]
        if not ivs:
            return None
        return sum(ivs) / len(ivs)

    def total_oi(self, option_type: OptionType, expiry: date | None = None) -> int:
        exp = expiry or self.expiry
        return sum(q.oi for q in self.for_expiry(exp) if q.option_type == option_type)


class MarketState(BaseModel):
    snapshot: ChainSnapshot
    history: list[Bar] = Field(default_factory=list)
    vix: float | None = None
    provider: str = "unknown"
    warnings: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- analysis
class Regime(BaseModel):
    label: RegimeLabel
    trend_strength: float
    realized_vol: float
    implied_vol: float
    iv_rv_edge: float
    pcr: float
    vix: float | None = None
    levels: dict[str, float] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)


class Hypothesis(BaseModel):
    """A testable idea for the current iteration: which structures to try, and why."""

    name: str
    families: list[Family]
    sigma_multiple: float = 1.0
    wing_width_steps: int = 2
    rationale: str = ""
    origin: Literal["rules", "llm"] = "rules"


# --------------------------------------------------------------------------- structures
class Leg(BaseModel):
    action: Action
    option_type: OptionType
    strike: float
    expiry: date
    qty: int = 1
    price: float
    iv: float | None = None
    oi: int = 0
    volume: int = 0

    @property
    def sign(self) -> int:
        return -1 if self.action == "SELL" else 1


class Candidate(BaseModel):
    name: str
    family: Family
    legs: list[Leg]
    rationale: str = ""
    params: dict[str, Any] = Field(default_factory=dict)
    hypothesis: str = ""

    def front_expiry(self) -> date:
        return min(leg.expiry for leg in self.legs)


class ScoreComponents(BaseModel):
    ev_per_lot: float
    ev_to_risk: float
    pop: float
    iv_edge: float
    liquidity: float
    rom: float


class ScoredCandidate(BaseModel):
    candidate: Candidate
    credit: float
    max_loss: float
    max_profit: float
    breakevens: list[float]
    pop: float
    cvar_5: float
    ev_per_lot: float
    margin_proxy: float
    undefined_risk: bool = False
    dte: int = 0
    rom: float
    liquidity: float
    iv_edge: float
    score: float
    components: ScoreComponents
    assumptions: dict[str, Any] = Field(default_factory=dict)


class GuardViolation(BaseModel):
    code: str
    detail: str


class GuardResult(BaseModel):
    passed: bool
    violations: list[GuardViolation] = Field(default_factory=list)


class Finding(BaseModel):
    """One iteration's headline: what was tested, what survived, what the model says."""

    iteration: int
    hypothesis: str
    regime: str
    evaluated: int
    accepted: int
    best: dict[str, Any] | None = None
    violations: dict[str, int] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)


class ResearchNote(BaseModel):
    run_id: str
    generated_at: datetime
    symbol: str
    provider: str
    regime: Regime
    assumptions: dict[str, Any]
    book_context: dict[str, Any] | None = None
    accepted: list[ScoredCandidate] = Field(default_factory=list)
    rejected: list[ScoredCandidate] = Field(default_factory=list)
    guard_report: dict[str, list[GuardViolation]] = Field(default_factory=dict)
    findings: list[Finding] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    markdown: str = ""
