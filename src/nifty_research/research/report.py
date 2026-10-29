"""Markdown + JSON rendering of a research run.

The report is written to be auditable: every number that drove a decision is visible,
the modelling assumptions are printed next to the results, and rejected structures
keep their rejection codes so a human can overrule the guard rails knowingly.
"""

from __future__ import annotations

from pathlib import Path

from ..schemas import ResearchNote, ScoredCandidate


def _fmt_money(x: float) -> str:
    return f"{x:,.0f}"


def _legs_table(scored: ScoredCandidate) -> str:
    lines = ["| action | type | strike | expiry | price | OI |", "| --- | --- | --- | --- | --- | --- |"]
    for leg in scored.candidate.legs:
        lines.append(
            f"| {leg.action} | {leg.option_type} | {leg.strike:,.0f} | {leg.expiry.isoformat()} "
            f"| {leg.price:,.2f} | {leg.oi:,} |"
        )
    return "\n".join(lines)


def render_markdown(note: ResearchNote) -> str:
    regime = note.regime
    out: list[str] = []
    out.append(f"# NIFTY option-selling research — {note.run_id}")
    out.append("")
    out.append(f"*generated {note.generated_at.isoformat()} · data source `{note.provider}`*")
    out.append("")

    out.append("## Where the index is")
    out.append("")
    out.append(f"- **regime**: `{regime.label}` (trend strength {regime.trend_strength:+.2f})")
    out.append(
        f"- **vol**: realised {regime.realized_vol:.2%} vs implied {regime.implied_vol:.2%} "
        f"→ edge {regime.iv_rv_edge:+.2%}"
    )
    if regime.vix is not None:
        out.append(f"- **VIX**: {regime.vix:.2f}")
    out.append(f"- **put/call OI**: {regime.pcr:.2f}")
    for key, value in regime.levels.items():
        out.append(f"- **{key}**: {value:,.2f}")
    out.append("")
    for line in regime.notes:
        out.append(f"> {line}")
    out.append("")

    out.append("## What was tested this run")
    out.append("")
    out.append("| iteration | hypothesis | evaluated | accepted | best |")
    out.append("| --- | --- | --- | --- | --- |")
    for finding in note.findings:
        best = finding.best or {}
        best_txt = (
            f"{best.get('name')} (score {best.get('score')})" if best.get("name") else "—"
        )
        out.append(
            f"| {finding.iteration} | {finding.hypothesis} | {finding.evaluated} "
            f"| {finding.accepted} | {best_txt} |"
        )
    out.append("")

    if note.accepted:
        out.append("## Recommended structures (cleared every guard rail)")
        out.append("")
        for s in note.accepted:
            out.append(f"### {s.candidate.name} — score {s.score:.1f}")
            out.append("")
            out.append(f"*{s.candidate.family} · hypothesis `{s.candidate.hypothesis}`*")
            out.append("")
            out.append(s.candidate.rationale)
            out.append("")
            out.append(_legs_table(s))
            out.append("")
            premium_label = "credit" if s.credit >= 0 else "debit"
            out.append(
                f"- {premium_label} **{_fmt_money(abs(s.credit))}** per lot-set"
                f" · max loss **{_fmt_money(s.max_loss)}**"
                f" · margin proxy **{_fmt_money(s.margin_proxy)}** · return on margin {s.rom:.1%}"
            )
            out.append(
                f"- POP **{s.pop:.1%}** · expected value **{_fmt_money(s.ev_per_lot)}** per lot-set"
                f" · 5% CVaR **{_fmt_money(s.cvar_5)}**"
            )
            out.append(
                f"- breakevens {', '.join(f'{b:,.0f}' for b in s.breakevens) or '—'}"
                f" · {s.dte}d to expiry · IV edge {s.iv_edge:+.2%}"
            )
            out.append(
                f"- {'⚠️ undefined risk (naked short optionality)' if s.undefined_risk else 'defined risk'}"
                f" · liquidity {s.liquidity:.2f}"
            )
            if s.evidence_adjustment:
                out.append(
                    f"- measured-evidence adjustment {s.evidence_adjustment:+.1%} "
                    f"from {s.evidence_trades} settled trades in this regime"
                )
            out.append("")
    else:
        out.append("## Recommended structures")
        out.append("")
        out.append(
            "**Nothing cleared the guard rails this run.** That is a real result: the tape did not "
            "pay enough for the risk. The rejections below show which gate each structure failed."
        )
        out.append("")

    if note.rejected:
        out.append("## Rejected (and why)")
        out.append("")
        out.append("| structure | score | POP | failures |")
        out.append("| --- | --- | --- | --- |")
        for s in note.rejected:
            codes = ", ".join(v.code for v in note.guard_report.get(s.candidate.name, []))
            out.append(f"| {s.candidate.name} | {s.score:.1f} | {s.pop:.1%} | {codes or '—'} |")
        out.append("")
        for s in note.rejected[:5]:
            reasons = "; ".join(v.detail for v in note.guard_report.get(s.candidate.name, []))
            out.append(f"- **{s.candidate.name}**: {reasons or 'no guard violation recorded'}")
        out.append("")

    if note.book_context:
        out.append("## Existing book context")
        out.append("")
        out.append(
            f"- net delta {note.book_context.get('net_delta')} · net vega/vol-point "
            f"{note.book_context.get('net_vega_per_vol_point')} · short lots {note.book_context.get('short_lots')}"
        )
        if note.book_context.get("note"):
            out.append(f"- {note.book_context['note']}")
        out.append("")

    out.append("## Model assumptions (read these before acting)")
    out.append("")
    for key, value in note.assumptions.items():
        out.append(f"- **{key}**: {value}")
    out.append("")

    if note.warnings:
        out.append("## Warnings")
        out.append("")
        for warning in note.warnings:
            out.append(f"- {warning}")
        out.append("")

    out.append("---")
    out.append("")
    out.append(
        "*Research output, not investment advice. Option selling carries unlimited loss risk; "
        "verify every leg against the live chain and your own margin before trading.*"
    )
    return "\n".join(out)


def write_note(note: ResearchNote, out_dir: Path) -> tuple[Path, Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / f"{note.run_id}.md"
    json_path = out_dir / f"{note.run_id}.json"
    md_path.write_text(note.markdown)
    json_path.write_text(note.model_dump_json(indent=2))
    latest = out_dir / "latest.md"
    latest.write_text(note.markdown)
    return md_path, json_path
