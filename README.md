# NIFTY Option-Selling Research Agent

A **LangGraph** agent that keeps researching optimised option-selling strategies for the
NIFTY index: it reads where the index *is* right now, proposes concrete sell-side
structures against the live chain, scores them probabilistically, refuses the ones that
break a risk policy, and publishes a cited research note — then re-plans and tests a
different parameter set on the next cycle. The research log compounds across runs.

> Research tooling, **not investment advice**. Option selling carries unlimited loss
> risk. Verify every leg against a live chain and your own margin before trading.

## The graph

```
START
  └─ load_market_state ──(no data)──────────────► halt ─► END
        │
        ▼
     assess_regime ─► book_context ─► form_hypotheses ─► generate_candidates
                                                                │
                                                                ▼
                                                        score_candidates
                                                                │
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

* **inner cycle** — when nothing clears the guard rails, thresholds are loosened one
  documented step at a time and the *same* scored candidates are re-judged. Every
  relaxation is announced in the note, so a marginal pass can never hide.
* **outer cycle** — each iteration re-plans (baseline families → sigma sweep → wing
  sweep, minus anything already in the research log) so successive cycles add
  information rather than recompute one number.

## What it actually measures

| stage | what it does |
| --- | --- |
| `load_market_state` | resolves a provider: **NSE** live chain → **Yahoo** spot/VIX → **synthetic** → **fixture**, recording which source was used |
| `assess_regime` | labels the index position `trend_up / trend_down / range_low_vol / range_high_vol / vol_shock` from spot vs 20/50-DMA, 20-DMA slope, realised vol, IV–RV edge, PCR and VIX — and prints its reasoning |
| `book_context` | (optional) nets the delta/vega exposure of your open positions and checks whether the top candidate hedges or adds to them |
| `form_hypotheses` | deterministic parameter planner + optional LLM planner, both skipping parameter space already in the log |
| `generate_candidates` | builds short straddle, short strangle, iron condor, iron fly, put/call credit spread and calendar **from quotes that exist** — missing strikes are skipped, never invented |
| `score_candidates` | expiry-payoff grid (legs expiring before the horizon marked to intrinsic, later legs marked at Black-Scholes) → credit, max loss, breakevens, margin proxy, RoM; plus a 512-point lognormal terminal grid → POP, expected value, 5% CVaR |
| `validate` | guard rails: min POP, min credit/risk, max risk per lot, per-leg OI, IV edge, DTE window, naked-short policy, and no naked shorts in a vol shock |
| `rank_and_publish` | appends every evaluated structure to `data/research_log.jsonl` (fingerprinted, so an unchanged tape is not passed off as new research) |
| `finalize` | writes `data/out/<run-id>.md` + `.json` and `latest.md` |

### Model assumptions (printed in every note)

* Options are **priced at chain IV**; terminal outcomes are drawn from
  `rv_weight × realised_vol + (1 − rv_weight) × implied_vol`. That gap *is* the
  premium-selling edge being researched. Set `rv_weight = 0` and every structure
  converges to ≈ zero expected value — the honest null result, and a unit test.
* Payoff is a 401-point grid from −40% to +40% of spot; risk per lot for undefined-risk
  structures is a margin proxy (`naked_margin_pct × spot × lot`), not exchange SPAN.
* Score = `0.35 × EV/risk + 0.25 × POP + 0.15 × IV-edge + 0.15 × liquidity + 0.10 × RoM`,
  scaled to 0–100 with the anchors in `analytics/scoring.py`.

## Install & run

```bash
uv venv --python 3.11 .venv && source .venv/bin/activate
uv pip install -e ".[dev]"

nifty-research run                      # one research pass (auto provider)
nifty-research run --provider fixture   # fully offline, deterministic
nifty-research show                     # print the latest note
nifty-research memory                   # what has been tested, best per regime
nifty-research graph                    # print the topology

nifty-research loop --interval 900      # keep researching every 15 minutes
nifty-research loop --interval 1800 --max-runs 4
```

Python API:

```python
from nifty_research.config import RunConfig
from nifty_research.graph import run_once

state = run_once(RunConfig(provider="fixture"))
print(state["status"], state["note_path"])
```

Exit code is `0` on a normal run, `2` when no market data could be resolved.

## Data sources and their honesty contract

NSE's option-chain endpoint is IP-restricted in practice (it returned nothing at all
from the machine this was built on). The agent therefore never pretends:

| provider | spot/trend/VIX | option quotes | label in the note |
| --- | --- | --- | --- |
| `nse` | live | **live NSE chain** | `nse` |
| `yahoo` | live | modelled from live spot + VIX | `synthetic(yahoo spot 25,150)` + warning |
| `synthetic` | seeded | modelled, Black-Scholes + skew | `synthetic` + warning |
| `fixture` | seeded | modelled from fixture params | `fixture(nifty_chain_sample.json)` + warning |

Capture a real chain where the market is reachable and replay it anywhere:

```bash
python scripts/capture_chain.py --symbol NIFTY --out data/fixtures/nse_25000.json
```

Captures store **days-to-expiry per quote** instead of absolute dates, so a file
captured today still replays consistently a month later.

## Configuration

All via environment (`see .env.example`) — `NR_PROVIDER`, `NR_MAX_ITERATIONS`,
`NR_MAX_RELAXATIONS`, `NR_MIN_POP`, `NR_MIN_CREDIT_RATIO`, `NR_MAX_LOSS_PER_LOT`,
`NR_MIN_LEG_OI`, `NR_DTE_MIN/MAX`, `NR_SIGMA_MULTIPLE`, `NR_WING_STEPS`, `NR_RV_WEIGHT`,
`NR_LOT_SIZE`, `NR_OUT_DIR`, `NR_LOG_PATH`, `NR_POSITIONS`, plus the optional LLM
planner (`NR_LLM_ENABLED`, `NR_LLM_BASE_URL`, `NR_LLM_MODEL`) which accepts any
OpenAI-compatible endpoint (Ollama, vLLM, the Hermes proxy). The LLM is strictly
additive — if it is off or unreachable, the deterministic planner still runs a full agenda.

## See a real run

[`docs/example-report.md`](docs/example-report.md) is the unedited output of
`nifty-research run --provider fixture --iterations 3` — regime read, the three
iterations with what each one tested, the structures that cleared the guard rails,
the ones that did not (with the exact gate each failed), and the model assumptions
printed next to the numbers.

## Tests

```bash
pytest            # 40+ tests, all offline against the deterministic fixture market
```

Coverage of the parts that can silently be wrong: put-call parity, IV round-trip,
delta bounds, risk-neutral drift of the terminal grid, regime labelling across five
hand-built market histories, structure geometry (shorts outside the straddle,
defined-risk detection, calendars spanning two expiries, **missing strikes skipped not
fabricated**, structure names never colliding), condor max-loss arithmetic, the
`rv_weight = 0` null result and its monotonicity, every guard code, relaxation
monotonicity, the end-to-end run, memory de-duplication, acceptance/rejection never
overlapping in the published note, the CLI, and the no-data halt path.

## Layout

```
src/nifty_research/
  config.py           all tunables in one auditable dataclass (incl. relaxation ladder)
  schemas.py          typed contracts shared by every node
  providers/          nse · yahoo · synthetic · fixture (+ resolver with fallback)
  analytics/          greeks · regime · strategies · scoring · guards · book
  research/           memory (JSONL log) · hypotheses (rules + optional LLM) · report
  graph/              state · nodes · build (wiring) · loop (continuous) · cli
scripts/              make_fixture.py · capture_chain.py
data/fixtures/        nifty_chain_sample.json (date-relative, never rots)
```

## Roadmap

- Backtest engine over historical expiry outcomes (turn the research log's model EV into
  realised P&L per regime).
- Real broker integration for margin (SPAN) instead of the proxy.
- Per-leg IV-surface fitting (currently a parametric smile in the synthetic generator).
