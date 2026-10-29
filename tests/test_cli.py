"""CLI smoke tests — the entry point is what a user actually touches."""

from __future__ import annotations

import json
from pathlib import Path

from nifty_research.graph.cli import main


def _args(tmp_path: Path, *extra: str) -> list[str]:
    return [
        *extra,
        "--provider",
        "fixture",
        "--out",
        str(tmp_path / "out"),
        "--log",
        str(tmp_path / "research_log.jsonl"),
    ]


def test_run_command_exits_zero_and_prints_a_summary(tmp_path, capsys):
    code = main(_args(tmp_path, "run", "--iterations", "2"))
    out = capsys.readouterr().out
    assert code == 0
    assert "status    : ok" in out
    assert "regime    :" in out
    assert "note      :" in out
    assert (tmp_path / "out" / "latest.md").exists()


def test_run_command_supports_json_output(tmp_path, capsys):
    code = main(_args(tmp_path, "run", "--iterations", "1", "--json"))
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["status"] == "ok"
    assert payload["iterations"] == 1
    assert Path(payload["note_path"]).exists()


def test_show_command_prints_the_latest_note(tmp_path, capsys):
    main(_args(tmp_path, "run", "--iterations", "1"))
    capsys.readouterr()
    assert main(_args(tmp_path, "show")) == 0
    assert "# NIFTY option-selling research" in capsys.readouterr().out


def test_show_command_reports_a_missing_note(tmp_path, capsys):
    assert main(_args(tmp_path, "show")) == 1
    assert "no report yet" in capsys.readouterr().err


def test_memory_command_summarises_the_log(tmp_path, capsys):
    main(_args(tmp_path, "run", "--iterations", "2"))
    capsys.readouterr()
    assert main(_args(tmp_path, "memory")) == 0
    assert "evaluated structures" in capsys.readouterr().out


def test_graph_command_prints_the_topology(capsys):
    import argparse

    from nifty_research.graph.cli import cmd_graph

    assert cmd_graph(argparse.Namespace()) == 0
    out = capsys.readouterr().out
    assert "relax -> validate" in out
    assert "next_cycle -> form_hypotheses" in out


def test_loop_command_runs_a_bounded_number_of_times(tmp_path, capsys):
    code = main(_args(tmp_path, "loop", "--iterations", "1", "--interval", "0", "--max-runs", "2"))
    out = capsys.readouterr().out
    assert code == 0
    assert "finished 2 run(s)" in out
    assert (tmp_path / "out" / "heartbeat.json").exists()
