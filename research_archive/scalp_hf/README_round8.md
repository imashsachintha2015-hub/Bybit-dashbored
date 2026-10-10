# Round 8: three out-of-the-box scalping mechanisms

**Answer.** None of the three produces a scalping edge. Each was chosen to attack a different reason the earlier rounds failed, and each one failed in a way that teaches something.

| Mechanism | Idea | Result |
|---|---|---|
| **SHOCK**: market-wide contagion | When 6-8 of 10 coins jump together in 5 minutes, do coins continue, reverse, or do laggards catch up? | **Price is a martingale after the shock.** Gross edge −3 to +1 bps in every direction. 0 of 12 cells qualified |
| **DC**: a volume clock | Close bars on dollars traded instead of minutes, then run the round-7 estimator panel | Worse than fixed-time bars: gross +15 to +19 bps against +25 to +33. $10 → $9.36 / $8.58 / $13.11 |
| **KNN**: an analog forecaster | Of all past moments that look like now (12-dimensional state, 305,000 examples), what happened in the next 60 minutes? | **No usable information out of sample**: top-minus-bottom prediction quintile is +1.9 to +5.8 bps against a 14 bps fee. No cell qualified |

Rules: `PREREG_round8.md`, committed before any code ran. Each module has a causal test that passed (events, bars and estimator scores do not change when the data is cut).

## SHOCK (`r8_shock.py`, `r8_shock_select_out.txt`)
- **Definition:** per coin, the 5-minute return divided by its trailing 7-day volatility (taken before the bar). An event is 6 (or 8) of 10 coins at z ≥ 2.5 in the same direction, with a 60-minute cooldown. 4,255 events (N=6) and 2,931 (N=8) on the design coins over 5.75 years.
- **Responses tested:** trade every coin with the shock (CONT), against it (FADE), or only the coins that had not yet moved (LAG); hold 15 or 60 minutes; stop 3 ATR.
- **Result:** the gross edge per trade is +0.9 / −3.1 bps for CONT, −1.3 / −0.5 for FADE (N=6, 15 and 60 minutes). The net R is −0.07 to −0.17 in both the tuning years (2021-2024) and the next year. LAG has only 245 trades and −4 to −11 bps.
- **Meaning:** the market absorbs a common shock within the 5 minutes it takes to measure it. There is nothing left to continue or fade.

## DC (`r8_dc.py`, `r8_dc_select_out.txt`, `r8_dc_judge_out.txt`)
- **The clock:** a bar closes when the cumulative quote volume since the last close reaches 1/48 (or 1/96) of the previous 20 days' mean daily volume. BTC: 46 bars a day; duration median 24 minutes, 5th-95th percentile 4-82 minutes.
- **Selection:** the best cell was 48 bars/day with 8 of 9 votes (A+B t 1.9; the same panel on 30-minute time bars had t 2.1).
- **Judges** ($10, 0.5% risk, 8 open):

| Test set | Trades (per day) | Gross | Net R | $10 → (taken, drawdown) | Fixed-time 30m reference |
|---|---|---|---|---|---|
| design coins FINAL | 1,312 (2.9) | +14.8 bps | +0.007 | $9.36 (1,249, 50%) | $10.01 |
| 10 unseen coins | 1,177 (2.5) | +15.2 bps | −0.006 | $8.58 (1,141, 49%) | $10.25 |
| 10 fresh coins | 5,196 (2.5) | +19.1 bps | +0.022 (t 0.7) | $13.11 (5,071, 60%) | $23.87 |

- **Meaning:** letting the estimators see events instead of clock time does not sharpen them. The trade count and hold are the same, and the edge per trade is lower.

## KNN (`r8_knn.py`, `r8_knn_select_out.txt`, `r8_knn_clean_out.txt`)
- **State (12 numbers):** returns over 30 minutes / 2 / 8 / 24 hours (volatility-normalised), short/long volatility ratio, 1-hour taker flow, 4-hour open-interest change, last funding, hour of day (sin, cos), BTC's 2-hour move, distance to the daily VWAP. Prediction = the mean 60-minute return of the 400 most similar past states (305,482 design-coin examples from 2021-01..2024-06).
- **Does the prediction know anything?** Realised 60-minute return by prediction quintile (bps):

| Window | Correlation | Lowest → highest quintile | Top − bottom |
|---|---|---|---|
| training years (in-sample) | +0.143 | −20.7 … +21.1 | +41.8 |
| design coins, next year | +0.027 | −2.1 … +3.8 | +5.8 |
| design coins, 2025-07..2026-09 | +0.007 | −1.5 … +1.2 | +2.7 |
| 10 unseen coins | −0.008 | −3.0 … +0.3 | +3.3 |
| 10 fresh coins, after the training years | +0.003 | −1.0 … +0.9 | +1.9 |
| 10 fresh coins, **inside** the training years | +0.105 | −17.2 … +15.8 | **+33.1** |

- **The last row is a leak, not an edge.** Fresh coins look predictable only in the years the model was trained on, because the "similar past states" include other coins at the same moment, and coins move together. After the training years the same coins show +1.9 bps. This is why a "fresh coins" test that overlaps in time with training cannot be trusted for a learned model.
- **Trading it:** the best case (|prediction| ≥ 25 bps) had +10.6 bps gross and −0.010R net in the first out-of-sample year; ≥ 15 bps had +7.6 bps and −0.033R. No cell qualified, so no judge was run.

## What eight rounds say about scalping
Gross edge before fees per trade, by how long the trade is held (production Kalman rule and the round-7 panel; the 14 bps round trip is the bar):

| Rule | Typical hold | Gross per trade | Net per trade |
|---|---|---|---|
| Setups on 5-15 minute bars (rounds 1-3) | minutes to hours | about 0 to +5 bps | negative |
| Kalman trend, 15-minute bars | 7 h | +9 bps | −0.03R |
| Panel with quick exit, 15-30 minute bars | 2-6 h | +2 to +18 bps | negative |
| Panel with slow trend exit, 30-minute bars | 17 h | +25 to +33 bps | +0.02 to +0.06R |
| Kalman trend, 60-minute bars | 27 h | +28 to +43 bps | +0.04R |
| Kalman trend, 120-minute bars | 58 h | +94 to +115 bps | +0.15R |

The edge per trade grows with the hold, roughly in step with the size of the trend being ridden, and the fee is a fixed 14 bps. Fast ideas keep shrinking the edge toward zero, whatever the signal (patterns, filters, estimators, shocks, clocks, analogs).

## Files
| Area | Files |
|---|---|
| Plan | `PREREG_round8.md` |
| Code | `r8_shock.py`, `r8_dc.py`, `r8_knn.py` |
| Outputs | `r8_shock_select_out.txt`, `r8_dc_select_out.txt`, `r8_dc_judge_out.txt`, `r8_knn_select_out.txt`, `r8_knn_clean_out.txt`, `r8_*_frozen.json` |
