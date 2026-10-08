# Scenario setup research (run of research_archive/PROMPT_scenario_setup_research.md)
Data: OKX 1H, 32 USDT perps, 2024-04-09 .. 2026-09-30; 1D bias from closed days; BTC 1D trend as context. Longs + mirrored shorts.
Split: 19 design coins / 13 unseen coins; dev < 2025-07-11 <= val < 2026-02-19 <= final. Costs 14 bps market / 8 bps limit + funding 0.5 bp per 8h.
Limit fills need 0.05 ATR trade-through; fill bar can only stop out; stop-first on ambiguous bars.
Scenarios / setups (19): fib pullbacks 0.382/0.5/0.635 (golden pocket)/0.705 (OTE)/0.786; impulse fixed-range POC, VAL; anchored VWAP from the swing low;
range fade, range breakout with volume, accumulation spring, range sweep-reclaim; reversal CHoCH (market and retest), RSI divergence;
equal-lows stop hunt; body fakeout; AMD-FVG (frozen PRIMARY, NO_GATE). Variants: 1D bias any/with/against x BTC any/aligned x target 2R/structural/extension = 252 configs.
Results: 0 configs with DEV t>=3 (luck expects 0.3). Frozen DEV-best per setup, one look at FINAL + UNSEEN: 0 of 19 pass the prompt's bar.
Closest: AMD_NO_GATE (limit at FVG 50%, 2R): FINAL +0.129R (n=227), UNSEEN +0.138R (n=657, t=1.9), random control -0.097R, flipped -0.102R,
+0.087R even at 20 bps; $10 -> $46.83 (628 trades, max DD -36%); fails only "positive in 3 of 4 unseen quarters" (2 of 4).
Liquidity sweeps / stop hunts / range-low fades / RSI divergence lose (unseen -0.11..-0.36R); flipping them (trading continuation) also loses.
Golden pocket / OTE / fib pullbacks with the 1D trend: about 0R after costs. Note: a flipped-direction control taken at the signal bar is
look-ahead-biased for limit setups (only trades that later filled are included); controls are taken at the fill bar.
Not covered: 15m trigger (see mtf_confluence, failed), footprint/order flow (tick data), "PCI" (undefined).

## Round 2: invented tools and situations (scen2.py; same data, split and costs)
New Fibonacci-like tools: VFIB (volume-Fibonacci: limit at the price below which (1-r) of the impulse's volume traded), TFIB (time-Fibonacci:
enter when the pullback has lasted r x the impulse's duration), LFIB grid (entry depth 0.30..0.90 x stop below the low or 0.15 deeper),
SBGZ VU ladder (4.0/4.7/5.7 VU), harmonics (Gartley, Bat, AB=CD 1.0/1.272). New situations: CME weekend gap (Friday CME close -> Sunday CME
open, US DST aware), Dalton 80% value-area rule, previous day/week high-low sweeps and breakouts, Monday weekend-range breakout / failed break,
round-number sweeps. 702 configs; 0 with DEV t>=3 (luck expects 0.9); 0 of 48 frozen configs pass (scen2_an_out.txt, scen2_final_out.txt).
Leads taken forward: CME gap fill (FINAL +0.28R n=25, UNSEEN +0.15R n=121) and previous-week-low sweep (FINAL -0.13R, UNSEEN +0.48R).
Clear losers: TFIB, round-number sweeps, Gartley/Bat. Data-created situations (scen_cluster.py: k-means K=30 on 15 market-state features,
fitted on DEV only) fail on FINAL (scen_cluster_out.txt). CME placebo (cme_check.py): weekend gaps fill within 24h far more often than
same-size mid-week windows, but trading toward the fill nets about 0R after costs (cme_d4_out.txt).

## Pre-registered test on never-seen data, 2021-06-01 .. 2024-04-09 (PREREG_old_period.md, written before the download; prereg_run.py)
fetch_old.py pages OKX history-candles back from the start of the 2024-26 files (contiguous, no missing hours). 30 coins (ONDO, POL had no
data then; survivorship bias: today's coin list). One look. Pass = avg net R > 0 and t >= 2.0 (strict Bonferroni for 6 tests: t >= 2.6).

| test (frozen) | 2024-26, where found | 2021-24, never seen | $10 at 2% risk (trades taken) | verdict |
|---|---|---|---|---|
| 1 CME gap fill, gap >= 1%, stop 1x gap, coin 1D against, BTC aligned | n=271 +0.150R t=1.1 | n=133 -0.288R t=-2.1 | $6.45 (92), DD -41% | FAIL |
| 2 CME gap fill, gap >= 2%, stop 2x gap, no filters | n=2272 +0.050R t=1.5 | n=1864 -0.037R t=-1.0 | $8.58 (442), DD -36% | FAIL |
| 3 weekend vs placebo, filled within 24h (gap >= 1%) | 44% vs 25%, t=5.9 | 42% vs 28%, t=4.9 | - | PASS strict (real effect, not tradeable) |
| 4 AMD_NO_GATE | n=1559 +0.158R t=2.6 | n=1375 +0.120R t=2.3 | $59.31 (904), DD -59% | PASS (not strict) |
| 5 PWL sweep + reclaim, coin 1D with, BTC aligned, target PWH | n=637 +0.255R t=1.2 | n=552 -0.081R t=-0.6 | $2.57 (428), DD -82% | FAIL |
| 6 AMD_PRIMARY (reference) | n=1103 +0.232R t=3.0 | n=930 +0.137R t=2.1 | $29.63 (715), DD -58% | PASS (not strict) |

AMD-FVG by year (avg net R per trade): NO_GATE 2021 +0.29, 2022 +0.31, 2023 -0.08, 2024 +0.04, 2025 +0.38, 2026 +0.01;
PRIMARY 2021 +0.25, 2022 +0.34, 2023 -0.08, 2024 -0.03, 2025 +0.51, 2026 +0.18. Still positive at 20 bps (+0.08 / +0.09R on 2021-24).
At 1% risk: NO_GATE $10 -> $27.39 on 2021-24 (904 trades, DD -35%), $93.22 on 2021-26 (1,803 trades, DD -41%); 2% risk doubles the drawdown.
Every CME trade variant is negative on 2021-24 (-0.07 .. -0.25R, cme_d5_out.txt) although the weekend fill-rate effect holds.

## Does the in-sample ranking predict anything? (meta_rank.py, descriptive, selects nothing)
934 configs of rounds 1 + 2 with >= 60 trades in both periods. Rank correlation of avg net R, 2024-26 vs 2021-24: +0.54, driven by losers
staying losers (bottom 20 by 2024-26 t: -0.18R -> -0.17R, 0 of 20 positive). Winners do not repeat: top 20 by 2024-26 t: +0.18R -> -0.06R,
5 of 20 positive. The best family on 2021-24 averages +0.02R. Lesson: a grid search over chart patterns reliably finds what loses
(costs on tight stops, counter-trend sweeps) and finds winners mostly by luck; only AMD-FVG held up on a third period.
