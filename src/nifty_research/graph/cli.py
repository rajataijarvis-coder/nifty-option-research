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

    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        args = parser.parse_args(["run"])
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
