# Round 7: a panel of nine sensitive estimators on 15 and 30 minute bars

**Answer.** On 30-minute bars a panel of very responsive estimators voting together gives the first 15-30 minute strategy in this project that is **non-negative on all three untouched test sets**, but it does not meet the pre-registered pass rule. On 15-minute bars nothing works.
- **30 minutes:** about 2.2 trades per day on 10 coins (about 6.6 per day on 30 coins), hold about 17 hours, win rate 36%. $10 → $10.01 / $10.25 / **$23.87** on the three test sets; net +0.02 / +0.04 / +0.06R per trade.
- **Why it is not a pass:** significance is weak (t 0.2 to 1.5, rule needs 2.0 on the fresh coins) and the drawdown at 0.5% risk is 41-58% (the rule allows 40%). At 0.25% risk the drawdown is 23-35% but the profit nearly vanishes ($10.34 / $10.38 / $17.17).
- **15 minutes:** the edge before fees is +0.4 to +13 bps per trade against 14 bps of cost. $10 → $2.79 / $4.04 / $3.00.
- It improves on the plain Kalman at the same speed. The plain 30-minute Kalman made $3.81 / $5.30 / $8.19 on the same sets.

Rules: `PREREG_round7.md`, committed before any code ran. Addendum 1 (the slow-exit cells) was written after seeing the first selection grid and before any judge was run; this is disclosed because it widens the choice of cells.

## The panel (`r7_sens.py`)
Nine causal estimators, each producing a normalised trend score, and a vote when the score passes a threshold:
Kalman local-linear trend, Ehlers SuperSmoother, Kaufman adaptive MA, Laguerre filter, Hull MA, zero-lag EMA, Ehlers **MAMA/FAMA** (Hilbert-transform adaptive), **Fisher transform** of the price position, and **taker order flow** (smoothed buy/sell imbalance, not a price filter).
The score S is the sum of the 9 votes (−9 to +9). Entry when S reaches +K or −K, with the production BTC-trend and funding rules, stop 3 ATR, 14 bps + real funding. A no-repaint test confirms every estimator's output before a cut-off is identical when computed on the cut data (`python3 r7_sens.py test`: 0 failures).

## What selection found (design coins 2021-01..2025-06, `r7_select_out.txt`, `r7_select_out_v2.txt`)
- **Single estimators alone** (quick entry and exit): all lose after fees. Gross edge per trade is tiny (0 to +5 bps at 15 minutes; +1 to +16 bps at 30 minutes). Only Kalman (+11 bps) and MAMA (+16 bps) at 30 minutes are close.
- **Panel with the panel's own exit** (24 cells): at 15 minutes none qualified; at 30 minutes the best was +0.03R in the tuning years and about 0 afterwards. Sensitive exits cut trades short (2-6 hours) so the gain per trade shrinks below the fee.
- **Panel entry + slow Kalman exit** (12 added cells): the hold rises to 15-18 hours and the gross edge to +20 to +38 bps. Best at 30 minutes: THETA 1.0, K 8 of 9, exit when the slow Kalman trend turns (A+B t 2.1, $10 → $39 on selection, drawdown 27%). Best at 15 minutes: the same (t 1.2).
- So the exit still uses a Kalman; the new part is the nine-estimator entry.

## The judges (`r7_judge_out.txt`), $10 start, 0.5% risk, 8 open
| Bar | Test set | Trades (per day) | Gross | Net R | $10 → (trades taken, drawdown) |
|---|---|---|---|---|---|
| 30 | design coins FINAL | 1,106 (2.4) | +25.1 bps | +0.020 (t 0.2) | $10.01 (1,051, 50%) |
| 30 | 10 unseen coins | 1,098 (2.3) | +33.2 bps | +0.039 (t 0.4) | $10.25 (1,059, 41%) |
| 30 | 10 fresh coins 2021-26 | 4,462 (2.2) | +31.0 bps | +0.059 (t 1.5) | $23.87 (4,347, 58%) |
| 15 | design coins FINAL | 2,247 (4.9) | +0.4 bps | −0.102 | $2.79 (2,140, 87%) |
| 15 | 10 unseen coins | 2,173 (4.5) | +4.8 bps | −0.073 | $4.04 (2,078, 74%) |
| 15 | 10 fresh coins | 8,825 (4.3) | +13.0 bps | −0.005 | $3.00 (8,602, 92%) |

- With +2 bps slippage per side the 30-minute cell is −0.003 / +0.024 / +0.043R.
- If the round trip cost 10 / 6 / 3 bps (optimistic, same fills), the 30-minute cell gives $11.27 / $11.10 / $34.29, then $12.69 / $12.02 / $49.25, then $13.88 / $12.75 / $64.61.
- **Maker follow-up** (`r7_maker_out.txt`, limit entry at the signal close, 2 bps): 82-87% fill; net +0.028 / +0.040 / +0.059R; $10.05 / $10.43 / $19.90. No better than taker: orders that miss are the strongest continuations, as before.

## Risk and activity of the 30-minute cell (`r7_risk_out.txt`)
| Risk per trade | design coins FINAL | unseen coins | fresh coins |
|---|---|---|---|
| 0.15% | $10.29 (DD 18%) | $10.30 (14%) | $14.21 (22%) |
| 0.25% | $10.34 (28%) | $10.38 (23%) | $17.17 (35%) |
| 0.50% | $10.01 (50%) | $10.25 (41%) | $23.87 (58%) |

About 66 trades a month on 10 coins (about 200 on 30), median hold 16 hours, 36% win rate. For comparison the plain 30-minute Kalman at 0.15% risk: $7.85 / $8.56 / $10.99.

## What this means
- Sensitivity alone does not create profit: it shortens trades, and below about half a day a trade does not move far enough to repay 14 bps.
- The useful part is the pairing: **sensitive nine-way entry, slow trend exit**. That is the first 30-minute rule here that stays non-negative on unseen time, unseen coins and fresh coins.
- It is a weak edge (about +20 to +30 bps net-of-nothing, +0.02 to +0.06R), so it needs low fees and small risk to be worth running. I would not put it live. A paper forward test would be the next step.
- Fifteen-minute rules remain unprofitable after fees in every round.

## Files
| Area | Files |
|---|---|
| Plan | `PREREG_round7.md` (+ Addendum 1) |
| Code | `r7_sens.py` (estimators, test, build, select, judge), `r7_maker.py`, `r7_risk.py` |
| Outputs | `r7_select_out.txt`, `r7_select_out_v2.txt`, `r7_frozen.json`, `r7_judge_out.txt`, `r7_maker_out.txt`, `r7_risk_out.txt` |
