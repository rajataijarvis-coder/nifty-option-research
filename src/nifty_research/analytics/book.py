"""Book context: what the user's existing NIFTY positions imply for new structures."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

from ..config import RunConfig
from ..schemas import Bar, ChainSnapshot, ScoredCandidate
from .greeks import bs_delta, bs_vega, intrinsic

DEFAULT_RISK_FREE = 0.065


def load_positions(path: Path | str) -> list[dict]:
    payload = json.loads(Path(path).read_text())
    if isinstance(payload, dict):
        return list(payload.get("positions") or [])
    return list(payload)


def _years_to(expiry_offset_days: int) -> float:
    return max(expiry_offset_days, 1) / 365.0


def summarize(
    positions: list[dict],
    snapshot: ChainSnapshot,
    config: RunConfig | None = None,
    top: ScoredCandidate | None = None,
) -> dict:
    """Net greek exposure of the open book, plus a conflict check against ``top``.

    Deltas/vegas are model values (Black-Scholes at the position's own IV) reported
    per lot — good enough to answer "does this new trade add to or offset the book?".
    """
    rate = (config.risk_free_rate if config else DEFAULT_RISK_FREE)
    spot = snapshot.spot
    net_delta = 0.0
    net_vega = 0.0
    gross_premium_short = 0.0
    rows: list[dict] = []

    for pos in positions:
        option_type = str(pos.get("option_type", "CE")).upper()
        strike = float(pos["strike"])
        qty = float(pos.get("qty", 0))
        dte = int(pos.get("expiry_offset_days", 7))
        iv = float(pos.get("iv") or snapshot.atm_iv() or 0.13)
        years = _years_to(dte)
        delta = bs_delta(spot, strike, years, iv, rate, option_type) * qty
        vega = bs_vega(spot, strike, years, iv, rate) * qty / 100.0  # per vol point
        moneyness = (strike - spot) / spot
        rows.append(
            {
                "option_type": option_type,
                "strike": strike,
                "qty": qty,
                "dte": dte,
                "delta": round(delta, 2),
                "vega_per_point": round(vega, 2),
                "moneyness_pct": round(moneyness * 100, 2),
                "intrinsic": round(intrinsic(spot, strike, option_type), 2),
            }
        )
        net_delta += delta
        net_vega += vega
        if qty < 0:
            gross_premium_short += abs(qty)

    context: dict = {
        "positions": rows,
        "net_delta": round(net_delta, 2),
        "net_vega_per_vol_point": round(net_vega, 2),
        "short_lots": gross_premium_short,
    }

    if top is not None:
        cand_delta = 0.0
        for leg in top.candidate.legs:
            years = max((leg.expiry - snapshot.as_of.date()).days, 1) / 365.0
            iv = leg.iv or snapshot.atm_iv() or 0.13
            cand_delta += (
                bs_delta(spot, leg.strike, years, iv, rate, leg.option_type)
                * (leg.qty if leg.action == "BUY" else -leg.qty)
                * config.lot_size
                if config
                else bs_delta(spot, leg.strike, years, iv, rate, leg.option_type) * leg.qty
            )
        context["top_candidate_delta"] = round(cand_delta, 2)
        if net_delta != 0 and cand_delta != 0 and (net_delta > 0) != (cand_delta > 0):
            context["delta_conflict"] = True
            context["note"] = (
                "top candidate's delta offsets the existing book's net delta (hedge-like), "
                "so its standalone score overstates the marginal risk it takes"
            )
        else:
            context["delta_conflict"] = False
            context["note"] = "top candidate's delta adds to the existing book's net delta"
    return context


def history_bar_count(history: list[Bar]) -> int:
    return len(history)
