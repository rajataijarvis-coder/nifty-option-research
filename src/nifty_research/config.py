"""Run configuration for the research agent."""

from __future__ import annotations

import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    try:
        return float(raw) if raw not in (None, "") else default
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    try:
        return int(raw) if raw not in (None, "") else default
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw in (None, ""):
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class RunConfig:
    """Everything the agent needs to make a decision, in one auditable place."""

    # --- market / instrument ---
    symbol: str = "NIFTY"
    provider: str = field(default_factory=lambda: os.environ.get("NR_PROVIDER", "auto"))
    lot_size: int = field(default_factory=lambda: _env_int("NR_LOT_SIZE", 75))
    strike_step: int = field(default_factory=lambda: _env_int("NR_STRIKE_STEP", 50))
    risk_free_rate: float = field(default_factory=lambda: _env_float("NR_RISK_FREE", 0.065))

    # --- research loop shape ---
    max_iterations: int = field(default_factory=lambda: _env_int("NR_MAX_ITERATIONS", 3))
    max_relaxations: int = field(default_factory=lambda: _env_int("NR_MAX_RELAXATIONS", 2))
    target_dte_min: int = field(default_factory=lambda: _env_int("NR_DTE_MIN", 3))
    target_dte_max: int = field(default_factory=lambda: _env_int("NR_DTE_MAX", 14))
    sigma_multiple: float = field(default_factory=lambda: _env_float("NR_SIGMA_MULTIPLE", 1.0))
    wing_width_steps: int = field(default_factory=lambda: _env_int("NR_WING_STEPS", 2))

    # --- risk / guard rails (relaxable in order) ---
    min_pop: float = field(default_factory=lambda: _env_float("NR_MIN_POP", 0.55))
    min_credit_ratio: float = field(default_factory=lambda: _env_float("NR_MIN_CREDIT_RATIO", 0.15))
    max_loss_per_lot: float = field(default_factory=lambda: _env_float("NR_MAX_LOSS_PER_LOT", 150_000.0))
    min_leg_oi: int = field(default_factory=lambda: _env_int("NR_MIN_LEG_OI", 5_000))
    min_iv_rv_edge: float = field(default_factory=lambda: _env_float("NR_MIN_IV_RV_EDGE", -0.02))

    # --- modelling assumptions (documented, not hidden) ---
    rv_weight: float = field(default_factory=lambda: _env_float("NR_RV_WEIGHT", 0.5))
    naked_margin_pct: float = field(default_factory=lambda: _env_float("NR_NAKED_MARGIN_PCT", 0.15))
    allow_undefined_risk: bool = field(default_factory=lambda: _env_bool("NR_ALLOW_UNDEFINED", True))

    # --- outputs ---
    out_dir: Path = field(default_factory=lambda: Path(os.environ.get("NR_OUT_DIR", "data/out")))
    log_path: Path = field(
        default_factory=lambda: Path(os.environ.get("NR_LOG_PATH", "data/research_log.jsonl"))
    )
    positions_path: Path | None = field(
        default_factory=lambda: (
            Path(os.environ["NR_POSITIONS"]) if os.environ.get("NR_POSITIONS") else None
        )
    )
    evidence_path: Path | None = field(
        default_factory=lambda: (
            Path(os.environ["NR_EVIDENCE"]) if os.environ.get("NR_EVIDENCE") else None
        )
    )
    ledger_path: Path = field(
        default_factory=lambda: Path(os.environ.get("NR_LEDGER", "data/ledger.jsonl"))
    )
    backtest_history: str = field(
        default_factory=lambda: os.environ.get("NR_HISTORY", "auto")
    )

    # --- optional LLM hypothesis generator (never required) ---
    llm_enabled: bool = field(default_factory=lambda: _env_bool("NR_LLM_ENABLED", False))
    llm_base_url: str = field(
        default_factory=lambda: os.environ.get("NR_LLM_BASE_URL", "http://127.0.0.1:11434/v1")
    )
    llm_model: str = field(default_factory=lambda: os.environ.get("NR_LLM_MODEL", "llama3.1"))
    llm_timeout: float = field(default_factory=lambda: _env_float("NR_LLM_TIMEOUT", 30.0))

    def with_relaxation(self, level: int) -> "RunConfig":
        """Return a copy with thresholds widened for relaxation level N (1-indexed).

        Relaxation is deliberately monotone and capped: each step loosens exactly
        one dimension so the audit trail can say *why* a marginal candidate passed.
        """
        if level <= 0:
            return self
        cfg = RunConfig(**asdict(self))
        cfg.min_pop = self.min_pop - 0.04 * level
        cfg.min_credit_ratio = self.min_credit_ratio - 0.03 * level
        cfg.min_leg_oi = int(self.min_leg_oi * (0.6**level))
        cfg.min_iv_rv_edge = self.min_iv_rv_edge - 0.01 * level
        cfg.max_loss_per_lot = self.max_loss_per_lot * (1.15**level)
        return cfg

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["out_dir"] = str(self.out_dir)
        d["log_path"] = str(self.log_path)
        d["positions_path"] = str(self.positions_path) if self.positions_path else None
        return d
