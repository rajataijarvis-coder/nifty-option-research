"""Walk-forward backtest engine.

What is real and what is modelled — this is the whole honesty contract of the backtest:

* **The index path is real** whenever the history source is real (CSV or Yahoo). Entry,
  expiry and every intermediate mark come from actual closes.
* **The entry premiums are modelled**: the chain the agent sees at entry is reconstructed
  from the index level and an IV estimate (VIX if the history carries it, otherwise
  realised vol × a variance-risk-premium factor). Real historical *option* chains are not
  freely available, so pretending otherwise would be the dishonest option.
* **The settlement is real**: at expiry every leg is marked to intrinsic against the
  actual index close. That means realised P&L is a genuine outcome of a modelled entry —
  which is exactly what you need to test whether the model's expected value means
  anything.

No lookahead anywhere: the regime is read from bars up to and including the entry day,
strikes are chosen from that day's reconstruction, and nothing after the entry day touches
the decision.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Callable

from pydantic import BaseModel, Field

from ..analytics import regime as regime_mod, scoring, strategies
from ..analytics.greeks import realized_vol
from ..config import RunConfig
from ..research.hypotheses import REGIME_FAMILIES, baseline_hypothesis
from ..schemas import Hypothesis
from ..providers.synthetic import build_snapshot
from .history import IndexHistory, entries_for_expiries


@dataclass
class BacktestConfig:
    entry_offset_trading_days: int = 3
    iv_premium: float = 1.15  # sold IV = realised vol x this, when VIX is unavailable
    families: list[str] | None = None  # None -> whatever the regime's baseline mix is
    sigma_multiple: float = 1.0
    wing_width_steps: int = 2
    max_cycles: int | None = None
    min_trades: int = 20  # below this a cell is reported as insufficient sample

    def run_config(self) -> RunConfig:
        return RunConfig(
            provider="fixture",
            lot_size=75,
            strike_step=50,
            risk_free_rate=0.065,
            sigma_multiple=self.sigma_multiple,
            wing_width_steps=self.wing_width_steps,
            target_dte_min=1,
            target_dte_max=15,
        )


class TradeRecord(BaseModel):
    cycle: int
    entry_day: date
    expiry_day: date
    dte: int
    regime: str
    trend_strength: float
    spot_entry: float
    spot_expiry: float
    iv_used: float
    iv_source: str
    family: str
    name: str
    hypothesis: str
    legs: list[dict] = Field(default_factory=list)
    credit: float
    max_loss: float
    pop: float
    model_ev: float
    score: float
    undefined_risk: bool
    realised_pnl: float
    mae: float  # worst mark-to-model P&L while the trade was open
    mfe: float  # best mark-to-model P&L while the trade was open
    expiry_move_pct: float  # index move over the cycle, in %


class BacktestResult(BaseModel):
    history_source: str
    history_is_real: bool
    cycles_evaluated: int
    trades: list[TradeRecord] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    entries: dict = Field(default_factory=dict)
    started_at: datetime
    finished_at: datetime | None = None

    @property
    def real_label(self) -> str:
        return "real index path" if self.history_is_real else "SYNTHETIC index path"


def run_backtest(
    config: BacktestConfig | None = None,
    history: IndexHistory | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> BacktestResult:
    config = config or BacktestConfig()
    history = history or IndexHistory([], [], source="empty")
    if len(history) < 120:
        raise ValueError(f"need at least 120 bars of history, got {len(history)}")

    run_config = config.run_config()
    rolls = entries_for_expiries(history, config.entry_offset_trading_days)
    if config.max_cycles:
        rolls = rolls[-config.max_cycles :]

    warnings = list(history.warnings)
    trades: list[TradeRecord] = []
    started = datetime.now()

    for cycle, (entry_idx, expiry_idx, expiry_day) in enumerate(rolls, start=1):
        if progress:
            progress(cycle, len(rolls))
        bars = history.bars(0, entry_idx)
        entry_day = history.days[entry_idx]
        spot_entry = history.closes[entry_idx]
        spot_expiry = history.closes[expiry_idx]
        dte = max((expiry_day - entry_day).days, 1)

        vix_value = history.vix_at(entry_day)
        if vix_value:
            iv = vix_value / 100.0
            iv_source = "vix"
        else:
            iv = max(realized_vol([b.close for b in bars], window=20) * config.iv_premium, 0.05)
            iv_source = f"realised_vol x {config.iv_premium:g}"

        snapshot = build_snapshot(
            spot=spot_entry,
            atm_iv=iv,
            as_of=datetime.combine(entry_day, datetime.min.time()).replace(hour=15, minute=30),
            strike_step=run_config.strike_step,
            rate=run_config.risk_free_rate,
            expiry_offsets=(dte,),
        )
        regime = regime_mod.analyze(snapshot, bars, vix=vix_value, expiry=expiry_day)

        if config.families:
            hypothesis = Hypothesis(
                name="backtest_families",
                families=list(config.families),  # type: ignore[arg-type]
                sigma_multiple=config.sigma_multiple,
                wing_width_steps=config.wing_width_steps,
            )
        else:
            hypothesis = baseline_hypothesis(regime, run_config)

        candidates, _skips = strategies.build_candidates(snapshot, regime, run_config, hypothesis)
        if not candidates:
            warnings.append(f"{entry_day.isoformat()}: no structure could be built ({hypothesis.name})")
            continue

        for candidate in candidates:
            scored = scoring.score_candidate(candidate, snapshot, regime, run_config)
            realised = scoring.structure_pnl(
                candidate, spot_expiry, expiry_day, run_config, iv, run_config.lot_size
            )
            marks = [
                scoring.structure_pnl(
                    candidate,
                    history.closes[i],
                    history.days[i],
                    run_config,
                    iv,
                    run_config.lot_size,
                )
                for i in range(entry_idx, expiry_idx + 1)
            ]
            trades.append(
                TradeRecord(
                    cycle=cycle,
                    entry_day=entry_day,
                    expiry_day=expiry_day,
                    dte=dte,
                    regime=regime.label,
                    trend_strength=regime.trend_strength,
                    spot_entry=round(spot_entry, 2),
                    spot_expiry=round(spot_expiry, 2),
                    iv_used=round(iv, 4),
                    iv_source=iv_source,
                    family=candidate.family,
                    name=candidate.name,
                    hypothesis=hypothesis.name,
                    legs=[
                        {
                            "action": leg.action,
                            "option_type": leg.option_type,
                            "strike": leg.strike,
                            "price": leg.price,
                        }
                        for leg in candidate.legs
                    ],
                    credit=round(scored.credit, 2),
                    max_loss=round(scored.max_loss, 2),
                    pop=scored.pop,
                    model_ev=round(scored.ev_per_lot, 2),
                    score=scored.score,
                    undefined_risk=scored.undefined_risk,
                    realised_pnl=round(realised, 2),
                    mae=round(min(marks), 2),
                    mfe=round(max(marks), 2),
                    expiry_move_pct=round((spot_expiry / spot_entry - 1) * 100, 2),
                )
            )

    result = BacktestResult(
        history_source=history.source,
        history_is_real=history.is_real,
        cycles_evaluated=len({t.cycle for t in trades}),
        trades=trades,
        warnings=warnings,
        entries={
            "entry_offset_trading_days": config.entry_offset_trading_days,
            "iv_premium": config.iv_premium,
            "families": config.families or "regime baseline mix",
            "sigma_multiple": config.sigma_multiple,
            "wing_width_steps": config.wing_width_steps,
            "lot_size": 75,
            "history_bars": len(history),
            "history_from": history.days[0].isoformat() if len(history) else None,
            "history_to": history.days[-1].isoformat() if len(history) else None,
            "modelled": "entry premiums (Black-Scholes at the IV estimate)",
            "real": "index path, expiry settlement" if history.is_real else "nothing — synthetic path",
        },
        started_at=started,
        finished_at=datetime.now(),
    )
    if not history.is_real:
        result.warnings.append(
            "SYNTHETIC history: outcomes are not evidence about the real NIFTY, only a "
            "mechanical check of the rules. Supply csv:<path> or a reachable yahoo feed for real results."
        )
    return result
