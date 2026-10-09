# Scalp study, round 2: 25 structural setups tested by market situation

**Answer.** Still no profitable scalping setup. Round 2 tested 3,200 setup cells, situation by situation:
- 25 trader-style setups: breakouts, fakeouts, liquidity sweeps, Fibonacci pullbacks, VWAP / EMA / Supertrend / MACD continuation, CHoCH and divergence reversals, volume-profile fades, and a custom confluence-level bounce.
- On 5m and 15m bars, long and short, in UP, DOWN, RANGE and ALL regimes, with and without a volume filter, and with 4 exits.

Results:
- **0 of 3,200 cells passed the DEV screen.** The best DEV t was +1.1.
- The best cell of each situation, frozen on DEV and run once, lost money in 46 of 48 cases in FINAL. The 2 that ended above $10 lost on the unseen coins.
- A gradient-boosting model trained on every event lost in every period.
- Taking the setups only in the direction of the working 4H Kalman trend also failed.

**Why.** Before costs, the 25 setups together have no edge at any stop width (−0.005R, t −1.0), the same as random entries. They do not predict the next few hours' direction on 5m/15m crypto bars. The best single setups make about +5 bps per trade, against a 14 bps round-trip cost.

Rules and pass criteria were written before any round-2 run (`PREREG_round2.md`, Addendum 1 in `hf2_setups.py`). Addendum 2 (the 4H-aligned test) was written after the DEV screen but before any of its new cells were run. The data, periods, coins, costs and $10 portfolio are the same as in round 1 (`README.md`).

## What was built
- **Levels and tools, all causal** (`hf2_feat.py`):
  - previous-day high, low and close; previous-week high and low;
  - Asia session range;
  - daily and weekly anchored VWAP with standard-deviation bands;
  - previous-day volume profile (POC, VAH, VAL);
  - Fibonacci 0.5 / 0.618 / 0.786 / 1.272 levels of the last swing leg;
  - round numbers;
  - a custom confluence score: how many levels sit within 0.25 ATR of a price;
  - swing pivots, CVD (taker delta), open-interest change, Supertrend, MACD, Bollinger / Keltner squeeze, ADX, RSI and relative volume.
- **Regime tags** from the 1H chart:
  - UP: close > EMA200, EMA50 > EMA200, EMA50 rising, ADX ≥ 20;
  - DOWN: the mirror;
  - RANGE: ADX < 20.
- **Setups** (`hf2_setups.py`): structural stops (behind the wick, swing or range) and level targets.
- **Exits:** 1.5R target, 3R target, level target, or a 3-ATR chandelier trail. Each trade is walked on 1m bars, stop first.
- **Checks** (`test_round2.py`, all pass):
  - nothing repaints: events and levels are identical when the data is cut mid-bar;
  - PDH / PDL / POC / VWAP match a direct computation;
  - every stop, target and time exit is consistent;
  - random entries lose exactly their cost (gross −0.02 to +0.05R).

## Results by situation (best DEV cell per situation, regime and side; frozen; run once)
Summary per situation over its 8 frozen cells. FINAL is 2025-07..2026-09 on the design coins; UNSEEN is the same months on 10 other coins. Every run starts at $10 with 0.5% risk per trade. The full 48-row table is `r2_summary.md`; full period-by-period output is `r2_final_out.txt`.

| Situation | Setups in it | FINAL $10 → median (best / worst) | FINAL net R median | FINAL trades | UNSEEN $10 → median (best) | Cells above $10, FINAL / UNSEEN |
|---|---|---|---|---|---|---|
| Breakouts | Donchian, PDH/PDL, Asia range, retest, squeeze, ORB | $6.38 ($7.81 / $1.92) | −0.22R | 5,196 | $7.17 ($8.87) | 0 / 0 |
| Fakeouts | failed Donchian break, failed PDH/PDL break | $7.82 ($9.00 / $4.19) | −0.18R | 2,432 | $7.60 ($9.14) | 0 / 0 |
| Liquidity hunts | PDH/PDL sweep, Asia sweep, equal highs/lows, swing stop-hunt | $4.19 ($5.91 / $0.98) | −0.45R | 4,361 | $5.22 ($9.02) | 0 / 0 |
| Trend continuation (long / short trend) | Fib 0.5–0.786 pullback, VWAP, EMA, Supertrend, MACD | $7.72 ($10.39 / $5.24) | −0.13R | 3,673 | $8.00 ($9.49) | 1 / 0 |
| Trend reversals | CHoCH, RSI and CVD divergence, VWAP exhaustion, OI flush | $8.48 ($9.12 / $7.37) | −0.20R | 2,016 | $8.75 ($9.83) | 0 / 0 |
| Range / custom levels | volume-profile fade, confluence bounce, Bollinger fade | $6.01 ($10.60 / $1.50) | −0.17R | 6,396 | $6.67 ($10.31) | 1 / 1 |

By regime, FINAL median of the frozen cells:

| Regime | FINAL $10 → median | Net R median |
|---|---|---|
| UP trend | $7.57 | −0.18R |
| DOWN trend | $6.84 | −0.26R |
| RANGE | $5.98 | −0.15R |
| ALL | $4.99 | −0.19R |

The ALL cells trade the most, so they lose the most.

**The two cells that ended above $10 in FINAL were noise.**
- Fib pullback long (15m, 3R): $10.39 on +0.03R. It lost on VAL (−0.12R), on unseen coins ($7.40) and on 2021-22 ($6.46).
- Confluence bounce long in UP trends: $10.60. It lost on VAL, on unseen coins ($6.59) and on 2021-22 ($5.96).

With 48 frozen cells, about 2 finishing above $10 by chance is what no edge looks like.

## Why nothing works: edge before costs (DEV, `r2_gross_dev.txt`)
| Stop width | Setups: gross R | Random entries: gross R | Setups: net R |
|---|---|---|---|
| 0.15–0.30% | −0.005 (t −1.0) | +0.017 | −0.76 |
| 0.30–0.60% | −0.008 (t −1.6) | −0.001 | −0.34 |
| 0.60–1.20% | +0.001 (t +0.2) | +0.025 | −0.17 |
| 1.20–4.0% | −0.008 (t −1.0) | +0.041 | −0.09 |

- Wider stops only shrink the cost in R. They do not create an edge, because there is none before costs.
- The strongest single setups on DEV:
  - failed PDH/PDL break (FAKE_PD): +0.08R, +4.8 bps per trade, t 2.6;
  - CVD divergence on 15m: +0.06R with the trail, t 2.4;
  - Bollinger fade with the trail on 5m: +0.05R, t 4.7.
- None of these survived costs. Their 5 bps are a third of the 14 bps taker round trip. Even a maker-only round trip at 4 bps would leave about 1 bp.

## The kitchen sink: gradient boosting on every event (`hf2_ml.py`, `r2_ml_out.txt`)
Setup: about 900k DEV events with 26 features (setup, regime, levels, confluence, VWAP distance, OI, funding, BTC moves, hour, …). The model was trained on DEV, then on DEV+VAL, and kept the top 10% of predictions.
- It improved the average trade from −0.45R to −0.10R, mainly by picking wide-stop events.
- Its own cut-off stayed below zero: it never expected a profit.

| Exit | VAL | FINAL | Unseen coins | 2021-22 |
|---|---|---|---|---|
| 1.5R target | −0.105R, $10 → $0.03 (16,204 trades) | −0.107R, $10 → $0.05 (13,626) | −0.083R, $10 → $0.02 (21,354) | −0.058R, $10 → $0.00 (45,994) |
| Level target | −0.105R, $10 → $0.02 (16,518) | −0.111R, $10 → $0.04 (14,067) | −0.088R, $10 → $0.04 (22,004) | −0.059R, $10 → $0.00 (45,042) |

## Created setup: scalps in the direction of the 4H Kalman trend (Addendum 2, `hf2_stageB.py`, `r2B_out.txt`)
The idea: the only edge we have is the 4H trend, so use 5m/15m setups as scale-in entries while the production 4H Kalman rules hold a trade in the same direction.
- 16% of setup events qualify.
- 0 of 800 cells passed the DEV screen.
- Pooled, the aligned events make +0.1 to +1.4 bps per trade before costs. The 4H drift over a few hours is far too small to pay 14 bps.
- 11 of the 12 frozen best cells lost in FINAL ($10 → $0.59 to $9.49).
- The 12th was Supertrend flip long, 15m, 3R target, volume filter. It ended at $10.48 in FINAL (+0.015R, t 0.1, 276 trades) and $11.91 on unseen coins (+0.13R, t 1.2). But it lost on DEV (−0.08R) and VAL (−0.12R), was flat on 2021-22, had 3 of 4 FINAL quarters negative, and went negative with +2 bps of slippage. This is not a pass. It is what one lucky cell out of 12 looks like.

## Where this leaves scalping (both rounds)
- Over 4,000 rule variants were tested on 1m, 5m and 15m data:
  - round 1: 262 signal cells plus the frequency ladder;
  - round 2: 3,200 setup cells, 800 trend-aligned cells and a machine-learning model.
- Not one beats a 14 bps round trip out of sample. The setups traders use (sweeps, fakeouts, Fib, VWAP, CHoCH, volume profile) are, on this data, no better than random entries before costs.
- What does work in this project is slower: the production Kalman trend on **4H and 2H** bars. In round 1's ladder it was profitable in every period and on unseen coins, with typical moves of hundreds of bps against the same 14 bps.
- If faster trading is still wanted, the first thing that has to change is the cost, not the setup:
  - a fee tier or rebate that brings a round trip near 2–4 bps;
  - and proof from live fills that limit orders are not adversely selected.

  Without that, the arithmetic of round 1 still holds.

## Files
| Area | Files |
|---|---|
| Plan | `PREREG_round2.md` (plan, Addendum 2) |
| Code | `hf2_feat.py` (bars, levels, regimes); `hf2_setups.py` (setups, Addendum 1, trade walk, event builder); `hf2_eval.py` (DEV screen, VAL confirmation, frozen runs); `hf2_ml.py`; `hf2_gross.py`; `hf2_stageB.py`; `r2_summary.py` |
| Tests | `test_round2.py` |
| Outputs | `r2_screen_out.txt`, `r2_dev_table.tsv` (all 3,200 DEV cells); `r2_frozen.json`; `r2_final_out.txt`; `r2_summary.md`; `r2_gross_dev.txt`; `r2_ml_out.txt`; `r2B_out.txt`, `r2B_dev_table.tsv` |
| Events | the per-event files (`ev_COIN_TF.npz`, 440 MB) are in the scratchpad, not in git; `python3 hf2_setups.py COIN,...` rebuilds them |
