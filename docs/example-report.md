# NIFTY option-selling research — run-20261029T205314Z-97db

*generated 2026-10-29T20:53:14+00:00 · data source `fixture(nifty_chain_sample.json)`*

## Where the index is

- **regime**: `trend_up` (trend strength +0.64)
- **vol**: realised 11.79% vs implied 13.20% → edge +1.41%
- **VIX**: 13.40
- **put/call OI**: 1.00
- **sma20_pct_from_spot**: 0.73
- **sma50_pct_from_spot**: 4.25
- **range_high_20**: 25,318.03
- **range_low_20**: 24,628.53
- **atm_iv_pct**: 13.20

> spot 25,150 is +0.73% vs 20dma and +4.25% vs 50dma (20dma slope +0.50%)
> realised vol (20d) 11.79%, ATM IV 13.20%, edge +1.41%
> put/call OI ratio 1.00

## What was tested this run

| iteration | hypothesis | evaluated | accepted | best |
| --- | --- | --- | --- | --- |
| 0 | baseline | 4 | 2 | call_credit_spread_S25500C-B25600C_2026-11-05 (score 54.53) |
| 1 | sigma_sweep_0.75, sigma_sweep_1.25, sigma_sweep_1.5 | 6 | 3 | call_credit_spread_S25500C-B25600C_2026-11-05 (score 54.53) |
| 2 | wing_sweep_1step, wing_sweep_3step | 4 | 3 | call_credit_spread_S25500C-B25600C_2026-11-05 (score 54.53) |

## Recommended structures (cleared every guard rail)

### call_credit_spread_S25500C-B25600C_2026-11-05 — score 54.5

*call_credit_spread · hypothesis `baseline`*

25-delta call spread: caps upside participation, collects a defined credit.

| action | type | strike | expiry | price | OI |
| --- | --- | --- | --- | --- | --- |
| SELL | CE | 25,500 | 2026-11-05 | 65.62 | 31,611 |
| BUY | CE | 25,600 | 2026-11-05 | 44.43 | 13,003 |

- credit **1,589** per lot-set · max loss **5,911** · margin proxy **5,911** · return on margin 26.9%
- POP **78.3%** · expected value **100** per lot-set · 5% CVaR **-5,911**
- breakevens 25,521 · 7d to expiry · IV edge +1.24%
- defined risk · liquidity 1.00
- measured-evidence adjustment +7.1% from 50 settled trades in this regime

### put_credit_spread_B24750P-S24850P_2026-11-05 — score 51.4

*put_credit_spread · hypothesis `baseline`*

25-delta put spread: paid to be wrong about downside, losses defined.

| action | type | strike | expiry | price | OI |
| --- | --- | --- | --- | --- | --- |
| SELL | PE | 24,850 | 2026-11-05 | 64.37 | 47,182 |
| BUY | PE | 24,750 | 2026-11-05 | 44.14 | 20,558 |

- credit **1,517** per lot-set · max loss **5,983** · margin proxy **5,983** · return on margin 25.4%
- POP **78.9%** · expected value **79** per lot-set · 5% CVaR **-5,983**
- breakevens 24,830 · 7d to expiry · IV edge +1.58%
- defined risk · liquidity 1.00
- measured-evidence adjustment -1.0% from 50 settled trades in this regime

### iron_condor_B24700P-S24800P-S25500C-B25600C_2026-11-05 — score 42.5

*iron_condor · hypothesis `sigma_sweep_0.75`*

0.75σ strangle with wings: range thesis with a hard floor on the loss.

| action | type | strike | expiry | price | OI |
| --- | --- | --- | --- | --- | --- |
| SELL | CE | 25,500 | 2026-11-05 | 65.62 | 31,611 |
| SELL | PE | 24,800 | 2026-11-05 | 53.50 | 31,611 |
| BUY | CE | 25,600 | 2026-11-05 | 44.43 | 13,003 |
| BUY | PE | 24,700 | 2026-11-05 | 36.15 | 13,003 |

- credit **2,890** per lot-set · max loss **4,610** · margin proxy **4,610** · return on margin 62.7%
- POP **62.7%** · expected value **189** per lot-set · 5% CVaR **-4,610**
- breakevens 24,761, 25,539 · 7d to expiry · IV edge +1.41%
- defined risk · liquidity 1.00
- measured-evidence adjustment -1.2% from 50 settled trades in this regime

### iron_condor_B24650P-S24700P-S25600C-B25650C_2026-11-05 — score 48.1

*iron_condor · hypothesis `wing_sweep_1step`*

1σ strangle with wings: range thesis with a hard floor on the loss.

| action | type | strike | expiry | price | OI |
| --- | --- | --- | --- | --- | --- |
| SELL | CE | 25,600 | 2026-11-05 | 44.43 | 13,003 |
| SELL | PE | 24,700 | 2026-11-05 | 36.15 | 13,003 |
| BUY | CE | 25,650 | 2026-11-05 | 36.09 | 8,011 |
| BUY | PE | 24,650 | 2026-11-05 | 29.39 | 8,011 |

- credit **1,132** per lot-set · max loss **2,618** · margin proxy **2,618** · return on margin 43.3%
- POP **71.3%** · expected value **95** per lot-set · 5% CVaR **-2,618**
- breakevens 24,684, 25,616 · 7d to expiry · IV edge +1.41%
- defined risk · liquidity 1.00
- measured-evidence adjustment -1.2% from 50 settled trades in this regime

## Rejected (and why)

| structure | score | POP | failures |
| --- | --- | --- | --- |
| iron_condor_B24600P-S24700P-S25600C-B25700C_2026-11-05 | 48.9 | 72.7% | LIQUIDITY_THIN |
| calendar_S25150C-B25150C_2026-11-05 | 22.2 | 48.6% | POP_BELOW_MIN, CREDIT_RATIO_LOW |
| iron_condor_B24500P-S24600P-S25700C-B25800C_2026-11-05 | 44.3 | 80.9% | LIQUIDITY_THIN |
| iron_condor_B24350P-S24450P-S25850C-B25950C_2026-11-05 | 39.8 | 89.6% | CREDIT_RATIO_LOW, LIQUIDITY_THIN |
| iron_condor_B24550P-S24700P-S25600C-B25750C_2026-11-05 | 43.6 | 73.8% | LIQUIDITY_THIN |

- **iron_condor_B24600P-S24700P-S25600C-B25700C_2026-11-05**: legs below min OI 5,000: CE25700 OI 4815, PE24600 OI 4815
- **calendar_S25150C-B25150C_2026-11-05**: POP 48.6% < required 55.0%; credit -6,914 is -100.0% of risk 6,914 (< 15.0%)
- **iron_condor_B24500P-S24600P-S25700C-B25800C_2026-11-05**: legs below min OI 5,000: CE25700 OI 4815, PE24600 OI 4815, CE25800 OI 1621, PE24500 OI 1621
- **iron_condor_B24350P-S24450P-S25850C-B25950C_2026-11-05**: credit 784 is 11.7% of risk 6,716 (< 15.0%); legs below min OI 5,000: CE25850 OI 910, PE24450 OI 910, CE25950 OI 269, PE24350 OI 269
- **iron_condor_B24550P-S24700P-S25600C-B25750C_2026-11-05**: legs below min OI 5,000: CE25750 OI 2826, PE24550 OI 2826

## Model assumptions (read these before acting)

- **spot**: 25150.0
- **chain_source**: synthetic
- **expiry_analyzed**: 2026-11-05
- **lot_size**: 75
- **risk_free_rate**: 0.065
- **expectation_vol**: rv_weight * realised_vol + (1 - rv_weight) * implied_vol
- **rv_weight**: 0.5
- **scoring_weights**: 0.35 EV/risk + 0.25 POP + 0.15 IV edge + 0.15 liquidity + 0.10 ROM
- **guard_rails**: min POP 55%, min credit/risk 15%, max risk/lot 150,000, min leg OI 5,000, DTE window 3-14d, undefined risk allowed
- **relaxations_applied**: 0
- **iterations**: 3
- **evidence**: backtest:synthetic:gbm (SYNTHETIC history), shrink 0.3, cap ±15%, min trades 20, built 2026-10-29 20:51

## Warnings

- evidence adjustment applied: put_credit_spread -1.0% (50 trades); call_credit_spread +7.1% (50 trades); iron_condor -1.2% (50 trades)
- evidence adjustment applied: put_credit_spread -1.0% (50 trades); call_credit_spread +7.1% (50 trades); iron_condor -1.2% (50 trades); iron_condor -1.2% (50 trades)
- evidence adjustment applied: put_credit_spread -1.0% (50 trades); call_credit_spread +7.1% (50 trades); iron_condor -1.2% (50 trades); iron_condor -1.2% (50 trades); iron_condor -1.2% (50 trades)
- fixture(nifty_chain_sample.json): option quotes are modelled (Black-Scholes + smile), not real market data
- iteration 0: logged 4 new research record(s)
- iteration 1: logged 3 new research record(s)
- iteration 2: logged 2 new research record(s)

---

*Research output, not investment advice. Option selling carries unlimited loss risk; verify every leg against the live chain and your own margin before trading.*