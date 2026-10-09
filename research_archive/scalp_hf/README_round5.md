# Round 5: where do trends pay? Trend-birth indicators, a situation map, and level add-ons

**Answer.** The trend is where the money is, and it held up on coins it never saw, but I did not find a trend *situation* or *level* that adds profit beyond the trend itself.
- **The 4H Kalman trend works on fresh coins.** On 10 coins that no trend rule had touched (AAVE BCH CRV ETC FIL HBAR ICP RUNE TRX UNI, 2021-01..2026-09) it took 1,041 trades at +0.172R and made $10 into **$19.85** (979 trades taken, max drawdown 19%, t 2.1).
- **The situation I found did not hold up.** In 2021 to mid-2024, trends that were young (little move since birth) or coiled (tight range before) paid +0.55 to +0.70R. In the judges the same groups paid +0.11R against +0.29R for everything else.
- **The level add-on is leverage, not a level edge.** Adding a second position at a pullback to the trend's anchored VWAP earned +0.16R per add-on, but an add-on on the first possible bar, with no level at all, earned the same. Adding at random times inside the trend lost −0.27 to −0.41R.

Rules: `PREREG_round5.md`, committed before any code ran; the frozen rules (`r5_frozen.json`) were committed before the judges were run. The control and the equal-exposure comparison were added after the judges and are labelled exploratory.

## The unique part: trend-birth indicators (`r5_prep.py`)
Every production trade is described by what had happened since its trend was born (the bar where the Kalman z last crossed 0):
age; how far it had already run (MAT); distance to the **anchored VWAP from the birth bar** (what the average participant of this trend paid); compression of the previous 60 bars; whether the signal broke the 30- or 90-day extreme; position against the previous 30-day **volume profile** value area; open-interest change; taker flow; the share of coins in the same trend (breadth); volatility versus normal; and how fast z jumped. Pullback add-ons used three levels: the birth-anchored VWAP, EMA20, and the 0.5 Fibonacci retrace of the trend leg.

## Results ($10 start, 0.5% risk per trade, 14 bps + real funding, 8 open at most)
| Set | Period | Trades (taken) | Win | Net R | $10 → | Max DD |
|---|---|---|---|---|---|---|
| Selection A: design coins | 2021-01..2024-06 | 610 (577) | 37% | +0.349 | $25.09 | 23% |
| Selection B: design coins | 2024-07..2025-06 | 220 (198) | 35% | +0.249 | $11.50 | 11% |
| **J1** design coins FINAL | 2025-07..2026-09 | 252 (241) | 37% | +0.325 | $12.94 | 12% |
| **J2** 10 unseen coins | 2025-06..2026-09 | 235 (215) | 34% | +0.065 | $10.37 | 15% |
| **J3** 10 fresh coins (HOLDOUT2) | 2021-01..2026-09 | 1,041 (979) | 34% | +0.172 | $19.85 | 19% |
| J1+J2+J3 as one portfolio | all | 1,528 (1,113) | — | — | $23.92 | 19% |

- The trend was positive in all three judge sets. J2 is the weakest: about a year, 235 trades, t 0.4, so it neither confirms nor refutes.
- J3 shares market regimes with the selection years (coins move together), so it shows the trend is not specific to the 10 design coins. The time tests are J1 and J2.
- 73% of signals were taken; the rest came while 8 positions were already open.

## What the selection stage found (design coins only, `r5_select_out.txt`)
The full situation map (11 indicators, 3 groups each, net R, win rate and $10 per group) is in that file.
- **No "danger" group**: no situation was reliably bad enough to skip.
- **Two "bonus" groups** passed the pre-registered rule: young trends (MAT low) and coiled ranges (RNG low).
- **One add-on cell**: AVWAP pullback with a 3-ATR stop (+0.29R in A, +0.25R in B).

## What the judges said (`r5_judge_out.txt`, `r5_pooled_out.txt`)
| Rule | J1 FINAL | J2 UNSEEN | J3 HOLDOUT2 | Pooled | Verdict |
|---|---|---|---|---|---|
| Baseline $10 | $12.94 | $10.37 | $19.85 | $23.92 (DD 19%) | — |
| SKIP danger groups | none accepted | | | | not applicable |
| SIZE: risk ×1.5 on bonus groups | $13.78 | $10.08 | $20.67 | $25.67 (DD 25%) | **fail** (loses in J2) |
| Bonus groups R vs others | +0.317 vs +0.336 | −0.025 vs +0.214 | +0.098 vs +0.293 | +0.112 vs +0.289 (t −1.1) | **reversed** |
| AVWAP add-ons, stand-alone R | +0.443 (n 179) | −0.044 (n 185) | +0.150 (n 834) | +0.164 (n 1,198, t 2.1) | **fail** (negative in J2) |
| Baseline + add-ons, 12 open | $14.30 (374 trades) | $10.23 (359) | $26.41 (1,592) | $34.91 (1,774 taken, DD 26%) | not a pass |

## Why the add-on is not a level edge (`r5_control_out.txt`)
Stand-alone net R of a second position added inside a working trend:

| When the add-on is placed | A+B 2021-25 | J1 | J2 | J3 |
|---|---|---|---|---|
| At the AVWAP pullback | +0.280 | +0.443 | −0.044 | +0.150 |
| First possible bar (no level) | +0.269 | +0.301 | +0.005 | +0.144 |
| Random bar inside the trend | −0.265 | −0.276 | −0.412 | −0.300 |

- The pullback entries mostly happen early, so they look like "adding right after the signal". Late additions have too little trend left to pay the 14 bps and lose.
- So the useful finding is about timing (add early or not at all), not about a level.

## Is it just leverage? (`r5_leverage_out.txt`, pooled J1+J2+J3)
| System | $10 → | Max DD |
|---|---|---|
| Baseline, 0.50% risk | $23.92 | 19% |
| Baseline, 0.65% risk | $29.04 | 24% |
| Baseline, 0.80% risk | $34.32 | 29% |
| Baseline, 1.00% risk | $41.28 | 35% |
| SIZE (bonus ×1.5) | $25.67 | 25% |
| Baseline + AVWAP add-ons (12 open) | $34.91 | 26% |

The add-ons end where the baseline ends with about 0.75% risk, at about the same drawdown. SIZE is worse than plain 0.65% risk. So both are more risk on the same edge, not new edge.

## Where profit is
- **The trend itself**, on 4H bars, long and short, with the funding rules. It is the one thing that was positive on 20 coins it was not designed on, and it has now passed on 10 more.
- **Risk size is the only lever that reliably raises the $10 result**, and it raises drawdown with it. 0.5% risk gave $23.92 at 19% drawdown on the pooled 30 coins. 0.8% gave $34.32 at 29%. That is a risk choice, not a new strategy, and past drawdowns are not a limit on future ones.
- **Situations inside the trend** (young, coiled, outside value, breakout, open-interest, breadth) did not separate winners from losers once they left the years they were chosen on.

## Files
| Area | Files |
|---|---|
| Plan | `PREREG_round5.md` |
| Code | `r5_prep.py` (production trades, 11 indicators, add-ons), `r5_run.py` (select, judge), `r5_pooled.py`, `r5_control.py`, `r5_leverage.py` |
| Outputs | `r5_select_out.txt`, `r5_frozen.json`, `r5_judge_out.txt`, `r5_pooled_out.txt`, `r5_control_out.txt`, `r5_leverage_out.txt` |
| Caveat | the prep script drops signals in the first 600 bars (100 days) of each coin, so a few early trades of earlier tables are missing |
