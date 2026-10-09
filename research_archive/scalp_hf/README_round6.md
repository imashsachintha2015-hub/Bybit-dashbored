# Round 6: the fastest version of the trend rule that still pays

**Answer.** True scalping (bars of 15 to 60 minutes) loses money, even when the rule is tuned for the bar size. The fastest speed that held up on coins it was not tuned on is **2-hour bars**, which is about 1 trade per day across 10 coins.
- **15 minutes** (7.9 trades/day): no setting worked on the tuning years. The production rule turned $10 into **$0.26** on 10 fresh coins, with 15,187 trades taken.
- **30 and 60 minutes:** settings that looked good on 2021-2025 (+0.06 to +0.09R) went to −0.06 to +0.02R on the later and fresh coins. $10 → $4.51 to $12.99 with drawdowns of 55-77%.
- **120 minutes:** positive in all three judges. The unchanged production setting met every pass criterion on the fresh coins ($10 → **$27.21**, 2,067 trades, +0.145R, t 2.4, drawdown 35%).
- **Lower fees do not rescue the fast bars.** Even at a 3 bps round trip, 15 minutes makes only +0.04R per trade, 66% drawdown, and loses on the unseen coins.

Rules: `PREREG_round6.md`, committed before any code ran. The frozen cells (`r6_frozen.json`) were committed before the judges were run.

## Setup
The 4H Kalman trend rule of `hf_ladder.py` made parameterised (the runner is checked to reproduce production exactly at 240 and 60 minutes). For bar sizes 15, 30, 60 and 120 minutes, 12 settings each (drift LAM 1e-4 or 1e-3, entry threshold Z_IN 1.0 / 1.5 / 2.0, stop 2 or 3 ATR) were run on 30 coins with 14 bps + real funding, $10, 0.5% risk, 8 open at most.
Selection used 10 design coins in 2021-01..2025-06. The judges were run once: J1 design coins 2025-07..2026-09; J2 10 unseen coins; J3 10 fresh coins (2021-2026).

## Selection (`r6_select_out.txt`)
| Bar | Qualifying settings | Frozen | Selection result (A+B) |
|---|---|---|---|
| 15 min | none (all 12 negative) | — | — |
| 30 min | 5 of 12 | LAM 1e-4, Z 2.0, stop 3 | +0.09R / +0.11R, t 2.1 |
| 60 min | 12 of 12 | LAM 1e-3, Z 1.0, stop 3 | +0.07R / +0.09R, t 2.2 |
| 120 min | 12 of 12 | LAM 1e-4, Z 2.0, stop 3 | +0.20R / +0.33R, t 2.7 |

## The judges (`r6_judge_out.txt`)
Each cell shows net R, then $10 → final equity (trades taken, max drawdown). Trades per day are for 10 coins together.

| Bar | Setting | Trades/day | J1 design FINAL | J2 UNSEEN | J3 FRESH COINS 2021-26 | Pass |
|---|---|---|---|---|---|---|
| 15 | production | 7.7-8.6 | −0.081R, $1.85 (3,585, 91%) | −0.054R, $2.86 (3,466, 79%) | −0.029R, $0.26 (15,187, 99%) | no |
| 30 | frozen | 3.4-3.7 | −0.061R, $5.31 (1,575, 75%) | −0.069R, $4.51 (1,558, 71%) | +0.014R, $7.85 (6,590, 77%) | no |
| 30 | production | 3.9-4.3 | −0.084R, $3.81 (1,804, 79%) | −0.040R, $5.30 (1,743, 67%) | +0.014R, $8.19 (7,546, 79%) | no |
| 60 | frozen | 3.0-3.3 | −0.012R, $7.97 (1,373, 58%) | −0.012R, $6.95 (1,357, 55%) | +0.017R, $12.99 (5,865, 64%) | no |
| 60 | production | 1.9-2.2 | −0.024R, $8.44 (903, 51%) | +0.023R, $9.20 (876, 47%) | +0.036R, $11.26 (3,867, 52%) | no |
| 120 | frozen | 0.9-1.0 | +0.176R, $12.89 (413, 21%) | +0.115R, $11.94 (410, 22%) | +0.117R, t 1.86, $22.07 (1,728, 34%) | no (t < 2.0) |
| 120 | production | 1.0-1.1 | +0.194R, $13.87 (459, 20%) | +0.103R, $11.88 (451, 21%) | +0.145R, t 2.41, $27.21 (1,933, 35%) | not frozen |

- **No pre-registered cell passed.** The frozen 120-minute cell missed the J3 t ≥ 2.0 rule (1.86) while being positive in all three judges.
- **The unchanged production setting at 120 minutes** met every pass criterion, but it was not the cell selection froze, so I count it as supporting evidence, not as a pass.
- The 4H result from round 5 for comparison: J3 $19.85 (979 taken, drawdown 19%), J1 $12.94, J2 $10.37. 2H trades about 4x as often and has a larger drawdown (35% against 19%).

## Do lower fees help? (`r6_fees_out.txt`, optimistic: same fills, cost refunded)
| Bar | 14 bps (retail taker) | 10 bps | 6 bps | 3 bps |
|---|---|---|---|---|
| 15 min, fresh coins | $0.26 | $1.86 | $13.33 (77% DD) | $58.28 (66% DD), unseen $8.42 |
| 30 min | $8.19 | $16.26 | $32.25 | $53.90 (65% DD), unseen $7.75 |
| 60 min | $11.26 | $14.30 | $18.17 | $21.75, unseen $10.50 |
| 120 min | $27.21 | $29.55 | $32.09 | $34.13, unseen $12.47 |

At 3 bps the fast bars show large numbers, but with 65% drawdown and a loss or flat result on the unseen coins. The edge per trade shrinks with speed (+0.145R at 120 minutes, +0.04R at 15 minutes at 3 bps), so fees are only part of the problem. A fee tier near 3 bps (the Bybit VIP / market-maker tiers) combined with fills at the signal price would be needed, and the earlier maker tests found the fills are worse than assumed here.

## What this means for scalping
- **Real scalping in the sense of minutes per trade has no edge** in any of the six rounds, on trend rules or on setups.
- **The fastest rule that holds up is the 2H Kalman trend.** About 1 trade per day on 10 coins, 3 per day on 30 coins; trades last hours to a day or two.
- **Do not add a faster mode to the live system.** The trend works because each trade moves a lot compared with the fee. Going faster shrinks that move faster than it shrinks the fee.
- The live engine currently uses 4H bars. Running it on 2H bars would be a configuration change plus the larger drawdown above; I have not changed it.

## Files
| Area | Files |
|---|---|
| Plan | `PREREG_round6.md` |
| Code | `r6_fast.py` (parity, build, select, judge), `r6_fees.py` |
| Outputs | `r6_select_out.txt`, `r6_frozen.json`, `r6_judge_out.txt`, `r6_fees_out.txt` |
