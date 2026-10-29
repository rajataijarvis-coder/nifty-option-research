"""Continuous research loop: run the graph on an interval, forever if asked.

Designed to be boring on purpose — a crash in one cycle logs and continues, a
heartbeat file shows the loop is alive, and Ctrl-C exits cleanly with a summary.
"""

from __future__ import annotations

import json
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

from ..config import RunConfig
from .build import run_once


def heartbeat_path(out_dir: Path | str) -> Path:
    return Path(out_dir) / "heartbeat.json"


def _write_heartbeat(out_dir: Path | str, run_status: str, run: dict | None = None) -> None:
    path = heartbeat_path(out_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "ts": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": run_status,
    }
    if run:
        payload["last_run"] = run
    path.write_text(json.dumps(payload, default=str, indent=2))


def summarize_state(state: dict) -> dict:
    accepted = state.get("_all_accepted") or state.get("accepted") or []
    return {
        "run_id": state.get("run_id"),
        "status": state.get("status"),
        "provider": (state.get("market").provider if state.get("market") else None),
        "regime": (state["regime"].label if state.get("regime") else None),
        "iterations": state.get("iteration"),
        "accepted": len(accepted),
        "top": [s.candidate.name for s in accepted[:3]],
        "note_path": state.get("note_path"),
        "errors": state.get("errors", []),
    }


def run_loop(
    config: RunConfig,
    interval_seconds: int,
    max_runs: int = 0,
    on_run=None,
) -> list[dict]:
    """Run the agent every ``interval_seconds`` until ``max_runs`` (0 == forever)."""
    results: list[dict] = []
    run_number = 0
    try:
        while max_runs == 0 or run_number < max_runs:
            run_number += 1
            try:
                state = run_once(config)
                summary = summarize_state(state)
            except Exception as exc:  # noqa: BLE001 - never let one cycle kill the loop
                summary = {
                    "run_number": run_number,
                    "status": "error",
                    "error": f"{type(exc).__name__}: {exc}",
                    "traceback": traceback.format_exc(limit=3),
                }
            summary["run_number"] = run_number
            results.append(summary)
            _write_heartbeat(config.out_dir, summary.get("status", "unknown"), run=summary)
            if on_run:
                on_run(summary)

            if max_runs == 0 or run_number < max_runs:
                time.sleep(interval_seconds)
    except KeyboardInterrupt:
        _write_heartbeat(config.out_dir, "interrupted", runs=run_number)
    return results
