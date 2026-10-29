"""CLI entry point."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..config import RunConfig
from ..research import memory as memory_mod
from .build import build_graph, run_once
from .loop import run_loop, summarize_state


def _add_config_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--provider", default=None, help="auto|nse|yahoo|synthetic|fixture")
    parser.add_argument("--iterations", type=int, default=None, help="research iterations per run")
    parser.add_argument("--relaxations", type=int, default=None, help="max guard-rail relaxations")
    parser.add_argument("--out", default=None, help="output directory for notes")
    parser.add_argument("--log", default=None, help="research log (JSONL) path")
    parser.add_argument("--positions", default=None, help="JSON file describing open positions")
    parser.add_argument("--evidence", default=None, help="measured-evidence JSON from a backtest")
    parser.add_argument("--ledger", default=None, help="settled track-record ledger path")
    parser.add_argument("--lot-size", type=int, default=None)
    parser.add_argument("--rv-weight", type=float, default=None, help="0=price at IV, 1=price at realised")
    parser.add_argument("--llm", action="store_true", help="enable the optional LLM hypothesis planner")
    parser.add_argument("--json", action="store_true", help="print the run summary as JSON")


def _config_from_args(args: argparse.Namespace) -> RunConfig:
    config = RunConfig()
    if args.provider:
        config.provider = args.provider
    if args.iterations is not None:
        config.max_iterations = args.iterations
    if args.relaxations is not None:
        config.max_relaxations = args.relaxations
    if args.out:
        config.out_dir = Path(args.out)
    if args.log:
        config.log_path = Path(args.log)
    if getattr(args, "positions", None):
        config.positions_path = Path(args.positions)
    if getattr(args, "evidence", None):
        config.evidence_path = Path(args.evidence)
    if getattr(args, "ledger", None):
        config.ledger_path = Path(args.ledger)
    if getattr(args, "lot_size", None):
        config.lot_size = args.lot_size
    if getattr(args, "rv_weight", None) is not None:
        config.rv_weight = args.rv_weight
    if getattr(args, "llm", False):
        config.llm_enabled = True
    return config


def _print_summary(summary: dict, config: RunConfig, as_json: bool) -> None:
    if as_json:
        print(json.dumps(summary, indent=2, default=str))
        return
    print(f"run       : {summary.get('run_id')}")
    print(f"status    : {summary.get('status')}")
    print(f"provider  : {summary.get('provider')}")
    print(f"regime    : {summary.get('regime')}")
    print(f"iterations: {summary.get('iterations')}")
    print(f"accepted  : {summary.get('accepted') or 0}")
    for item in summary.get("top") or []:
        print(f"  - {item}")
    if summary.get("errors"):
        for err in summary["errors"]:
            print(f"error     : {err}")
    print(f"note      : {summary.get('note_path')}")
    print(f"log       : {config.log_path}")


def cmd_run(args: argparse.Namespace) -> int:
    config = _config_from_args(args)
    state = run_once(config)
    summary = summarize_state(state)
    _print_summary(summary, config, args.json)
    if state.get("status") == "no_data":
        return 2
    return 0


def cmd_loop(args: argparse.Namespace) -> int:
    config = _config_from_args(args)
    runs_target = 0 if args.max_runs <= 0 else args.max_runs

    def on_run(summary: dict) -> None:
        print(
            f"[{summary.get('run_number')}] {summary.get('status')} "
            f"regime={summary.get('regime')} accepted={summary.get('accepted')} "
            f"-> {summary.get('note_path')}",
            flush=True,
        )

    print(
        f"research loop: every {args.interval}s, "
        f"{'forever' if runs_target == 0 else str(runs_target) + ' run(s)'}, provider={config.provider}"
    )
    results = run_loop(config, args.interval, max_runs=runs_target, on_run=on_run)
    print(f"finished {len(results)} run(s)")
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    config = _config_from_args(args)
    latest = Path(config.out_dir) / "latest.md"
    if not latest.exists():
        print(f"no report yet at {latest}", file=sys.stderr)
        return 1
    print(latest.read_text())
    return 0


def cmd_memory(args: argparse.Namespace) -> int:
    config = _config_from_args(args)
    mem = memory_mod.ResearchMemory(config.log_path)
    print(mem.summary())
    if args.json:
        print(json.dumps(mem.best_by_regime(), indent=2, default=str))
    return 0


def cmd_backtest(args: argparse.Namespace) -> int:
    """Walk-forward backtest over an index history, then build the evidence table."""
    from ..backtest.engine import BacktestConfig, run_backtest
    from ..backtest.evidence import build_evidence
    from ..backtest.history import load as load_history
    from ..backtest.metrics import by_regime_family, calibration_verdict, headline
    from ..backtest.report import write_backtest_report

    config = _config_from_args(args)
    try:
        history = load_history(args.history, range_=args.history_range)
    except Exception as exc:  # noqa: BLE001 - CLI boundary
        print(f"could not load history {args.history!r}: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    families = [f.strip() for f in args.families.split(",")] if args.families else None
    bt_config = BacktestConfig(
        entry_offset_trading_days=args.entry_offset,
        iv_premium=args.iv_premium,
        families=families,
        sigma_multiple=args.sigma_multiple,
        wing_width_steps=args.wing_steps,
        max_cycles=args.cycles or None,
        min_trades=args.min_trades,
    )

    def progress(cycle: int, total: int) -> None:
        if not args.quiet and (cycle % 10 == 0 or cycle == total):
            print(f"  cycle {cycle}/{total}", file=sys.stderr)

    result = run_backtest(bt_config, history=history, progress=progress)
    evidence = build_evidence(result, min_trades=args.min_trades)
    report_path = write_backtest_report(result, evidence, args.out or config.out_dir)
    evidence_path = None
    if args.evidence_out is not False:
        evidence_path = str(evidence.write(args.evidence_out))

    head = headline(result)
    if args.json:
        print(json.dumps({"headline": head, "report": report_path, "evidence": evidence_path}, indent=2, default=str))
        return 0

    print(f"history   : {result.history_source} ({result.real_label})")
    print(f"cycles    : {result.cycles_evaluated} weekly rolls, {len(result.trades)} structures evaluated")
    print(f"top pick  : total {head['top_pick_total_pnl']:,.0f} | mean/week {head['top_pick_mean_pnl']:,.0f} "
          f"| win {head['top_pick_win_rate']:.1%} | max DD {head['top_pick_max_drawdown']:,.0f} "
          f"| CVaR5 {head['top_pick_cvar_5']:,.0f}")
    print(f"sell-all  : {head['all_candidates_total_pnl']:,.0f} (equal-weight every candidate)")
    print(f"model said: {head['model_expected_total']:,.0f} expected vs {head['top_pick_total_pnl']:,.0f} realised "
          f"(calibration {head['calibration']})")
    print(f"verdict   : {calibration_verdict(result)}")
    print("strongest cells (min trades %d):" % args.min_trades)
    for cell in by_regime_family(result, min_trades=args.min_trades)[:5]:
        flag = "insufficient" if cell.insufficient else f"kept {cell.credit_kept:+.0%} of credit"
        print(f"  {cell.key[0]:15} {cell.key[1]:20} {cell.trades:3} trades  mean {cell.mean_pnl:>10,.0f}  {flag}")
    if result.warnings:
        for warning in dict.fromkeys(result.warnings):
            print(f"warning   : {warning}")
    print(f"report    : {report_path}")
    if evidence_path:
        print(f"evidence  : {evidence_path}")
    return 0


def cmd_settle(args: argparse.Namespace) -> int:
    """Settle expired recommendations in the research log against real index closes."""
    from ..backtest.history import load as load_history
    from ..backtest.ledger import settle

    config = _config_from_args(args)
    log_path = Path(args.log) if args.log else config.log_path
    try:
        history = load_history(args.history, range_=args.history_range)
    except Exception as exc:  # noqa: BLE001
        print(f"could not load history {args.history!r}: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    result = settle(
        log_path=log_path,
        history=history,
        ledger_path=config.ledger_path,
        only_accepted=not args.include_rejected,
        allow_synthetic=args.allow_synthetic,
        config=config,
    )
    if args.json:
        print(json.dumps(result, indent=2, default=str))
        return 0 if result.get("settled") is not None else 1

    if result.get("reason"):
        print(result["reason"], file=sys.stderr)
        return 1
    print(f"settled   : {result['settled']} (skipped {result['skipped']})")
    print(f"history   : {result['history_source']}")
    summary = result.get("summary") or {}
    print(f"total P&L : {summary.get('total_pnl')} | win rate {summary.get('win_rate')}")
    for family, stats in (summary.get("per_family") or {}).items():
        print(
            f"  {family:22} {stats['settled']:3} settled  mean {stats['mean_pnl']:>10,.0f} "
            f"win {stats['win_rate']:.0%}  kept {stats['credit_kept']}  calibration {stats['calibration']}"
        )
    print(f"ledger    : {result['ledger_path']}")
    return 0


def cmd_graph(args: argparse.Namespace) -> int:
    """Print the compiled graph's nodes/edges — useful documentation for a reviewer."""
    compiled = build_graph()
    drawable = compiled.get_graph()
    print("nodes:")
    for name in sorted(drawable.nodes):
        print(f"  - {name}")
    print("edges:")
    for edge in drawable.edges:
        print(f"  {edge.source} -> {edge.target}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="nifty-research",
        description="LangGraph research agent for NIFTY option-selling strategies.",
    )
    sub = parser.add_subparsers(dest="command")

    run_p = sub.add_parser("run", help="run the research agent once")
    _add_config_flags(run_p)
    run_p.set_defaults(func=cmd_run)

    loop_p = sub.add_parser("loop", help="keep researching on an interval")
    _add_config_flags(loop_p)
    loop_p.add_argument("--interval", type=int, default=900, help="seconds between runs")
    loop_p.add_argument("--max-runs", type=int, default=0, help="0 = run forever")
    loop_p.set_defaults(func=cmd_loop)

    show_p = sub.add_parser("show", help="print the latest research note")
    _add_config_flags(show_p)
    show_p.set_defaults(func=cmd_show)

    mem_p = sub.add_parser("memory", help="summarise the persistent research log")
    _add_config_flags(mem_p)
    mem_p.set_defaults(func=cmd_memory)

    graph_p = sub.add_parser("graph", help="print the graph topology")
    graph_p.set_defaults(func=cmd_graph)

    bt_p = sub.add_parser("backtest", help="walk-forward backtest and evidence build")
    _add_config_flags(bt_p)
    bt_p.add_argument("--history", default="auto", help="auto|synthetic|yahoo|csv:<path>")
    bt_p.add_argument("--history-range", default="10y", help="yahoo range, e.g. 5y")
    bt_p.add_argument("--cycles", type=int, default=0, help="limit to the last N weekly rolls")
    bt_p.add_argument("--entry-offset", type=int, default=3, help="trading days before expiry to enter")
    bt_p.add_argument("--families", default=None, help="comma-separated families (default: regime mix)")
    bt_p.add_argument("--sigma-multiple", type=float, default=1.0)
    bt_p.add_argument("--wing-steps", type=int, default=2)
    bt_p.add_argument("--iv-premium", type=float, default=1.15, help="sold IV = realised vol x this")
    bt_p.add_argument("--min-trades", type=int, default=20, help="sample gate for evidence cells")
    bt_p.add_argument("--evidence-out", default="data/evidence/evidence.json")
    bt_p.add_argument("--quiet", action="store_true")
    bt_p.set_defaults(func=cmd_backtest)

    settle_p = sub.add_parser("settle", help="settle expired recommendations into the ledger")
    _add_config_flags(settle_p)
    settle_p.add_argument("--history", default="auto", help="auto|synthetic|yahoo|csv:<path>")
    settle_p.add_argument("--history-range", default="10y")
    settle_p.add_argument("--include-rejected", action="store_true")
    settle_p.add_argument("--allow-synthetic", action="store_true")
    settle_p.set_defaults(func=cmd_settle)

    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        args = parser.parse_args(["run"])
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
