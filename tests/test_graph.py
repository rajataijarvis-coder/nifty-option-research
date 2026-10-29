"""End-to-end graph tests: the agent runs, cycles, publishes, and remembers."""

from __future__ import annotations

import json
from pathlib import Path

from nifty_research.config import RunConfig
from nifty_research.graph import nodes
from nifty_research.graph.build import build_graph, run_once
from nifty_research.graph.loop import run_loop, summarize_state


def _config(tmp_path: Path, **overrides) -> RunConfig:
    config = RunConfig(
        provider="fixture",
        out_dir=tmp_path / "out",
        log_path=tmp_path / "research_log.jsonl",
        max_iterations=2,
    )
    for key, value in overrides.items():
        setattr(config, key, value)
    return config


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_graph_topology_includes_both_cycles():
    graph = build_graph().get_graph()
    edges = {(e.source, e.target) for e in graph.edges}
    assert ("relax", "validate") in edges  # inner: guard-rail relaxation
    assert ("next_cycle", "form_hypotheses") in edges  # outer: new research iteration
    assert ("load_market_state", "assess_regime") in edges
    assert ("score_candidates", "validate") in edges
    assert ("halt", "__end__") in edges


def test_full_run_publishes_a_note_and_logs_research(tmp_path):
    config = _config(tmp_path)
    state = run_once(config)

    assert state["status"] in {"ok", "no_recommendation"}
    assert state["regime"].label
    assert state["iteration"] == 2  # both iterations ran
    assert len(state["findings"]) == 2

    note_path = Path(state["note_path"])
    assert note_path.exists()
    markdown = note_path.read_text()
    assert "# NIFTY option-selling research" in markdown
    assert "## Where the index is" in markdown
    assert "## What was tested this run" in markdown
    assert "## Model assumptions" in markdown
    assert "not investment advice" in markdown

    records = _jsonl(config.log_path)
    assert records, "research log should have entries"
    assert all(r["fingerprint"] for r in records)
    assert {r["regime"] for r in records} == {state["regime"].label}
    assert (tmp_path / "out" / "latest.md").exists()


def test_every_evaluated_structure_is_recorded_with_its_legs(tmp_path):
    config = _config(tmp_path, max_iterations=1)
    state = run_once(config)
    records = _jsonl(config.log_path)
    evaluated = len(state["scored"])
    assert evaluated > 0
    assert len(records) == evaluated
    sample = records[0]
    for field in ("score", "pop", "credit", "ev_per_lot", "cvar_5", "legs", "family"):
        assert field in sample
    assert all(leg["action"] in {"SELL", "BUY"} for leg in sample["legs"])


def test_memory_deduplicates_a_repeated_tape(tmp_path):
    config = _config(tmp_path, max_iterations=1)
    run_once(config)
    first = _jsonl(config.log_path)
    run_once(config)  # identical fixture market -> identical fingerprints
    second = _jsonl(config.log_path)
    assert len(second) == len(first), "unchanged tape must not masquerade as new research"


def test_relaxation_cycle_fires_when_nothing_passes(tmp_path):
    config = _config(tmp_path, max_iterations=1, max_relaxations=2, min_pop=0.99)
    state = run_once(config)
    relaxed = [w for w in state["warnings"] if w.startswith("relaxation")]
    assert relaxed, "the agent should announce every relaxation it applies"
    assert state["status"] == "no_recommendation"
    markdown = Path(state["note_path"]).read_text()
    assert "Nothing cleared the guard rails" in markdown


def test_second_iteration_tests_different_parameters(tmp_path):
    config = _config(tmp_path, max_iterations=2)
    state = run_once(config)
    hypotheses = [f.hypothesis for f in state["findings"]]
    assert len(set(hypotheses)) == 2, f"iterations should differ, got {hypotheses}"


def test_no_data_path_halts_cleanly(tmp_path, monkeypatch):
    def boom(_config):
        raise RuntimeError("simulated outage")

    monkeypatch.setattr(nodes, "get_market_state", boom)
    state = run_once(_config(tmp_path, max_iterations=1))
    assert state["status"] == "no_data"
    assert state["errors"] and "simulated outage" in state["errors"][0]
    assert state.get("note") is None


def test_book_context_is_attached_when_positions_are_supplied(tmp_path):
    positions = tmp_path / "positions.json"
    positions.write_text(
        json.dumps(
            {
                "positions": [
                    {"option_type": "CE", "strike": 25_600, "qty": -1, "expiry_offset_days": 7, "iv": 0.135},
                    {"option_type": "PE", "strike": 24_700, "qty": -1, "expiry_offset_days": 7, "iv": 0.145},
                ]
            }
        )
    )
    config = _config(tmp_path, max_iterations=1, positions_path=positions)
    state = run_once(config)
    assert state["book_context"] is not None
    assert "net_delta" in state["book_context"]
    assert Path(state["note_path"]).read_text().count("Existing book context") == 1


def test_loop_runs_a_bounded_number_of_times(tmp_path):
    config = _config(tmp_path, max_iterations=1)
    results = run_loop(config, interval_seconds=0, max_runs=2)
    assert len(results) == 2
    assert all(r["status"] in {"ok", "no_recommendation"} for r in results)
    heartbeat = json.loads((tmp_path / "out" / "heartbeat.json").read_text())
    assert heartbeat["status"] in {"ok", "no_recommendation"}


def test_summarize_state_shape(tmp_path):
    state = run_once(_config(tmp_path, max_iterations=1))
    summary = summarize_state(state)
    assert set(summary) >= {"run_id", "status", "provider", "regime", "accepted", "top", "note_path"}
    assert isinstance(summary["accepted"], int)


def test_published_note_never_lists_a_structure_as_both_accepted_and_rejected(tmp_path):
    state = run_once(_config(tmp_path, max_iterations=3))
    note = state["note"]
    accepted_names = {s.candidate.name for s in note.accepted}
    rejected_names = {s.candidate.name for s in note.rejected}
    assert not (accepted_names & rejected_names)
    # every rejection in the report keeps the reason it was rejected for
    for scored in note.rejected:
        assert note.guard_report.get(scored.candidate.name), (
            f"{scored.candidate.name} was rejected but carries no recorded violation"
        )


def test_note_counts_completed_iterations(tmp_path):
    state = run_once(_config(tmp_path, max_iterations=3))
    note = state["note"]
    assert note.assumptions["iterations"] == len(note.findings) == 3
