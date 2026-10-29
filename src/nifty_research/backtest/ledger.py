"""Track-record ledger: settle what the live agent actually recommended.

The research log records what the agent *proposed*. This settles those proposals at their
expiry against real index closes, so the agent accumulates a track record instead of an
opinion — closing the loop between the model's expected value and what actually happened.

Two honesty guards:

* by default a settlement requires a **real** history source; settling synthetic
  recommendations against a synthetic path proves nothing, so it is refused
  (``allow_synthetic=True`` overrides, and marks the entries accordingly);
* structures with a leg that outlives the front expiry (calendars) cannot be settled from
  index closes alone, so the surviving leg is marked to a model value and the entry is
  flagged ``model_valued_legs`` rather than being presented as clean evidence.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field

from ..analytics.greeks import realized_vol
from ..analytics.scoring import structure_pnl
from ..config import RunConfig
from ..schemas import Candidate, Leg, Regime
from .history import IndexHistory


class LedgerEntry(BaseModel):
    run_id: str
    name: str
    family: str
    regime: str
    expiry_day: date
    settled_day: date
    spot_settle: float
    credit: float
    model_ev: float
    pop: float
    score: float
    realised_pnl: float
    model_valued_legs: bool = False
    history_source: str
    synthetic: bool = False
    settled_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc).replace(microsecond=0))

    @property
    def key(self) -> str:
        return f"{self.run_id}|{self.name}|{self.expiry_day.isoformat()}"


def load_ledger(path: Path | str) -> list[LedgerEntry]:
    path = Path(path)
    if not path.exists():
        return []
    out: list[LedgerEntry] = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(LedgerEntry(**json.loads(line)))
        except Exception:  # noqa: BLE001
            continue
    return out


def append_ledger(path: Path | str, entries: list[LedgerEntry]) -> int:
    if not entries:
        return 0
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as fh:
        for entry in entries:
            fh.write(entry.model_dump_json() + "\n")
    return len(entries)


def _regime_stub() -> Regime:
    return Regime(
        label="range_low_vol",
        trend_strength=0.0,
        realized_vol=0.0,
        implied_vol=0.0,
        iv_rv_edge=0.0,
        pcr=1.0,
    )


def _candidate_from_record(record: dict) -> Candidate | None:
    legs: list[Leg] = []
    for raw in record.get("legs") or []:
        try:
            legs.append(
                Leg(
                    action=raw["action"],
                    option_type=raw["option_type"],
                    strike=float(raw["strike"]),
                    expiry=date.fromisoformat(str(raw["expiry"])),
                    price=float(raw.get("price") or 0.0),
                )
            )
        except Exception:  # noqa: BLE001
            return None
    if not legs:
        return None
    return Candidate(name=record.get("name", "unknown"), family=record["family"], legs=legs)


def settle(
    log_path: Path | str,
    history: IndexHistory,
    ledger_path: Path | str,
    only_accepted: bool = True,
    allow_synthetic: bool = False,
    config: RunConfig | None = None,
) -> dict:
    """Settle every expired proposal in the research log. Idempotent by (run, name, expiry)."""
    config = config or RunConfig()
    log_path = Path(log_path)
    if not log_path.exists():
        return {"settled": 0, "skipped": 0, "reason": f"no research log at {log_path}"}
    if not history.is_real and not allow_synthetic:
        return {
            "settled": 0,
            "skipped": 0,
            "reason": (
                "refusing to settle against synthetic history — a synthetic track record is not "
                "evidence; supply csv:<path> or a reachable yahoo feed, or pass allow_synthetic=True"
            ),
        }

    existing = {entry.key for entry in load_ledger(ledger_path)}
    records = [json.loads(line) for line in log_path.read_text().splitlines() if line.strip()]
    settled: list[LedgerEntry] = []
    skipped = 0

    for record in records:
        if only_accepted and not record.get("accepted"):
            skipped += 1
            continue
        candidate = _candidate_from_record(record)
        if candidate is None:
            skipped += 1
            continue
        front = candidate.front_expiry()
        settlement = history.close_on_or_after(front)
        if settlement is None:
            skipped += 1
            continue
        settled_day, spot_settle = settlement
        key = f"{record.get('run_id')}|{candidate.name}|{front.isoformat()}"
        if key in existing:
            skipped += 1
            continue

        iv = max(realized_vol([b.close for b in history.bars(0, history.index_of(settled_day) or len(history) - 1)], window=20) * 1.15, 0.05)
        model_valued = any(leg.expiry > front for leg in candidate.legs)
        pnl = structure_pnl(candidate, spot_settle, front, config, iv, config.lot_size)
        entry = LedgerEntry(
            run_id=str(record.get("run_id")),
            name=candidate.name,
            family=candidate.family,
            regime=str(record.get("regime")),
            expiry_day=front,
            settled_day=settled_day,
            spot_settle=round(spot_settle, 2),
            credit=float(record.get("credit") or 0.0),
            model_ev=float(record.get("ev_per_lot") or 0.0),
            pop=float(record.get("pop") or 0.0),
            score=float(record.get("score") or 0.0),
            realised_pnl=round(pnl, 2),
            model_valued_legs=model_valued,
            history_source=history.source,
            synthetic=not history.is_real,
        )
        settled.append(entry)
        existing.add(key)

    written = append_ledger(ledger_path, settled)
    return {
        "settled": written,
        "skipped": skipped,
        "history_source": history.source,
        "synthetic": not history.is_real,
        "ledger_path": str(ledger_path),
        "summary": track_record(load_ledger(ledger_path)),
    }


def track_record(entries: list[LedgerEntry]) -> dict:
    """Per-family realised record, plus how the model's expectation compares."""
    buckets: dict[str, list[LedgerEntry]] = {}
    for entry in entries:
        buckets.setdefault(entry.family, []).append(entry)
    per_family: dict[str, dict] = {}
    for family, rows in buckets.items():
        pnls = [e.realised_pnl for e in rows]
        credits = [e.credit for e in rows]
        evs = [e.model_ev for e in rows]
        mean_pnl = sum(pnls) / len(pnls)
        mean_credit = sum(credits) / len(credits)
        mean_ev = sum(evs) / len(evs)
        per_family[family] = {
            "settled": len(rows),
            "total_pnl": round(sum(pnls), 2),
            "mean_pnl": round(mean_pnl, 2),
            "win_rate": round(sum(1 for p in pnls if p > 0) / len(pnls), 4),
            "credit_kept": round(mean_pnl / mean_credit, 4) if abs(mean_credit) > 1e-9 else None,
            "mean_model_ev": round(mean_ev, 2),
            "calibration": round(mean_pnl / mean_ev, 3) if abs(mean_ev) > 1e-9 else None,
        }
    total = [e.realised_pnl for e in entries]
    return {
        "settled_total": len(entries),
        "total_pnl": round(sum(total), 2) if total else 0.0,
        "win_rate": round(sum(1 for p in total if p > 0) / len(total), 4) if total else None,
        "per_family": per_family,
        "any_synthetic": any(e.synthetic for e in entries),
    }
