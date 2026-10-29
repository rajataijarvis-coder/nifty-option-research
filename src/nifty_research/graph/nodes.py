"""Graph nodes — one responsibility each, all of them pure-ish and observable.

Flow (see ``build.py`` for the wiring):

    load_market_state -> assess_regime -> book_context -> form_hypotheses
        -> generate_candidates -> score_candidates -> validate
             validate --(no survivor, relaxations left)--> relax --> validate
             validate --(survivor OR relaxations exhausted)--> rank_and_publish
        rank_and_publish -> next_cycle --(iterations left)--> form_hypotheses
                                       --(done)--> END
"""

from __future__ import annotations

import secrets
from datetime import date, datetime, timezone

from ..analytics import book as book_mod
from ..analytics import guards, regime as regime_mod, scoring, strategies
from ..config import RunConfig
from ..providers import get_market_state
from ..research import hypotheses as hypo_mod, memory as memory_mod, report
from ..schemas import Finding, ResearchNote
from .state import ResearchState


def _run_id() -> str:
    return f"run-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{secrets.token_hex(2)}"


def load_market_state(state: ResearchState) -> dict:
    """Fetch a market state through the provider chain (or fail cleanly)."""
    config: RunConfig = state["config"]
    try:
        market = get_market_state(config)
    except Exception as exc:  # noqa: BLE001 - the loop must survive any data outage
        return {
            "status": "no_data",
            "errors": [f"market data unavailable: {type(exc).__name__}: {exc}"],
            "warnings": [],
            "note": None,
        }
    return {
        "market": market,
        "run_id": state.get("run_id") or _run_id(),
        "warnings": list(market.warnings),
        "iteration": 0,
        "relax_level": 0,
        "findings": [],
        "accepted": [],
        "rejected": [],
        "guard_report": {},
        "status": "ok",
    }


def assess_regime(state: ResearchState) -> dict:
    config: RunConfig = state["config"]
    market = state["market"]
    expiry = regime_mod.find_expiry_for_dte(market.snapshot, config.target_dte_min, config.target_dte_max)
    regime = regime_mod.analyze(
        market.snapshot, market.history, vix=market.vix, expiry=expiry
    )
    warnings = []
    if market.snapshot.stale:
        warnings.append("chain snapshot is marked stale")
    if not market.history:
        warnings.append("no index history: trend components default to 0")
    return {"regime": regime, "expiry": expiry, "warnings": warnings}


def book_context(state: ResearchState) -> dict:
    """Attach the user's open-position exposure when a positions file is configured."""
    config: RunConfig = state["config"]
    snapshot = state["market"].snapshot
    if not config.positions_path:
        return {"book_context": None}
    try:
        positions = book_mod.load_positions(config.positions_path)
    except Exception as exc:  # noqa: BLE001
        return {
            "book_context": None,
            "warnings": [f"positions file unreadable ({exc}); continuing without book context"],
        }
    if not positions:
        return {"book_context": None, "warnings": ["positions file is empty"]}
    return {"book_context": book_mod.summarize(positions, snapshot, config)}


def form_hypotheses(state: ResearchState) -> dict:
    """Plan what this iteration will test, informed by the persistent research log."""
    config: RunConfig = state["config"]
    iteration = state.get("iteration", 0)
    regime = state["regime"]
    mem = memory_mod.ResearchMemory(config.log_path)
    hist = guards.violation_histogram(state.get("guard_report") or {})
    hypotheses, notes = hypo_mod.plan(iteration, regime, config, memory=mem, rejected=hist)
    warnings = list(notes)
    if not state.get("book_context") and config.positions_path:
        warnings.append("book context requested but unavailable this run")
    return {
        "hypotheses": hypotheses,
        "warnings": warnings,
        "candidates": [],
        "scored": [],
        "accepted": [],
        "rejected": [],
        "guard_report": {},
    }


def generate_candidates(state: ResearchState) -> dict:
    config: RunConfig = state["config"]
    snapshot = state["market"].snapshot
    regime = state["regime"]
    candidates = []
    warnings: list[str] = []
    seen: set[str] = set()
    for hyp in state.get("hypotheses", []):
        built, skips = strategies.build_candidates(snapshot, regime, config, hyp)
        warnings.extend(f"{hyp.name}: {s}" for s in skips)
        for cand in built:
            if cand.name in seen:
                continue
            seen.add(cand.name)
            candidates.append(cand)
    if not candidates:
        warnings.append("no structure could be assembled from this chain")
    return {"candidates": candidates, "warnings": warnings}


def score_candidates(state: ResearchState) -> dict:
    config: RunConfig = state["config"]
    snapshot = state["market"].snapshot
    regime = state["regime"]
    scored = [scoring.score_candidate(c, snapshot, regime, config) for c in state.get("candidates", [])]
    return {"scored": scoring.rank(scored)}


def validate(state: ResearchState) -> dict:
    """Apply the guard rails at the current relaxation level."""
    config: RunConfig = state["config"]
    effective = config.with_relaxation(state.get("relax_level", 0))
    accepted, rejected, report = guards.evaluate_all(state.get("scored", []), effective, state["regime"])
    return {
        "accepted": accepted,
        "rejected": rejected,
        "guard_report": report,
    }


def relax(state: ResearchState) -> dict:
    level = state.get("relax_level", 0) + 1
    config: RunConfig = state["config"]
    loosened = config.with_relaxation(level)
    return {
        "relax_level": level,
        "warnings": [
            (
                f"relaxation {level}/{config.max_relaxations}: min POP {config.min_pop:.0%}->"
                f"{loosened.min_pop:.0%}, min credit/risk {config.min_credit_ratio:.0%}->"
                f"{loosened.min_credit_ratio:.0%}, min leg OI {config.min_leg_oi:,}->"
                f"{loosened.min_leg_oi:,}"
            )
        ],
    }


def rank_and_publish(state: ResearchState) -> dict:
    """Record the iteration's finding, then (at the end of the run) write the note."""
    config: RunConfig = state["config"]
    regime = state["regime"]
    iteration = state.get("iteration", 0)
    accepted = state.get("accepted", [])
    rejected = state.get("rejected", [])
    report_map = state.get("guard_report") or {}
    scored = state.get("scored", [])

    hist = guards.violation_histogram(report_map)
    best = accepted[0] if accepted else (scored[0] if scored else None)
    finding = Finding(
        iteration=iteration,
        hypothesis=", ".join(h.name for h in state.get("hypotheses", [])) or "—",
        regime=regime.label,
        evaluated=len(scored),
        accepted=len(accepted),
        best=(
            {
                "name": best.candidate.name,
                "family": best.candidate.family,
                "score": best.score,
                "pop": best.pop,
                "ev_per_lot": best.ev_per_lot,
                "credit": best.credit,
                "undefined_risk": best.undefined_risk,
            }
            if best
            else None
        ),
        violations=hist,
        notes=[] if accepted else ["no structure passed the guard rails this iteration"],
    )

    # persist every evaluated structure to the research log (memory compounds here)
    mem = memory_mod.ResearchMemory(config.log_path)
    records = memory_mod.records_from_results(
        scored,
        accepted_names={s.candidate.name for s in accepted},
        regime_label=regime.label,
        run_id=state.get("run_id", "unknown"),
        provider=state["market"].provider,
    )
    new_records = [r for r in records if not mem.seen(r["fingerprint"], regime_label=regime.label)]
    written = mem.append(new_records)

    findings = list(state.get("findings", [])) + [finding]
    accumulated_accepted = _merge_unique(state.get("_all_accepted", []), accepted)
    accumulated_rejected = _merge_unique(state.get("_all_rejected", []), rejected)
    accumulated_report = dict(state.get("_all_guard_report") or {})
    accumulated_report.update(report_map)

    return {
        "findings": findings,
        "accepted": accepted,
        "rejected": rejected,
        "_all_accepted": accumulated_accepted,
        "_all_rejected": accumulated_rejected,
        "_all_guard_report": accumulated_report,
        "warnings": [f"iteration {iteration}: logged {written} new research record(s)"]
        if written
        else [],
    }


def next_cycle(state: ResearchState) -> dict:
    iteration = state.get("iteration", 0) + 1
    return {"iteration": iteration, "relax_level": 0}


def finalize(state: ResearchState) -> dict:
    """Write the markdown/JSON note for the whole run."""
    config: RunConfig = state["config"]
    accepted = state.get("_all_accepted", state.get("accepted", []))
    accepted_names = {s.candidate.name for s in accepted}
    # a structure accepted once is not a rejection, even if an earlier iteration refused it
    rejected = [
        s for s in state.get("_all_rejected", state.get("rejected", []))
        if s.candidate.name not in accepted_names
    ]
    report_map = state.get("_all_guard_report") or state.get("guard_report") or {}
    market = state["market"]

    note = ResearchNote(
        run_id=state.get("run_id", _run_id()),
        generated_at=datetime.now(timezone.utc).replace(microsecond=0),
        symbol=market.snapshot.symbol,
        provider=market.provider,
        regime=state["regime"],
        assumptions={
            "spot": market.snapshot.spot,
            "chain_source": market.snapshot.source,
            "expiry_analyzed": (state.get("expiry") or market.snapshot.expiry).isoformat(),
            "lot_size": config.lot_size,
            "risk_free_rate": config.risk_free_rate,
            "expectation_vol": "rv_weight * realised_vol + (1 - rv_weight) * implied_vol",
            "rv_weight": config.rv_weight,
            "scoring_weights": "0.35 EV/risk + 0.25 POP + 0.15 IV edge + 0.15 liquidity + 0.10 ROM",
            "guard_rails": (
                f"min POP {config.min_pop:.0%}, min credit/risk {config.min_credit_ratio:.0%}, "
                f"max risk/lot {config.max_loss_per_lot:,.0f}, min leg OI {config.min_leg_oi:,}, "
                f"DTE window {config.target_dte_min}-{config.target_dte_max}d, "
                f"undefined risk {'allowed' if config.allow_undefined_risk else 'refused'}"
            ),
            "relaxations_applied": state.get("relax_level", 0),
            "iterations": len(state.get("findings", [])),
        },
        book_context=state.get("book_context"),
        accepted=accepted,
        rejected=rejected,
        guard_report=report_map,
        findings=state.get("findings", []),
        warnings=sorted(set(state.get("warnings", []))),
    )
    if state.get("book_context") and accepted:
        note.book_context = book_mod.summarize(
            book_mod.load_positions(config.positions_path), market.snapshot, config, top=accepted[0]
        )
    note.markdown = report.render_markdown(note)
    md_path, _ = report.write_note(note, config.out_dir)
    return {"note": note, "note_path": str(md_path), "status": "ok" if accepted else "no_recommendation"}


def halt(state: ResearchState) -> dict:
    return {"status": state.get("status", "no_data"), "note_path": ""}


# --------------------------------------------------------------------------- routers
def route_after_load(state: ResearchState) -> str:
    return "assess_regime" if state.get("status") == "ok" else "halt"


def route_after_validate(state: ResearchState) -> str:
    if state.get("accepted"):
        return "rank_and_publish"
    if state.get("relax_level", 0) < state["config"].max_relaxations:
        return "relax"
    return "rank_and_publish"


def route_after_cycle(state: ResearchState) -> str:
    if state.get("iteration", 0) < state["config"].max_iterations:
        return "form_hypotheses"
    return "finalize"


def _merge_unique(existing: list, new: list) -> list:
    seen = {s.candidate.name for s in existing}
    out = list(existing)
    for s in new:
        if s.candidate.name not in seen:
            out.append(s)
            seen.add(s.candidate.name)
    return out


def as_date(value) -> date:  # pragma: no cover - convenience for callers/tests
    return value if isinstance(value, date) else date.fromisoformat(str(value))
