# Round 9: meta-labeling the sensitive panel on 5 / 15 / 30 minute bars

**Answer.** No pass. A second-stage model that tries to pick which of the panel's signals will pay does not rank signals on new data. The filtered 15-minute rule is the most interesting result of the round (gross +17 to +30 bps per trade in all three test sets), but it is not significant and loses on the unseen coins.

Rules: `PREREG_round9.md`, committed before any code ran. The frozen thresholds (`r9_frozen.json`) were committed before the judges were run.

## What was done (`r9_meta.py`)
- **Primary signals:** the round-7 panel (nine estimators, the score reaches +-4 of 9) with the slow Kalman exit, on 5, 15 and 30 minute bars. Every signal is walked on its own, with no "one position at a time" while building the training data.
- **Selector:** a gradient-boosting model with 25 causal features (score, the nine estimator values, volatility, hour and weekday, the coin's and BTC's slow trend on 60m/240m bars, momentum, range position, volume, funding, open interest, side), trained once on design coins 2021-01..2024-06 to predict each trade's net R. Trade if predicted R ≥ T.
- **Checks:** a no-look-ahead test (features and entries before a cut are identical on cut data, 0 failures).
- **Test sets:** design coins 2025-07..2026-09 (J1), 10 unseen coins (J2), and 10 fresh coins **from 2024-07 only** (J3c). The fresh coins before 2024-07 are left out because a model trained on shared dates leaks (round 8).

## Selection: the model overfits, and the filter still looks good for a year (`r9_select_out.txt`)
Correlation between predicted and realised net R:

| Bar | Training years (in-sample) | Validation year 2024-07..2025-06 | Judges J1 / J2 / J3c |
|---|---|---|---|
| 5 min | +0.376 | +0.057 | +0.014 / +0.046 / +0.030 |
| 15 min | +0.410 | +0.038 | −0.001 / −0.008 / +0.021 |
| 30 min | +0.557 | +0.030 | −0.010 / +0.002 / −0.003 |

On the validation year the filtered trades beat the unfiltered signals, so the thresholds were frozen: 5m T=0.1, 15m T=0.2, 30m T=0.0.

## The judges (`r9_judge_out.txt`), $10 start, 0.5% risk, 8 open
Cells show net R, then $10 → final equity (trades taken, max drawdown), with gross bps per trade.

| Bar | Test set | Unfiltered signals | With the model |
|---|---|---|---|
| 5 | design coins FINAL | −0.197R, $0.00 (37 trades/day) | −0.173R, $6.59 (477, 37%), gross +1.7 bps |
| 5 | 10 unseen coins | −0.148R, $0.00 | −0.103R, $6.62 (745, 37%), +4.2 bps |
| 5 | 10 fresh coins | −0.147R, $0.00 | −0.091R, $5.41 (1,285, 50%), +7.0 bps |
| 15 | design coins FINAL | −0.089R, $0.88 (11 trades/day) | **+0.053R, $10.72** (390, 27%), +22.7 bps |
| 15 | 10 unseen coins | −0.070R, $1.07 | −0.056R, $8.80 (368, 21%), +17.1 bps |
| 15 | 10 fresh coins | −0.054R, $0.37 | **+0.144R, $15.66** (753, 26%), +30.2 bps (t 1.25) |
| 30 | design coins FINAL | −0.028R, $6.19 (5.5/day) | +0.059R, $11.76 (1,029, 36%), +28.9 bps |
| 30 | 10 unseen coins | −0.024R, $5.69 | −0.003R, $8.96 (1,154, 39%), +12.2 bps |
| 30 | 10 fresh coins | −0.025R, $3.43 | −0.006R, $8.31 (2,112, 59%), +16.5 bps |

- **Pass: no for all three.** The 5-minute filter loses everywhere. The 30-minute filter is about zero on two of three sets. The 15-minute filter is positive on two of three (fresh coins +0.14R, t 1.25) and negative on the unseen coins.
- **The model does not rank on the judges.** Realised net R by predicted decile is flat or noisy (top-minus-bottom +0.02, −0.06 and +0.08 at 15 minutes; −0.12 to +0.08 at 30). Its correlation with realised R is within ±0.05 of zero.
- **What the filter really does:** it cuts 11–37 trades a day to 0.9–2.4 a day, so fewer fees are paid, and it mostly re-learns the vote count (round 7's "more votes means a better signal"). Beyond that it adds nothing that survives new data.
- **15-minute, pooled over the three sets** (1,515 trades): net about +0.03R, t about 0.5. Not distinguishable from zero.

## Lower fees (fresh coins, same trades, optimistic)
| Bar | 14 bps (retail taker) | 10 bps | 6 bps | 3 bps |
|---|---|---|---|---|
| 5 min | $5.41 | $6.90 | $8.79 | $10.55 |
| 15 min | $15.66 | $17.60 | $19.77 | $21.58 |
| 30 min | $8.31 | $10.04 | $12.13 | $13.98 |

## What nine rounds say about scalping on this data
- **Taker scalping on 5-30 minute bars does not clear 14 bps.** Setups, indicators, filters, estimators, volume clocks, shocks, analogs and a meta-model were all tried, with pre-registered rules and untouched test sets. Nothing passes.
- **The only things that survive** are slow: the Kalman trend on 2H-4H bars, and a weak 30-minute panel rule that stays near zero.
- **Learned selectors are the repeated trap.** In-sample correlations of 0.14-0.56 (rounds 2, 8, 9) turn into 0.00-0.05 on new data.
- **Biggest lever left is cost**, not signal: at about 6 bps round trip the filtered 15-minute rule would make $19.77 on the fresh coins, but that assumes maker-like fills without the adverse selection the earlier tests measured.

## Files
| Area | Files |
|---|---|
| Plan | `PREREG_round9.md` |
| Code | `r9_meta.py` (candidates, features, test, build, fit, judge) |
| Outputs | `r9_select_out.txt`, `r9_frozen.json`, `r9_judge_out.txt` |
