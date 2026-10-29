"""Hypothesis planning.

The agent does not just re-run one idea every cycle. Each iteration proposes a
*different thing to test* — a family mix, then a sigma sweep, then a width sweep —
and skips parameter combinations the research log already contains, so successive
cycles add information instead of re-deriving the same number.

An LLM can be plugged in as an extra proposal source (``NR_LLM_ENABLED=1``, any
OpenAI-compatible endpoint — Ollama, the Hermes proxy, or a hosted API). It is
strictly additive: if it is off, unreachable, or returns nonsense, the deterministic
planner still produces a full research agenda.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from ..config import RunConfig
from ..schemas import Family, Hypothesis, Regime
from .memory import ResearchMemory

REGIME_FAMILIES: dict[str, list[Family]] = {
    "trend_up": ["put_credit_spread", "call_credit_spread", "iron_condor", "calendar"],
    "trend_down": ["call_credit_spread", "put_credit_spread", "iron_condor", "calendar"],
    "range_low_vol": ["short_straddle", "iron_fly", "short_strangle", "iron_condor", "calendar"],
    "range_high_vol": ["short_strangle", "iron_condor", "short_straddle", "iron_fly", "put_credit_spread"],
    "vol_shock": ["iron_condor", "iron_fly", "put_credit_spread", "call_credit_spread"],
}

SIGMA_LADDER = (0.75, 1.0, 1.25, 1.5)
WING_LADDER = (1, 2, 3)


def baseline_hypothesis(regime: Regime, config: RunConfig) -> Hypothesis:
    return Hypothesis(
        name="baseline",
        families=REGIME_FAMILIES.get(regime.label, ["iron_condor"]),
        sigma_multiple=config.sigma_multiple,
        wing_width_steps=config.wing_width_steps,
        rationale=f"regime-appropriate family mix for {regime.label} at configured parameters",
        origin="rules",
    )


def rules_hypotheses(
    iteration: int,
    regime: Regime,
    config: RunConfig,
    memory: ResearchMemory | None = None,
    limit: int = 3,
) -> list[Hypothesis]:
    families = REGIME_FAMILIES.get(regime.label, ["iron_condor"])
    out: list[Hypothesis] = []
    tried = memory.tried_parameters(regime.label) if memory else set()

    def keep(hyp: Hypothesis) -> bool:
        combos = {(f, hyp.sigma_multiple, hyp.wing_width_steps) for f in hyp.families}
        return not combos.issubset(tried)

    if iteration == 0:
        hyp = baseline_hypothesis(regime, config)
        if keep(hyp):
            out.append(hyp)
        return out

    if iteration == 1:
        for sigma in SIGMA_LADDER:
            if abs(sigma - config.sigma_multiple) < 1e-9:
                continue
            hyp = Hypothesis(
                name=f"sigma_sweep_{sigma:g}",
                families=[f for f in families if f not in {"short_straddle", "iron_fly"}],
                sigma_multiple=sigma,
                wing_width_steps=config.wing_width_steps,
                rationale=(
                    f"widen/narrow the short strikes to {sigma:g}σ of the expected move: tests "
                    "whether the chain is paying enough for the extra distance."
                ),
            )
            if keep(hyp):
                out.append(hyp)
            if len(out) >= limit:
                return out
        return out

    for wings in WING_LADDER:
        if wings == config.wing_width_steps:
            continue
        hyp = Hypothesis(
            name=f"wing_sweep_{wings}step",
            families=[f for f in families if f in {"iron_condor", "iron_fly", "put_credit_spread", "call_credit_spread"}],
            sigma_multiple=config.sigma_multiple,
            wing_width_steps=wings,
            rationale=(
                f"vary the protective wing to {wings} strikes: measures the cost of the tail "
                "hedge against the credit it gives up."
            ),
        )
        if keep(hyp):
            out.append(hyp)
        if len(out) >= limit:
            break
    return out


def llm_hypotheses(
    regime: Regime,
    config: RunConfig,
    memory: ResearchMemory | None = None,
    rejected: dict[str, int] | None = None,
) -> tuple[list[Hypothesis], list[str]]:
    """Ask a local/remote LLM for extra hypotheses. Returns (hypotheses, notes)."""
    notes: list[str] = []
    if not config.llm_enabled:
        return [], notes

    allowed = REGIME_FAMILIES.get(regime.label, ["iron_condor"])
    context: dict[str, Any] = {
        "regime": regime.model_dump(),
        "allowed_families": allowed,
        "already_tested": sorted(
            {"::".join(map(str, combo)) for combo in (memory.tried_parameters(regime.label) if memory else set())}
        )[:40],
        "guard_rejections": rejected or {},
    }
    prompt = (
        "You are a derivatives research planner for NIFTY index options. "
        "Given the market regime and the structures already tested, propose at most 3 NEW "
        "structures to evaluate this cycle. Reply with JSON only, shaped as "
        '{"hypotheses":[{"name":str,"families":[str],"sigma_multiple":float,'
        '"wing_width_steps":int,"rationale":str}]}. '
        f"families must be drawn from {allowed}. Context: {json.dumps(context, default=str)}"
    )
    try:
        resp = httpx.post(
            config.llm_base_url.rstrip("/") + "/chat/completions",
            json={
                "model": config.llm_model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.4,
                "stream": False,
            },
            timeout=config.llm_timeout,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        payload = _extract_json(content)
    except Exception as exc:  # noqa: BLE001 - additive feature, must degrade quietly
        notes.append(f"llm hypotheses unavailable ({type(exc).__name__}: {exc}); using rules planner")
        return [], notes

    out: list[Hypothesis] = []
    for item in (payload.get("hypotheses") or [])[:3]:
        try:
            families = [f for f in item.get("families", []) if f in allowed]
            if not families:
                continue
            hyp = Hypothesis(
                name=str(item.get("name") or "llm_hypothesis"),
                families=families,
                sigma_multiple=float(item.get("sigma_multiple", config.sigma_multiple)),
                wing_width_steps=int(item.get("wing_width_steps", config.wing_width_steps)),
                rationale=str(item.get("rationale") or "")[:400],
                origin="llm",
            )
            out.append(hyp)
        except (TypeError, ValueError):
            continue
    if out:
        notes.append(f"llm proposed {len(out)} additional hypothesis(es)")
    return out, notes


def _extract_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no JSON object in LLM reply")
    return json.loads(text[start : end + 1])


def plan(
    iteration: int,
    regime: Regime,
    config: RunConfig,
    memory: ResearchMemory | None = None,
    rejected: dict[str, int] | None = None,
) -> tuple[list[Hypothesis], list[str]]:
    notes: list[str] = []
    hypotheses = rules_hypotheses(iteration, regime, config, memory=memory)
    llm, llm_notes = llm_hypotheses(regime, config, memory=memory, rejected=rejected)
    notes.extend(llm_notes)
    seen_names = {h.name for h in hypotheses}
    for hyp in llm:
        if hyp.name not in seen_names:
            hypotheses.append(hyp)
    if not hypotheses:
        fallback = baseline_hypothesis(regime, config)
        fallback.name = f"baseline_repeat_iter{iteration}"
        fallback.rationale = "parameter space exhausted for this regime; re-testing baseline"
        hypotheses = [fallback]
        notes.append("all planned parameter combinations already in the research log; repeating baseline")
    return hypotheses, notes
