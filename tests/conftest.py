"""Shared test fixtures. Every test runs offline against the deterministic fixture market."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from nifty_research.analytics.regime import analyze, find_expiry_for_dte  # noqa: E402
from nifty_research.config import RunConfig  # noqa: E402
from nifty_research.providers.fixture import FixtureProvider  # noqa: E402


@pytest.fixture
def config(tmp_path: Path) -> RunConfig:
    return RunConfig(
        provider="fixture",
        out_dir=tmp_path / "out",
        log_path=tmp_path / "research_log.jsonl",
        max_iterations=1,
    )


@pytest.fixture
def market(config: RunConfig):
    return FixtureProvider().fetch(config)


@pytest.fixture
def front_expiry(market, config):
    return find_expiry_for_dte(market.snapshot, config.target_dte_min, config.target_dte_max)


@pytest.fixture
def regime(market, front_expiry):
    return analyze(market.snapshot, market.history, vix=market.vix, expiry=front_expiry)
