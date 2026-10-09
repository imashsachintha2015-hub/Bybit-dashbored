# Round 4: why the setups lose, and whether fixing the causes makes them profitable

**Answer.** The losses come from one place: **the setups do not know the direction, and the fee is charged anyway.**
- Across all 50 setup x timeframe families of round 2, fees are the bulk of the loss: 0.12-0.71R per trade.
- With fees removed, most families net about zero (-0.14 to +0.08R).

So most of the losers cannot be "fixed". There is no edge for a better exit to keep.

The 10 setups that did show an edge before costs were diagnosed and fixed:
- Fixes tried: exits, stop width, maker entries, and up to 2 entry-time causes, all chosen on DEV and VAL.
- **7 of 10 could not be made positive in both DEV and VAL by any of 216 re-simulated rules.**
- The 3 that could (MAGNET short, FAKE_PD long 15m, trend-day long) **all failed on 10 fresh coins** that no round had touched: $10 → $4.45, $3.95, $9.75 over 2021-2026.

Rules: `PREREG_round4.md`, committed before the diagnosis ran. The judge, HOLDOUT2, is 10 coins downloaded for this round and never loaded before: AAVE BCH CRV ETC FIL HBAR ICP RUNE TRX UNI.

## Truth 1: what the losses are made of (DEV, `r4_anatomy_out.txt`)
| What | Finding |
|---|---|
| Fees per trade, in R | 0.12R (Supertrend 15m) to 0.71R (VWAP pullback / Bollinger fade 5m). Tight structural stops (0.17-0.5%) make a 14 bps fee a large share of the risk |
| Edge before fees | −0.14R to +0.08R; most families are within ±0.02R of zero. Random entries: +0.01R |
| Funding | about 0 at these holding times |
| Losers that went straight to the stop (best move < 0.3R) | 20-26% for every candidate, about the same as random entries (22-24%) |
| Losers that were up ≥ 1R and then lost | 29-30% for Bollinger fade / CVD divergence (the trailing exit gives it back); 11-19% for FAKE_PD |
| Winners that came within 0.2R of the stop | 3-14%. Stops are not too tight; random entries show the same (14-15%) |

The anatomy of the candidates looks like the anatomy of random entries. The trades lose the way a coin flip with a fee loses.

## Truths 2-3: causes and fixes (chosen on DEV + VAL, `r4_fix_out.txt`)
The fix menu, every option re-simulated on every trade:
- targets 1 / 1.5 / 2 / 3R / own / none;
- breakeven stop on or off;
- hold time ½× / 1× / 2×;
- stop width 1× / 1.5× / 2×;
- taker or maker entry.

That is 216 rules per candidate, plus 13 entry-time causes.

| Candidate | Base rule DEV / VAL | Best rule positive in DEV and VAL? | Chosen fix |
|---|---|---|---|
| Bollinger fade 5m long / short | −0.70 / −0.61R, −0.62 / −0.59R | no (best −0.21R) | not fixable |
| CVD divergence 15m long / short | −0.43 / −0.33R, −0.33 / −0.33R | no (best −0.09R) | not fixable |
| Failed PDH/PDL break 5m long / short | −0.22 / −0.24R, −0.25 / −0.36R | no (best −0.09R) | not fixable |
| Failed PDH/PDL break 15m short | −0.13 / −0.26R | no | not fixable |
| Failed PDH/PDL break 15m long | −0.15 / −0.24R | yes: +0.07 / +0.04R | no target, 24h hold, maker entry; skip 14:00 UTC and one funding tercile → +0.16 / +0.08R |
| MAGNET short | 0.00 / +0.01R | yes: +0.02 / +0.26R | no target, 48h hold, maker entry; skip 07:00 UTC |
| Trend day long 08:00 | +0.07 / +0.01R | yes: +0.02 / +0.11R | 1.5R target, hold past the day close |

Most of the improvement comes from the same change: **wider stops or no target and longer holds** (fewer fees per R), plus maker entries. That turns a scalp into a 1-2 day swing trade.

## Truth 4: the judge, 10 fresh coins, 2021-01..2026-09 (`r4_judge_out.txt`)
| Fixed rule | Trades | Net R | t | $10 → (0.5% risk) | Max DD | Periods positive | Pass |
|---|---|---|---|---|---|---|---|
| MAGNET short | 5,581 | +0.032 | 0.6 | **$4.45** | 78% | 2 / 4 | no |
| FAKE_PD long 15m | 2,894 | −0.041 | −0.7 | **$3.95** | 78% | 2 / 4 | no |
| Trend day long | 358 | −0.009 | −0.1 | **$9.75** | 12% | 2 / 4 | no |

- **MAGNET short:** the fix turned it into a lottery ticket. 24% of trades win +3.4R and 76% lose −1R.
  - It made money in falling years (2021 +0.33R, 2022 +0.11R, 2025 +0.14R) and lost in rallies (2023 −0.20R, 2026 −0.20R).
  - With up to five correlated shorts open, the drawdowns compound the account down to $4.45 despite a slightly positive average.
  - This is a bet that the market falls, not a liquidation edge.
- **FAKE_PD long 15m:** on the fresh coins it went from +0.14R in DEV to −0.30R in FINAL. The filters it learned (14:00 UTC, a funding tercile) were noise.
- **Trend day long:** flat everywhere, as before.
- **Control:** random entries with the same fixes lose −0.16 to −0.29R on the fresh coins. The fixes did not manufacture profit out of noise; there was simply no edge left under them.
- On the old coins' FINAL period ("seen before") the fixed rules also lost: $5.59, $4.39 and $10.67.

## The conclusion after four rounds
- **A loss can be turned into a profit only when the setup has an edge that something else is eating.** Here the "something else" is the fee. The fix is then cheaper execution, wider stops or longer holds.
- Every setup tested on 1-15 minute bars has an edge before fees of about zero to +0.1R. That is the same as random entries, and not stable across coins or years.
- So the cheaper-execution fix has nothing to rescue, and filters chosen in hindsight do not survive new coins.
- The only rule in this project that has survived every holdout is the slower 4H Kalman trend. Its trades are large (hundreds of bps) relative to the fee, which is exactly what the fixes above were trying to imitate.

## Files
| Area | Files |
|---|---|
| Plan | `PREREG_round4.md` |
| Code | `r4.py` (events, anatomy, fix grid, causes, judge); `fetch_klines.py` (`HF_FETCH=holdout2` downloads the fresh coins) |
| Parity | `r4_parity.py`, `r4_parity_out.txt` (the base rules reproduce rounds 2-3 exactly) |
| Outputs | `r4_anatomy_out.txt`, `r4_fix_out.txt`, `r4_frozen.json`, `r4_judge_out.txt` |
