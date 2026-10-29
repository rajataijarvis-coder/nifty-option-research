# NIFTY option-selling backtest

*history `synthetic:gbm` (SYNTHETIC index path) · 120 weekly cycles · 455 structures evaluated*

## Headline: the agent's top pick each week

| metric | value |
| --- | --- |
| total realised P&L (1 lot per week) | **-47,475** |
| mean per week | -396 |
| win rate | 71.7% |
| worst week | -6,218 |
| 5% CVaR | -6,105 |
| max drawdown | -65,735 |
| annualised Sharpe-like | -0.87 |
| equal-weight-every-candidate baseline | -70,321 |

## Is the model's expected value trustworthy?

- model expected **17,292**, realised **-47,475** across 120 weeks
- calibration ratio: **-2.745**
- verdict: **model is systematically the wrong sign: expected gains realised as losses**

## Measured performance by regime and structure

| regime | family | trades | win rate | mean P&L | credit kept | worst | 5% CVaR | sample |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| trend_up | call_credit_spread | 50 | 82% | 377 | +24% | -6,102 | -6,060 | ok |
| trend_up | iron_condor | 50 | 72% | -82 | -4% | -5,685 | -5,652 | ok |
| trend_up | put_credit_spread | 50 | 76% | -54 | -3% | -6,218 | -6,152 | ok |
| range_high_vol | iron_condor | 47 | 66% | -438 | -23% | -5,774 | -5,748 | ok |
| range_high_vol | iron_fly | 47 | 19% | -149 | -2% | -1,084 | -1,028 | ok |
| range_high_vol | put_credit_spread | 47 | 77% | 14 | +1% | -6,081 | -6,063 | ok |
| range_high_vol | short_straddle | 47 | 51% | -3,939 | -17% | -59,154 | -57,099 | ok |
| range_high_vol | short_strangle | 47 | 70% | -2,873 | -59% | -47,666 | -46,910 | ok |
| trend_down | call_credit_spread | 22 | 77% | -111 | -7% | -6,086 | -6,086 | ok |
| trend_down | iron_condor | 22 | 59% | -723 | -37% | -5,950 | -5,950 | ok |
| trend_down | put_credit_spread | 22 | 82% | 258 | +16% | -5,938 | -5,938 | ok |
| vol_shock | call_credit_spread | 1 | 100% | 1,689 | +100% | 1,689 | 1,689 | insufficient |
| vol_shock | iron_condor | 1 | 100% | 1,897 | +89% | 1,897 | 1,897 | insufficient |
| vol_shock | iron_fly | 1 | 0% | -464 | -7% | -464 | -464 | insufficient |
| vol_shock | put_credit_spread | 1 | 0% | -5,866 | -359% | -5,866 | -5,866 | insufficient |

## By structure, all regimes

| family | trades | win rate | mean P&L | total | credit kept | calibration |
| --- | --- | --- | --- | --- | --- | --- |
| call_credit_spread | 73 | 81% | 248 | 18,072 | +15% | 2.02 |
| put_credit_spread | 120 | 77% | -18 | -2,216 | -1% | -0.18 |
| iron_fly | 48 | 19% | -155 | -7,460 | -2% | -3.21 |
| iron_condor | 120 | 68% | -322 | -38,698 | -16% | -1.38 |
| short_strangle | 47 | 70% | -2,873 | -135,045 | -59% | -3.17 |
| short_straddle | 47 | 51% | -3,939 | -185,137 | -17% | -2.58 |

## Top-pick equity curve (per week, cumulative)

| cycle | entry | expiry | regime | structure | P&L | cumulative |
| --- | --- | --- | --- | --- | --- | --- |
| 96 | 2026-03-10 | 2026-03-13 | trend_down | call_credit_spread_S30400C-B30500C_2026-03-13 | 1,570 | -53,995 |
| 97 | 2026-03-17 | 2026-03-20 | trend_up | iron_condor_B30350P-S30450P-S31200C-B31300C_2026-03-20 | 1,960 | -52,034 |
| 98 | 2026-03-24 | 2026-03-27 | trend_up | put_credit_spread_B30400P-S30500P_2026-03-27 | 1,551 | -50,483 |
| 99 | 2026-03-31 | 2026-04-03 | trend_up | put_credit_spread_B30850P-S30950P_2026-04-03 | 1,556 | -48,928 |
| 100 | 2026-04-07 | 2026-04-10 | trend_up | put_credit_spread_B31500P-S31600P_2026-04-10 | 1,594 | -47,333 |
| 101 | 2026-04-14 | 2026-04-17 | trend_up | put_credit_spread_B31950P-S32050P_2026-04-17 | 1,544 | -45,790 |
| 102 | 2026-04-21 | 2026-04-24 | trend_up | call_credit_spread_S32450C-B32550C_2026-04-24 | -5,989 | -51,778 |
| 103 | 2026-04-28 | 2026-05-01 | trend_up | iron_condor_B32800P-S32900P-S33700C-B33800C_2026-05-01 | -5,496 | -57,274 |
| 104 | 2026-05-05 | 2026-05-08 | range_high_vol | put_credit_spread_B30750P-S30850P_2026-05-08 | 1,642 | -55,633 |
| 105 | 2026-05-12 | 2026-05-15 | trend_down | put_credit_spread_B29950P-S30050P_2026-05-15 | 1,606 | -54,026 |
| 106 | 2026-05-19 | 2026-05-22 | vol_shock | put_credit_spread_B30000P-S30100P_2026-05-22 | -5,866 | -59,893 |
| 107 | 2026-05-26 | 2026-05-29 | trend_down | call_credit_spread_S30200C-B30300C_2026-05-29 | 1,608 | -58,285 |
| 108 | 2026-06-02 | 2026-06-05 | trend_down | put_credit_spread_B29750P-S29850P_2026-06-05 | 1,598 | -56,687 |
| 109 | 2026-06-09 | 2026-06-12 | trend_down | put_credit_spread_B29750P-S29850P_2026-06-12 | 1,652 | -55,035 |
| 110 | 2026-06-16 | 2026-06-19 | trend_down | iron_condor_B29350P-S29450P-S30350C-B30450C_2026-06-19 | -5,570 | -60,605 |
| 111 | 2026-06-23 | 2026-06-26 | range_high_vol | put_credit_spread_B30250P-S30350P_2026-06-26 | 1,587 | -59,018 |
| 112 | 2026-06-30 | 2026-07-03 | trend_up | call_credit_spread_S31450C-B31550C_2026-07-03 | 1,549 | -57,470 |
| 113 | 2026-07-07 | 2026-07-10 | trend_up | put_credit_spread_B30800P-S30900P_2026-07-10 | 1,645 | -55,825 |
| 114 | 2026-07-14 | 2026-07-17 | range_high_vol | put_credit_spread_B30050P-S30150P_2026-07-17 | 1,587 | -54,238 |
| 115 | 2026-07-21 | 2026-07-24 | trend_up | call_credit_spread_S31600C-B31700C_2026-07-24 | 1,579 | -52,659 |
| 116 | 2026-07-28 | 2026-07-31 | range_high_vol | put_credit_spread_B30450P-S30550P_2026-07-31 | 1,659 | -51,000 |
| 117 | 2026-08-04 | 2026-08-07 | trend_up | put_credit_spread_B31900P-S32000P_2026-08-07 | 1,694 | -49,306 |
| 118 | 2026-08-11 | 2026-08-14 | trend_up | put_credit_spread_B32400P-S32500P_2026-08-14 | -1,489 | -50,795 |
| 119 | 2026-08-18 | 2026-08-21 | trend_up | put_credit_spread_B32050P-S32150P_2026-08-21 | 1,698 | -49,097 |
| 120 | 2026-08-25 | 2026-08-28 | trend_up | call_credit_spread_S33300C-B33400C_2026-08-28 | 1,622 | -47,475 |

## Evidence table built from this backtest

- evidence source: backtest:synthetic:gbm (SYNTHETIC), shrink 0.3, cap ±15%, min trades 20
-   trend_up/call_credit_spread: 50 trades, kept +24% of credit -> score adjustment +7.1%
-   trend_up/iron_condor: 50 trades, kept -4% of credit -> score adjustment -1.2%
-   trend_up/put_credit_spread: 50 trades, kept -3% of credit -> score adjustment -1.0%
-   range_high_vol/iron_condor: 47 trades, kept -23% of credit -> score adjustment -6.8%
-   range_high_vol/iron_fly: 47 trades, kept -2% of credit -> score adjustment -0.7%
-   range_high_vol/put_credit_spread: 47 trades, kept +1% of credit -> score adjustment +0.3%
-   range_high_vol/short_straddle: 47 trades, kept -17% of credit -> score adjustment -5.0%
-   range_high_vol/short_strangle: 47 trades, kept -59% of credit -> score adjustment -15.0%
-   trend_down/call_credit_spread: 22 trades, kept -7% of credit -> score adjustment -2.1%
-   trend_down/iron_condor: 22 trades, kept -37% of credit -> score adjustment -11.1%
-   trend_down/put_credit_spread: 22 trades, kept +16% of credit -> score adjustment +4.8%

## Warnings

- synthetic index path (seeded GBM) — not a real NIFTY history
- SYNTHETIC history: outcomes are not evidence about the real NIFTY, only a mechanical check of the rules. Supply csv:<path> or a reachable yahoo feed for real results.

## What is real and what is modelled

- **entry_offset_trading_days**: 3
- **iv_premium**: 1.15
- **families**: regime baseline mix
- **sigma_multiple**: 1.0
- **wing_width_steps**: 2
- **lot_size**: 75
- **history_bars**: 1000
- **history_from**: 2022-10-31
- **history_to**: 2026-08-28
- **modelled**: entry premiums (Black-Scholes at the IV estimate)
- **real**: nothing — synthetic path

*Entry premiums are model prices, so this backtest measures the strike/tenor/regime rules against real index outcomes — not the P&L a real book would have booked.*