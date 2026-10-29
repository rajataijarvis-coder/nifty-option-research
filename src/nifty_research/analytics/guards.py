"""Guard rails: the objective rules a candidate must clear to be *published*.

The agent is allowed to propose anything; it is not allowed to recommend something
that breaks the risk policy. Violations are returned as coded facts (so the
relaxation loop and the audit log can both reason about them) rather than prose.
"""

from __future__ import annotations

from ..config import RunConfig
from ..schemas import GuardResult, GuardViolation, Regime, ScoredCandidate


def evaluate(scored: ScoredCandidate, config: RunConfig, regime: Regime) -> GuardResult:
    v: list[GuardViolation] = []

    if scored.pop < config.min_pop:
        v.append(
            GuardViolation(
                code="POP_BELOW_MIN",
                detail=f"POP {scored.pop:.1%} < required {config.min_pop:.1%}",
            )
        )

    risk_denominator = max(scored.margin_proxy, 1.0)
    credit_ratio = scored.credit / risk_denominator
    if credit_ratio < config.min_credit_ratio:
        v.append(
            GuardViolation(
                code="CREDIT_RATIO_LOW",
                detail=(
                    f"credit {scored.credit:,.0f} is {credit_ratio:.1%} of risk "
                    f"{risk_denominator:,.0f} (< {config.min_credit_ratio:.1%})"
                ),
            )
        )

    if risk_denominator > config.max_loss_per_lot:
        v.append(
            GuardViolation(
                code="MAX_LOSS_EXCEEDED",
                detail=f"risk per lot {risk_denominator:,.0f} > cap {config.max_loss_per_lot:,.0f}",
            )
        )

    thin = [
        f"{leg.option_type}{leg.strike:g} OI {leg.oi}"
        for leg in scored.candidate.legs
        if leg.oi < config.min_leg_oi
    ]
    if thin:
        v.append(
            GuardViolation(
                code="LIQUIDITY_THIN",
                detail=f"legs below min OI {config.min_leg_oi:,}: " + ", ".join(thin),
            )
        )

    if scored.iv_edge < config.min_iv_rv_edge:
        v.append(
            GuardViolation(
                code="IV_EDGE_LOW",
                detail=f"short-leg IV edge {scored.iv_edge:+.2%} < {config.min_iv_rv_edge:+.2%}",
            )
        )

    if not (config.target_dte_min <= scored.dte <= config.target_dte_max):
        v.append(
            GuardViolation(
                code="DTE_OUT_OF_WINDOW",
                detail=(
                    f"{scored.dte}d to expiry outside {config.target_dte_min}-"
                    f"{config.target_dte_max}d window"
                ),
            )
        )

    if scored.undefined_risk and not config.allow_undefined_risk:
        v.append(
            GuardViolation(
                code="UNDEFINED_RISK_NOT_ALLOWED",
                detail="structure carries uncovered short optionality",
            )
        )

    if regime.label == "vol_shock" and scored.candidate.family in {"short_straddle", "short_strangle"}:
        v.append(
            GuardViolation(
                code="NAKED_SHORT_IN_VOL_SHOCK",
                detail="naked short premium is refused while the regime is vol_shock",
            )
        )

    return GuardResult(passed=not v, violations=v)


def evaluate_all(
    scored: list[ScoredCandidate], config: RunConfig, regime: Regime
) -> tuple[list[ScoredCandidate], list[ScoredCandidate], dict[str, list[GuardViolation]]]:
    """Returns ``(accepted, rejected, report)`` with the report keyed by candidate name."""
    accepted: list[ScoredCandidate] = []
    rejected: list[ScoredCandidate] = []
    report: dict[str, list[GuardViolation]] = {}
    for s in scored:
        res = evaluate(s, config, regime)
        report[s.candidate.name] = res.violations
        (accepted if res.passed else rejected).append(s)
    return accepted, rejected, report


def violation_histogram(report: dict[str, list[GuardViolation]]) -> dict[str, int]:
    hist: dict[str, int] = {}
    for violations in report.values():
        for viol in violations:
            hist[viol.code] = hist.get(viol.code, 0) + 1
    return dict(sorted(hist.items(), key=lambda kv: -kv[1]))
