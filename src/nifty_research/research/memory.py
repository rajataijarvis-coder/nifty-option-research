"""Persistent research memory.

The agent's value compounds across runs only if it remembers what it already tested.
This is a JSON-lines log: append-only, greppable, one line per evaluated structure,
with a fingerprint so re-running on an unchanged tape does not pretend to be new
research.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from ..schemas import Finding, ResearchNote, ScoredCandidate


def fingerprint(scored: ScoredCandidate, regime_label: str) -> str:
    cand = scored.candidate
    legs = "|".join(
        f"{leg.action}{leg.option_type}{leg.strike:g}@{leg.expiry.isoformat()}"
        for leg in sorted(cand.legs, key=lambda l: (l.expiry, l.strike, l.option_type))
    )
    raw = f"{regime_label}::{cand.family}::{legs}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


class ResearchMemory:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.entries: list[dict[str, Any]] = []
        self._load()

    # ------------------------------------------------------------------ io
    def _load(self) -> None:
        if not self.path.exists():
            return
        for line in self.path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                self.entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # a corrupt line must not poison the whole history

    def append(self, records: Iterable[dict[str, Any]]) -> int:
        records = list(records)
        if not records:
            return 0
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a") as fh:
            for record in records:
                fh.write(json.dumps(record, default=str) + "\n")
        self.entries.extend(records)
        return len(records)

    # ------------------------------------------------------------------ queries
    def seen(self, fp: str, regime_label: str | None = None) -> bool:
        return any(
            e.get("fingerprint") == fp and (regime_label is None or e.get("regime") == regime_label)
            for e in self.entries
        )

    def tried_parameters(self, regime_label: str) -> set[tuple[str, float, int]]:
        out: set[tuple[str, float, int]] = set()
        for e in self.entries:
            if e.get("regime") != regime_label:
                continue
            params = e.get("params") or {}
            if e.get("family") is None:
                continue
            out.add(
                (
                    str(e["family"]),
                    float(params.get("sigma_multiple", 1.0)),
                    int(params.get("wing_steps", 0)),
                )
            )
        return out

    def best_by_regime(self) -> dict[str, dict[str, Any]]:
        best: dict[str, dict[str, Any]] = {}
        for e in self.entries:
            regime = e.get("regime")
            if not regime:
                continue
            current = best.get(regime)
            if current is None or (e.get("score") or -1e9) > (current.get("score") or -1e9):
                best[regime] = e
        return best

    def regime_counts(self) -> dict[str, int]:
        counts: dict[str, int] = defaultdict(int)
        for e in self.entries:
            counts[str(e.get("regime"))] += 1
        return dict(counts)

    def summary(self, limit: int = 5) -> str:
        if not self.entries:
            return "research log is empty"
        lines = [f"{len(self.entries)} evaluated structures across {len(self.regime_counts())} regimes"]
        for regime, entry in sorted(self.best_by_regime().items()):
            lines.append(
                f"  {regime}: best {entry.get('family')} '{entry.get('name')}' "
                f"score {entry.get('score')} (POP {entry.get('pop')})"
            )
        return "\n".join(lines[: limit + 1])


def records_from_results(
    scored: list[ScoredCandidate],
    accepted_names: set[str],
    regime_label: str,
    run_id: str,
    provider: str,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for s in scored:
        out.append(
            {
                "ts": datetime.now().replace(microsecond=0).isoformat(),
                "run_id": run_id,
                "provider": provider,
                "regime": regime_label,
                "family": s.candidate.family,
                "name": s.candidate.name,
                "hypothesis": s.candidate.hypothesis,
                "fingerprint": fingerprint(s, regime_label),
                "params": s.candidate.params,
                "score": s.score,
                "pop": s.pop,
                "credit": s.credit,
                "ev_per_lot": s.ev_per_lot,
                "cvar_5": s.cvar_5,
                "max_loss": s.max_loss,
                "margin_proxy": s.margin_proxy,
                "iv_edge": s.iv_edge,
                "undefined_risk": s.undefined_risk,
                "accepted": s.candidate.name in accepted_names,
                "legs": [
                    {
                        "action": leg.action,
                        "option_type": leg.option_type,
                        "strike": leg.strike,
                        "expiry": leg.expiry.isoformat(),
                        "price": leg.price,
                    }
                    for leg in s.candidate.legs
                ],
            }
        )
    return out


def note_payload(note: ResearchNote) -> dict[str, Any]:
    return json.loads(note.model_dump_json())
