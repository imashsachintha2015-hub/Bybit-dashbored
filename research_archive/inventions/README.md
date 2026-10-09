# Invention lab: new trend measures, tested against the Kalman trend

**Protocol.** Every rule and threshold was written down before the first run (`PREREG_inventions.md`).

**Data.** OKX 1H candles (2019-12 to 2026-09) aggregated to 4H bars, all coins, trading both long and short (mirrored prices).

**Execution.**
- Entry at the next open after the signal.
- Protective stop at 3 ATR.
- Exit when the invention's own trend measure falls below 0, or after 120 bars at most.
- Costs: 14 bps per round trip plus funding.

**Checks** (`inv_core_out.txt`).
- No repainting: values at 24 cut-off points, for each of the 6 estimators, match the full run (0 mismatches).
- Mirrored prices give an exactly mirrored trend.
- The benchmark matches `trendlines/tl_core.kalman_trend` exactly.

| invention | idea |
|---|---|
| NEWTON | constant-acceleration Kalman (level, velocity, acceleration); enter when velocity z > 1 and accelerating |
| IMM | slow and fast Kalman trends mixed by Bayesian model probabilities |
| SWARM | market breadth: enter only when 60%+ of coins trend the same way |
| HURST | persistence gate: variance ratio of 6-bar vs 1-bar returns >= 1.1 |
| ENTROPY | order gate: permutation entropy below its own 30th percentile |
| VOLCLOCK | Kalman running on activity time (volume clock) |
| ELASTIC | rubber band: enter on a 1-sd stretch below the Kalman level inside a strong up-trend |
| PATH | least resistance: enter only when less volume sits in the 3 ATR above price than in the 3 ATR below |

## Results (`inv_run_out.txt`)

**Selection on DEV (2021-23, design coins).** The only free choice was whether to apply the BTC filter. DEV chose it for KALMAN, NEWTON, IMM, VOLCLOCK, ELASTIC and PATH.

**Holdouts.** Average net R per trade; trade counts in brackets. The benchmark is KALMAN with the BTC filter.

| invention | VAL 2024 | FINAL 2025-26 | UNSEEN coins | OLD 2020-21 | beats bench | pooled | overlap with Kalman | $10 at 0.5% x 8, 2020-21 / 2024-26 |
|---|---|---|---|---|---|---|---|---|
| KALMAN+BTC (bench) | +0.434 (402) | +0.017 (857) | +0.103 (1448) | +0.980 (400) | - | +0.235 t=2.8 | - | $26.92 / $17.07 |
| PATH+BTC | +0.470 | +0.054 | +0.137 | +0.865 | 3/4 | +0.246 t=3.4 | 95% | $28.02 / $15.81 |
| NEWTON+BTC | +0.339 | +0.095 | +0.133 | +0.587 | 2/4 | +0.212 t=3.2 | 51% | $18.53 / $17.19 |
| IMM+BTC | +0.311 | +0.027 | +0.072 | +0.543 | 1/4 | +0.152 t=2.5 | 65% | $22.98 / $18.36 |
| VOLCLOCK+BTC | +0.325 | -0.008 | +0.068 | +0.590 | 0/4 | +0.151 t=2.3 | 76% | $16.42 / $14.96 |
| SWARM | +0.257 | -0.043 | +0.052 | +0.480 | 0/4 | +0.108 t=2.0 | 96% | $31.76 / $9.39 |
| ENTROPY | +0.373 | -0.098 | +0.014 | +0.524 | 0/4 | +0.093 t=1.7 | 96% | $20.98 / $10.68 |
| ELASTIC+BTC | +0.180 | -0.026 | -0.006 | +0.393 | 0/4 | +0.065 t=1.3 | 0% | $15.12 / $7.72 |
| HURST | +0.481 | -0.118 | +0.036 | +0.189 | 1/4 | +0.052 t=0.8 | 97% | $11.35 / $12.06 |

## Verdict
- **No invention beat the benchmark in all four holdouts.**
- **PATH** (volume overhead filter) beat it in 3 of 4 and had the best pooled result. It is a refinement of the Kalman entry, not a new signal.
- **NEWTON** brings genuinely different trades: only half of its trades are also Kalman trades. It was the best invention in the hard recent period (FINAL 2025-26: +0.095R vs +0.017R) and on unseen coins, but weaker in the 2020-21 bull run.
- **Gates that only look at "how random the market is"** (HURST, ENTROPY) and pullback entries (ELASTIC) do not add value.
- **The BTC-trend filter** improved almost every trend measure on DEV.
