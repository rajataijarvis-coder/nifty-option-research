"""Backtest reporting: what the rules actually did, and whether the model was honest."""

from __future__ import annotations

from .engine import BacktestResult
from .evidence import Evidence
from .metrics import (
    all_candidates_series,
    by_family,
    by_regime_family,
    calibration_verdict,
    headline,
    top_pick_series,
)


def _money(x: float) -> str:
    return f"{x:,.0f}"


def render_backtest_markdown(result: BacktestResult, evidence: Evidence | None = None) -> str:
    head = headline(result)
    out: list[str] = []
    out.append("# NIFTY option-selling backtest")
    out.append("")
    out.append(
        f"*history `{result.history_source}` ({result.real_label}) · "
        f"{result.cycles_evaluated} weekly cycles · {len(result.trades)} structures evaluated*"
    )
    out.append("")

    out.append("## Headline: the agent's top pick each week")
    out.append("")
    out.append("| metric | value |")
    out.append("| --- | --- |")
    out.append(f"| total realised P&L (1 lot per week) | **{_money(head['top_pick_total_pnl'])}** |")
    out.append(f"| mean per week | {_money(head['top_pick_mean_pnl'])} |")
    out.append(f"| win rate | {head['top_pick_win_rate']:.1%} |")
    out.append(f"| worst week | {_money(head['top_pick_worst_trade'])} |")
    out.append(f"| 5% CVaR | {_money(head['top_pick_cvar_5'])} |")
    out.append(f"| max drawdown | {_money(head['top_pick_max_drawdown'])} |")
    sharpe = head["top_pick_sharpe_like"]
    out.append(f"| annualised Sharpe-like | {sharpe if sharpe is not None else '—'} |")
    out.append(f"| equal-weight-every-candidate baseline | {_money(head['all_candidates_total_pnl'])} |")
    out.append("")

    out.append("## Is the model's expected value trustworthy?")
    out.append("")
    out.append(
        f"- model expected **{_money(head['model_expected_total'])}**, realised "
        f"**{_money(head['top_pick_total_pnl'])}** across {head['cycles']} weeks"
    )
    out.append(f"- calibration ratio: **{head['calibration'] if head['calibration'] is not None else '—'}**")
    out.append(f"- verdict: **{calibration_verdict(result)}**")
    out.append("")

    out.append("## Measured performance by regime and structure")
    out.append("")
    out.append("| regime | family | trades | win rate | mean P&L | credit kept | worst | 5% CVaR | sample |")
    out.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for cell in by_regime_family(result):
        regime, family = cell.key
        out.append(
            f"| {regime} | {family} | {cell.trades} | {cell.win_rate:.0%} | {_money(cell.mean_pnl)} "
            f"| {cell.credit_kept:+.0%} | {_money(cell.worst_trade)} | {_money(cell.cvar_5)} "
            f"| {'insufficient' if cell.insufficient else 'ok'} |"
        )
    out.append("")

    out.append("## By structure, all regimes")
    out.append("")
    out.append("| family | trades | win rate | mean P&L | total | credit kept | calibration |")
    out.append("| --- | --- | --- | --- | --- | --- | --- |")
    for cell in by_family(result):
        family = cell.key[1]
        cal = f"{cell.calibration:.2f}" if cell.calibration is not None else "—"
        out.append(
            f"| {family} | {cell.trades} | {cell.win_rate:.0%} | {_money(cell.mean_pnl)} "
            f"| {_money(cell.total_pnl)} | {cell.credit_kept:+.0%} | {cal} |"
        )
    out.append("")

    top = top_pick_series(result)
    if top:
        out.append("## Top-pick equity curve (per week, cumulative)")
        out.append("")
        out.append("| cycle | entry | expiry | regime | structure | P&L | cumulative |")
        out.append("| --- | --- | --- | --- | --- | --- | --- |")
        for row in top[-25:]:
            out.append(
                f"| {row['cycle']} | {row['entry_day']} | {row['expiry_day']} | {row['regime']} "
                f"| {row['name']} | {_money(row['realised_pnl'])} | {_money(row['cumulative_pnl'])} |"
            )
        out.append("")

    if evidence is not None:
        out.append("## Evidence table built from this backtest")
        out.append("")
        for line in evidence.summary_lines():
            out.append(f"- {line}")
        out.append("")

    if result.warnings:
        out.append("## Warnings")
        out.append("")
        for warning in dict.fromkeys(result.warnings):
            out.append(f"- {warning}")
        out.append("")

    out.append("## What is real and what is modelled")
    out.append("")
    for key, value in result.entries.items():
        out.append(f"- **{key}**: {value}")
    out.append("")
    out.append(
        "*Entry premiums are model prices, so this backtest measures the strike/tenor/regime "
        "rules against real index outcomes — not the P&L a real book would have booked.*"
    )
    return "\n".join(out)


def write_backtest_report(result: BacktestResult, evidence: Evidence | None, out_dir) -> str:
    from pathlib import Path

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = result.finished_at or result.started_at
    path = out_dir / f"backtest-{stamp:%Y%m%dT%H%M%S}.md"
    path.write_text(render_backtest_markdown(result, evidence))
    (out_dir / "latest-backtest.md").write_text(render_backtest_markdown(result, evidence))
    (out_dir / f"backtest-{stamp:%Y%m%dT%H%M%S}.json").write_text(result.model_dump_json(indent=2))
    return str(path)


def equity_rows(result: BacktestResult) -> list[dict]:
    """Merged top-pick and baseline curves, for charting."""
    top = {row["cycle"]: row for row in top_pick_series(result)}
    every = {row["cycle"]: row for row in all_candidates_series(result)}
    rows: list[dict] = []
    for cycle in sorted(top):
        rows.append(
            {
                "cycle": cycle,
                "entry_day": top[cycle]["entry_day"],
                "top_pick_cumulative": top[cycle]["cumulative_pnl"],
                "all_candidates_cumulative": every.get(cycle, {}).get("cumulative_pnl"),
                "regime": top[cycle]["regime"],
            }
        )
    return rows
