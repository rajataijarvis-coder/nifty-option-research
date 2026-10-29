"""Streamlit dashboard: a window onto the agent's research, evidence and track record.

Run it with:  streamlit run app.py
Everything it shows comes from the same package the CLI uses — the UI holds no logic of
its own, it only presents it.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..backtest.engine import BacktestConfig, run_backtest
from ..backtest.evidence import build_evidence, load_optional
from ..backtest.history import load as load_history
from ..backtest.ledger import load_ledger, settle, track_record
from ..backtest.metrics import calibration_verdict, headline
from ..config import RunConfig
from ..graph.build import run_once
from ..graph.loop import summarize_state
from . import data as ui

FAMILIES = [
    "short_straddle",
    "short_strangle",
    "iron_condor",
    "iron_fly",
    "put_credit_spread",
    "call_credit_spread",
    "calendar",
]


def main() -> None:
    import streamlit as st

    st.set_page_config(page_title="NIFTY option-selling research", layout="wide")
    st.title("NIFTY option-selling research")
    st.caption(
        "LangGraph research agent · regime → structures → scored → guard-railed · "
        "backtested and tracked against real index outcomes"
    )

    out_dir = Path(st.sidebar.text_input("notes directory", "data/out"))
    log_path = Path(st.sidebar.text_input("research log", "data/research_log.jsonl"))
    ledger_path = Path(st.sidebar.text_input("ledger", "data/ledger.jsonl"))
    evidence_path = Path(st.sidebar.text_input("evidence file", "data/evidence/evidence.json"))
    st.sidebar.divider()
    st.sidebar.caption("Data sources are pluggable: NSE is IP-restricted in practice, so live runs "
                       "fall back to a modelled chain — every report says which source it used.")

    tabs = st.tabs(["Run the agent", "Latest note", "Research log", "Backtest", "Evidence", "Track record"])

    with tabs[0]:
        _run_tab(st, out_dir, log_path, evidence_path)
    with tabs[1]:
        _note_tab(st, out_dir)
    with tabs[2]:
        _log_tab(st, log_path)
    with tabs[3]:
        _backtest_tab(st, evidence_path)
    with tabs[4]:
        _evidence_tab(st, evidence_path)
    with tabs[5]:
        _track_record_tab(st, log_path, ledger_path, evidence_path)


# --------------------------------------------------------------------------- tabs
def _run_tab(st, out_dir: Path, log_path: Path, evidence_path: Path) -> None:
    st.subheader("Run one research pass")
    col1, col2, col3, col4 = st.columns(4)
    provider = col1.selectbox("data provider", ["fixture", "synthetic", "auto", "nse", "yahoo"], index=0)
    iterations = col2.number_input("iterations", min_value=1, max_value=10, value=2)
    relaxations = col3.number_input("max relaxations", min_value=0, max_value=5, value=2)
    use_evidence = col4.checkbox("apply measured evidence", value=evidence_path.exists())

    if st.button("Run agent", type="primary"):
        config = RunConfig(
            provider=provider,
            max_iterations=int(iterations),
            max_relaxations=int(relaxations),
            out_dir=out_dir,
            log_path=log_path,
            evidence_path=evidence_path if use_evidence else None,
        )
        with st.spinner("running the graph..."):
            state = run_once(config)
        st.session_state["last_state_summary"] = summarize_state(state)
        st.session_state["last_note"] = state["note"].markdown if state.get("note") else ""
        st.session_state["last_status"] = state.get("status")
        st.session_state["last_regime"] = state["regime"].label if state.get("regime") else None

    summary = st.session_state.get("last_state_summary")
    if summary:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("status", summary.get("status"))
        c2.metric("regime", summary.get("regime"))
        c3.metric("accepted", summary.get("accepted"))
        c4.metric("iterations", summary.get("iterations"))
        if summary.get("errors"):
            for err in summary["errors"]:
                st.error(err)
        if st.session_state.get("last_note"):
            if st.session_state.get("last_status") == "no_recommendation":
                st.warning("Nothing cleared the guard rails this run — that is a result too.")
            st.markdown(st.session_state["last_note"])
    else:
        st.info("Press **Run agent** to produce a fresh note. Everything below reads from disk, "
                "so the tabs work on previous runs even without re-running.")


def _note_tab(st, out_dir: Path) -> None:
    st.subheader("Latest research note")
    files = ui.note_files(out_dir)
    latest = out_dir / "latest.md"
    if latest.exists():
        st.markdown(ui.read_text(latest))
    else:
        st.info(f"no note at {latest} yet — run the agent first")
    if files:
        with st.expander(f"{len(files)} earlier notes"):
            choice = st.selectbox("open a note", [f.name for f in files])
            st.markdown(ui.read_text(out_dir / choice))


def _log_tab(st, log_path: Path) -> None:
    st.subheader("Research log — everything the agent has evaluated")
    frame = ui.log_frame(log_path)
    if frame.empty:
        st.info(f"no records in {log_path} yet")
        return

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("structures evaluated", len(frame))
    c2.metric("accepted", int(frame["accepted"].sum()))
    c3.metric("regimes covered", frame["regime"].nunique())
    c4.metric("families", frame["family"].nunique())

    regimes = st.multiselect("regime", sorted(frame["regime"].dropna().unique()))
    families = st.multiselect("family", sorted(frame["family"].dropna().unique()))
    accepted_only = st.checkbox("accepted only", value=False)

    view = frame
    if regimes:
        view = view[view["regime"].isin(regimes)]
    if families:
        view = view[view["family"].isin(families)]
    if accepted_only:
        view = view[view["accepted"]]
    st.dataframe(
        view[["ts", "run_id", "regime", "family", "name", "accepted", "score", "pop", "credit", "ev_per_lot"]],
        use_container_width=True,
        hide_index=True,
    )

    st.caption("Mean score by family (accepted structures only)")
    accepted = frame[frame["accepted"]]
    if not accepted.empty:
        st.bar_chart(accepted.groupby("family")["score"].mean())


def _backtest_tab(st, evidence_path: Path) -> None:
    st.subheader("Walk-forward backtest")
    st.caption(
        "Index path and expiry settlement are real when the history is real; entry premiums are "
        "modelled from an IV estimate, because free historical option chains do not exist. "
        "That makes this a test of the rules, and a calibration test of the model."
    )
    col1, col2, col3 = st.columns(3)
    source = col1.selectbox("history", ["synthetic", "csv:<path>", "yahoo", "auto"], index=0)
    cycles = col2.number_input("max weekly cycles (0 = all)", min_value=0, max_value=2000, value=120)
    min_trades = col3.number_input("min trades for evidence", min_value=1, max_value=200, value=20)
    col4, col5, col6 = st.columns(3)
    entry_offset = col4.number_input("entry offset (trading days before expiry)", 1, 5, 3)
    iv_premium = col5.number_input("IV premium vs realised", 0.5, 2.0, 1.15)
    families = col6.multiselect("families (default: regime mix)", FAMILIES)

    csv_path = ""
    if source.startswith("csv"):
        csv_path = st.text_input("csv path", "data/history/nifty_daily.csv")
        source = f"csv:{csv_path}"

    if st.button("Run backtest", type="primary"):
        try:
            history = load_history(source)
        except Exception as exc:  # noqa: BLE001 - show the error in the UI
            st.error(f"could not load history: {exc}")
            return
        bt_config = BacktestConfig(
            entry_offset_trading_days=int(entry_offset),
            iv_premium=float(iv_premium),
            families=families or None,
            max_cycles=int(cycles) or None,
            min_trades=int(min_trades),
        )
        with st.spinner(f"backtesting {len(history)} bars of {history.source}..."):
            result = run_backtest(bt_config, history=history)
        st.session_state["backtest"] = result
        st.session_state["backtest_evidence"] = build_evidence(result, min_trades=int(min_trades))

    result = st.session_state.get("backtest")
    if result is None:
        st.info("Press **Run backtest**. Synthetic history is instant; a real CSV gives real evidence.")
        return

    head = headline(result)
    if not result.history_is_real:
        st.warning("SYNTHETIC history — mechanical check of the rules, not evidence about the real NIFTY. "
                   "Drop a real CSV at the path above for real results.")
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("top-pick total P&L", f"{head['top_pick_total_pnl']:,.0f}")
    c2.metric("win rate", f"{head['top_pick_win_rate']:.1%}")
    c3.metric("max drawdown", f"{head['top_pick_max_drawdown']:,.0f}")
    c4.metric("calibration", head["calibration"] if head["calibration"] is not None else "—")
    c5.metric("vs sell-everything", f"{head['all_candidates_total_pnl']:,.0f}")
    st.info(calibration_verdict(result))

    equity = ui.equity_frame(result)
    if not equity.empty:
        st.caption("Cumulative P&L — agent's weekly top pick vs equal-weight every candidate")
        st.line_chart(equity.set_index("cycle")[["top_pick_cumulative", "all_candidates_cumulative"]])

    st.caption("Measured performance by regime and structure")
    st.dataframe(ui.cells_frame(result, int(min_trades)), use_container_width=True, hide_index=True)
    with st.expander("By structure (all regimes)"):
        st.dataframe(ui.family_frame(result, int(min_trades)), use_container_width=True, hide_index=True)
    with st.expander("By regime (all structures)"):
        st.dataframe(ui.regime_frame(result, int(min_trades)), use_container_width=True, hide_index=True)
    with st.expander(f"Every trade ({len(result.trades)})"):
        st.dataframe(ui.trades_frame(result), use_container_width=True, hide_index=True)

    evidence = st.session_state.get("backtest_evidence")
    if evidence is not None:
        if st.button(f"Save evidence to {evidence_path}"):
            evidence.write(evidence_path)
            st.success(f"wrote {evidence_path} — the agent will use it when 'apply measured evidence' is on")


def _evidence_tab(st, evidence_path: Path) -> None:
    st.subheader("Measured evidence feeding the ranking")
    evidence = load_optional(evidence_path)
    if evidence is None:
        st.info(
            f"no evidence file at {evidence_path}. Run a backtest, then save it — until then the "
            "agent ranks on its model alone (and says so in the note)."
        )
        return
    st.markdown("  \n".join(f"- {line}" for line in evidence.summary_lines()))
    st.dataframe(ui.evidence_frame(evidence), use_container_width=True, hide_index=True)
    st.caption(
        "Adjustments are shrunk by the shrink factor and capped, and cells below the minimum "
        "trade count are ignored — a backtest must not turn into a curve-fit."
    )
    if st.button("Delete evidence file"):
        evidence_path.unlink(missing_ok=True)
        st.warning("evidence file removed; the agent is back to model-only ranking")


def _track_record_tab(st, log_path: Path, ledger_path: Path, evidence_path: Path) -> None:
    st.subheader("Track record — what the agent recommended, settled at expiry")
    entries = load_ledger(ledger_path)
    if not entries:
        st.info(
            f"nothing settled yet in {ledger_path}. Settlement needs real index history: "
            "`nifty-research settle --history csv:<path>` (synthetic settlement is refused by default)."
        )
    else:
        record = track_record(entries)
        c1, c2, c3 = st.columns(3)
        c1.metric("settled recommendations", record["settled_total"])
        c2.metric("total realised P&L", f"{record['total_pnl']:,.0f}")
        c3.metric("win rate", f"{record['win_rate']:.1%}" if record["win_rate"] is not None else "—")
        if record["any_synthetic"]:
            st.warning("some entries were settled against synthetic history — not evidence")
        st.dataframe(ui.ledger_frame(entries), use_container_width=True, hide_index=True)
        st.caption("Per-family realised record vs what the model expected")
        st.dataframe(
            json_normalize(record["per_family"]), use_container_width=True, hide_index=True
        )

    if st.button("Settle expired recommendations now"):
        try:
            history = load_history("auto")
        except Exception as exc:  # noqa: BLE001
            st.error(f"could not load history: {exc}")
            return
        result = settle(log_path=log_path, history=history, ledger_path=ledger_path)
        if result.get("reason"):
            st.warning(result["reason"])
        else:
            st.success(f"settled {result['settled']} (skipped {result['skipped']})")
            st.rerun()


def json_normalize(payload: dict):
    import pandas as pd

    return pd.DataFrame([{"family": k, **v} for k, v in payload.items()])
