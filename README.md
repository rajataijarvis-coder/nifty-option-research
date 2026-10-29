# NIFTY Option-Selling Research Agent

A **LangGraph** agent that researches optimised option-selling strategies for the NIFTY index,
plus the two things that turn research into evidence: a **walk-forward backtest** and an
**outcome ledger** that settles its own recommendations at expiry.

> Research tooling, **not investment advice**. Option selling carries unlimited loss risk.
> Verify every leg against a live chain and your own margin before trading.

## The idea in one line

Read where the index is positioned → propose concrete sell-side structures → score them
probabilistically → refuse what breaks the risk policy → publish an auditable note → **measure
what actually happened and feed that back into the ranking**.

## The graph

```
START
  └─ load_market_state ──(no data)──────────────► halt ─► END
        │
        ▼
     assess_regime ─► book_context ─► form_hypotheses ─► generate_candidates
                                                                │
                                                                ▼
                                                        score_candidates  ◄── measured evidence
                                                                │              adjusts the ranking
                                                                ▼
                                                            validate
                                    ┌───────────(nothing passed, relaxations left)
                                    ▼
                                  relax ────────► validate        (inner cycle)
                                    │
                    (a survivor, or relaxations exhausted)
                                    ▼
                            rank_and_publish ─► next_cycle
                                                    │
                              (iterations left) ◄───┴───► finalize ─► END
                                        └─► form_hypotheses          (outer cycle)
```

* **inner cycle** — when nothing clears the guard rails, thresholds loosen one documented step
  at a time and the *same* candidates are re-judged. Every relaxation is printed in the note.
* **outer cycle** — each iteration re-plans (baseline families → sigma sweep → wing sweep, minus
  anything already in the research log), so successive cycles add information.

## The measured loop (new)

```
  index history ──► nifty-research backtest ──► evidence.json ──► influences the live ranking
                          │                          ▲
                          └── per regime × family ────┘
  research log ──► nifty-research settle ──► ledger.jsonl ──► track record vs model expectation
```

**What is real and what is modelled** — the honesty contract, repeated in every report:

| element | status |
| --- | --- |
| index path, expiry settlement | **real** when the history source is real (CSV or Yahoo) |
| entry premiums | **modelled**: Black-Scholes at an IV estimate (VIX if the history carries it, else realised vol × premium) — free historical option chains do not exist |
| evidence adjustments | **measured**, but shrunk (×0.3), capped (±15%) and gated on a minimum trade count |

That mix is deliberate: it tests the *rules* (strike/tenor/regime selection) against real index
outcomes, and it produces a **calibration** number that says whether the model's expected value
is worth anything. On a synthetic path with no genuine variance premium it correctly reports
`calibration -2.7 — model is systematically the wrong sign`, which is the point: the layer is
built to falsify the scorecard, not to flatter it.

Evidence is only evidence when the history is real. Settling against synthetic history is
**refused by default** (`--allow-synthetic` overrides and marks the entries).

## Frontend

A Streamlit dashboard — the logic all stays in the Python package, the app only presents it:

```bash
uv pip install -e ".[ui]"
streamlit run app.py
```

Six tabs: **Run the agent** (provider, iterations, apply-evidence toggle, live note) ·
**Latest note** (plus earlier runs) · **Research log** (filterable table, accepted-only, mean
score by family) · **Backtest** (source, cycles, entry offset, IV premium → metrics, equity curve
of the weekly top pick vs equal-weight-every-candidate, regime × structure tables, save-evidence
button) · **Evidence** (what is adjusting the ranking, and by how much) · **Track record**
(settled recommendations, model expectation vs realised).

## Install & run

```bash
uv venv --python 3.11 .venv && source .venv/bin/activate
uv pip install -e ".[dev,ui]"

nifty-research run                          # one research pass (auto provider)
nifty-research run --provider fixture       # fully offline, deterministic
nifty-research run --evidence data/evidence/evidence.json   # rank with measured outcomes
nifty-research show                         # print the latest note
nifty-research memory                       # what has been tested, best per regime
nifty-research graph                        # print the topology

nifty-research backtest --history synthetic --cycles 120 --min-trades 20
nifty-research backtest --history csv:data/history/nifty_daily.csv   # real evidence
nifty-research settle --history csv:data/history/nifty_daily.csv     # settle the track record

nifty-research loop --interval 900          # keep researching
streamlit run app.py                        # see it
```

Getting **real** history (the one step that cannot be skipped for real conclusions):

```bash
python scripts/fetch_index_history.py --out data/history/nifty_daily.csv --range 10y
# or export date,close[,vix] from any vendor to that path
```

Python API:

```python
from nifty_research.config import RunConfig
from nifty_research.graph import run_once
from nifty_research.backtest import BacktestConfig, run_backtest, load, build_evidence

state = run_once(RunConfig(provider="fixture"))
result = run_backtest(BacktestConfig(max_cycles=120), history=load("synthetic"))
evidence = build_evidence(result, min_trades=20)
```

## What the agent measures

| stage | what it does |
| --- | --- |
| `load_market_state` | resolves a provider: **NSE** live chain → **Yahoo** spot/VIX → **synthetic** → **fixture**, recording which source was used |
| `assess_regime` | labels the index position `trend_up / trend_down / range_low_vol / range_high_vol / vol_shock` from spot vs 20/50-DMA, 20-DMA slope, realised vol, IV–RV edge, PCR and VIX — and prints its reasoning |
| `book_context` | (optional) nets the delta/vega of your open positions and checks whether the top candidate hedges or adds to them |
| `form_hypotheses` | deterministic parameter planner + optional LLM planner, both skipping space already in the log |
| `generate_candidates` | short straddle, short strangle, iron condor, iron fly, put/call credit spread, calendar — built from quotes that exist; missing strikes are skipped, never invented; names come from the legs, so two structures can never collide |
| `score_candidates` | expiry-payoff grid (expiring legs to intrinsic, later legs at Black-Scholes) → credit, max loss, breakevens, margin proxy, RoM; 512-point lognormal terminal grid → POP, expected value, 5% CVaR; then measured-evidence adjustment |
| `validate` | guard rails: min POP, min credit/risk, max risk per lot, per-leg OI, IV edge, DTE window, naked-short policy, no naked shorts in a vol shock |
| `rank_and_publish` | appends every evaluated structure to `data/research_log.jsonl` (fingerprinted, so an unchanged tape is not new research) |
| `finalize` | writes `data/out/<run-id>.md` + `.json` and `latest.md` |
| `backtest` | walk-forward over weekly expiries, no lookahead, settled at expiry → per regime × structure metrics, calibration, top-pick equity curve, evidence table |
| `settle` | settles accepted recommendations from the log at expiry → `data/ledger.jsonl` track record |

### Model assumptions (printed in every note)

* Options are **priced at chain IV**; terminal outcomes come from
  `rv_weight × realised_vol + (1 − rv_weight) × implied_vol`. That gap *is* the premium-selling
  edge being researched — and the backtest's calibration number tells you whether it exists.
* Payoff is a 401-point grid from −40% to +40% of spot; risk per lot for undefined-risk
  structures is a margin proxy (`naked_margin_pct × spot × lot`), not exchange SPAN.
* Score = `0.35 × EV/risk + 0.25 × POP + 0.15 × IV-edge + 0.15 × liquidity + 0.10 × RoM`, then
  `× (1 + evidence adjustment)` when measured evidence is configured.

## See real runs

* [`docs/example-report.md`](docs/example-report.md) — a live-agent note (regime read, three
  iterations, accepted structures, rejections with the exact gate each failed, assumptions).
* [`docs/example-backtest.md`](docs/example-backtest.md) — a 120-cycle backtest run, including the
  calibration verdict.
* [`docs/example-evidence.json`](docs/example-evidence.json) — the evidence table those cycles produced.

## Data sources

NSE's option-chain endpoint is IP-restricted in practice (it returned nothing at all from the
machine this was built on). The agent never pretends otherwise:

| provider | spot/trend/VIX | option quotes | label in the note |
| --- | --- | --- | --- |
| `nse` | live | **live NSE chain** | `nse` |
| `yahoo` | live | modelled from live spot + VIX | `synthetic(yahoo spot …)` + warning |
| `synthetic` | seeded | modelled, Black-Scholes + skew | `synthetic` + warning |
| `fixture` | seeded | modelled from fixture params | `fixture(nifty_chain_sample.json)` + warning |

Capture a real chain where the market is reachable and replay it anywhere:

```bash
python scripts/capture_chain.py --symbol NIFTY --out data/fixtures/nse_25000.json
```

Captures store **days-to-expiry per quote**, so a file captured today still replays
consistently a month later.

## Configuration

Environment driven (see `.env.example`): `NR_PROVIDER`, `NR_MAX_ITERATIONS`,
`NR_MAX_RELAXATIONS`, `NR_MIN_POP`, `NR_MIN_CREDIT_RATIO`, `NR_MAX_LOSS_PER_LOT`,
`NR_MIN_LEG_OI`, `NR_DTE_MIN/MAX`, `NR_SIGMA_MULTIPLE`, `NR_WING_STEPS`, `NR_RV_WEIGHT`,
`NR_LOT_SIZE`, `NR_OUT_DIR`, `NR_LOG_PATH`, `NR_POSITIONS`, `NR_EVIDENCE`, `NR_LEDGER`,
`NR_HISTORY`, plus the optional LLM planner (`NR_LLM_ENABLED`, `NR_LLM_BASE_URL`,
`NR_LLM_MODEL`) which accepts any OpenAI-compatible endpoint. The LLM is strictly additive — if
it is off or unreachable, the deterministic planner still runs a full agenda.

## Tests

```bash
pytest        # 98 tests, all offline and deterministic
```

Covering the parts that can silently be wrong: put-call parity, IV round-trip, delta bounds,
risk-neutral drift of the terminal grid, regime labels across five hand-built market histories,
structure geometry (missing strikes skipped not fabricated, names never colliding), condor
max-loss arithmetic, the `rv_weight = 0` null result and its monotonicity, every guard code,
relaxation monotonicity, the end-to-end run, memory de-duplication, acceptance and rejection
never overlapping, the CLI — and for the new layer: **no-lookahead** (corrupting the future must
not change a past decision), deterministic re-runs, expiry-calendar derivation from trading days,
calibration arithmetic, evidence sample-gating/cap/shrink, ledger settlement maths, settlement
idempotency, the synthetic-history refusal, and the Streamlit app executed headlessly by
`AppTest` (no exceptions, tables and warnings render).

## Layout

```
app.py                streamlit entry point (streamlit run app.py)
src/nifty_research/
  config.py           every tunable in one auditable dataclass (incl. the relaxation ladder)
  schemas.py          typed contracts shared by every stage
  providers/          nse · yahoo · synthetic · fixture (+ resolver with recorded fallback)
  analytics/          greeks · regime · strategies · scoring · guards · book
  research/           memory (JSONL log) · hypotheses (rules + optional LLM) · report
  backtest/           history · engine · metrics · evidence · ledger · report
  ui/                 data (frames) · app (streamlit) — no logic lives in the UI
  graph/              state · nodes · build (wiring) · loop (continuous) · cli
scripts/              make_fixture.py · capture_chain.py · fetch_index_history.py
docs/                 real run outputs used as examples
data/fixtures/        nifty_chain_sample.json (date-relative, never rots)
```

## Roadmap

- Ingest a vendor's historical option chain (when one is affordable) so entry premiums stop
  being modelled — that is the last modelling assumption in the loop.
- Real SPAN margin instead of the proxy.
- Scheduled runs: a daily job that runs the agent, settles last week's expiry, and rebuilds
  the evidence table automatically.
