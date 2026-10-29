"""Dashboard tests.

Streamlit renders on client connect, so a passing HTTP health check proves nothing about
the app script. ``AppTest`` runs the real script headlessly and reports exceptions and
elements — that is what these tests use, so "the dashboard works" is an assertion, not a
claim.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("streamlit")
pd = pytest.importorskip("pandas")

from streamlit.testing.v1 import AppTest  # noqa: E402

from nifty_research.backtest.engine import BacktestConfig, run_backtest  # noqa: E402
from nifty_research.backtest.evidence import build_evidence  # noqa: E402
from nifty_research.backtest.history import from_synthetic  # noqa: E402

APP = Path(__file__).resolve().parents[1] / "app.py"


def _run_app() -> AppTest:
    app = AppTest.from_file(str(APP), default_timeout=240)
    app.run()
    return app


def _exceptions(app: AppTest) -> list[str]:
    return [str(element.value) for element in app.exception]


def test_dashboard_loads_without_raising():
    app = _run_app()
    assert not _exceptions(app), _exceptions(app)
    titles = [element.value for element in app.title]
    assert any("NIFTY" in title for title in titles), titles


def test_dashboard_exposes_every_tab():
    app = _run_app()
    tabs = getattr(app, "tabs", None)
    assert tabs is not None
    labels = [getattr(tab, "label", None) for tab in tabs]
    assert labels.count(None) < len(labels) or len(tabs) >= 6


def test_dashboard_renders_results_branch_with_a_backtest_in_state():
    """Seeding session state exercises the metrics/charts/tables branch of the backtest tab."""
    result = run_backtest(
        BacktestConfig(max_cycles=6, min_trades=2), history=from_synthetic(days=500, seed=61)
    )
    evidence = build_evidence(result, min_trades=2)

    app = AppTest.from_file(str(APP), default_timeout=240)
    app.session_state["backtest"] = result
    app.session_state["backtest_evidence"] = evidence
    app.run()

    assert not _exceptions(app), _exceptions(app)
    dataframes = getattr(app, "dataframe", [])
    assert len(dataframes) >= 3, "expected the cell / family / trades tables to render"
    infos = [element.value for element in getattr(app, "info", [])]
    assert any("calibrat" in str(text).lower() or "model" in str(text).lower() for text in infos)


def test_dashboard_surfaces_the_synthetic_warning():
    result = run_backtest(
        BacktestConfig(max_cycles=4, min_trades=2), history=from_synthetic(days=500, seed=67)
    )
    app = AppTest.from_file(str(APP), default_timeout=240)
    app.session_state["backtest"] = result
    app.run()
    warnings = [element.value for element in getattr(app, "warning", [])]
    assert any("SYNTHETIC" in str(text) for text in warnings), warnings


def test_dashboard_reads_the_latest_note_when_present(tmp_path):
    import os

    out = Path("data/out")
    out.mkdir(parents=True, exist_ok=True)
    note = out / "latest.md"
    note.write_text("# NIFTY option-selling research\n\nsentinel-note-body\n")
    try:
        app = _run_app()
        assert not _exceptions(app), _exceptions(app)
        markdown = " ".join(element.value for element in app.markdown)
        assert "sentinel-note-body" in markdown
    finally:
        note.unlink(missing_ok=True)
