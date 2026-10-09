# Situation tests on the funding-aware Kalman trend (pre-registered)

**Rules.** All 29 tests and the stress profile were written in `PREREG_situations.md` (commit 991eab5) before any code ran.

**Engine (`sit_core.py`).** It replays every trade bar by bar. Before any test ran, `sit_parity_out.txt` confirmed that it reproduces the published system:
- all 3,080 signals (0 mismatches);
- the 3,460 candidate entries sequenced one per coin;
- the $10 portfolio to six decimals: $94.119377, 1,616 trades, max drawdown −28.48%.

**$10 results.** All use 0.5% risk, max 8 open and one per coin. "2020-26" is compounded from 2020-01 to 2026-09; "2024-26" restarts from $10 in 2024. The current system makes $94.12 (1,616 trades, DD −28%) and $19.37 (765 trades, DD −19%).

**Statistics.** "kept vs removed" and "t" are pooled over the holdouts VAL, FINAL, UNSEEN and OLD. "wins" counts holdouts where the change beat the benchmark.

## Summary
| test | result |
|---|---|
| 1. Chop detectors (7) | all fail; one points the opposite way (below) |
| 2. Profit-taking (8) | all fail; most are clearly worse (t −2 to −3) |
| 3. Crashes and slippage | **robust**: still +0.20R per trade at 0.25% slippage per fill; break-even about 0.95% |
| 4. Liquidation flush (4) | all fail; shorting after a squeeze loses (t −3.1) |
| 5. Relative strength (3) | all fail |
| 6. Portfolio heat (4) | **CORR_CAP passes** (drawdown −28% → −19%) but is sensitive to its threshold |
| 7-9. Weekend, new listing, FOMC | all fail; FOMC-day and new-listing signals did better, not worse |

Of the 25 t-based tests, 0 passed; about 0.6 passes were expected by luck.

## 1. Chop (`sit_filters_out.txt`): skip, or take at half risk, when the market is choppy
| filter | kept vs removed (n) | t | wins | skip: $10 2020-26 | half risk: $10 2020-26 |
|---|---|---|---|---|---|
| COIN_CHOP (efficiency ratio below the random-walk level) | +0.189 (1,083) vs +0.353 (1,317) | −1.07 | 1/4 | $32.65 (1,070 trades, DD −19%) | $52.62 (1,616, −21%) |
| BTC_CHOP | +0.386 (1,354) vs +0.140 (1,046) | +1.43 | 3/4 | $68.50 (1,011, −18%) | $72.49 (1,616, −22%) |
| MEAN_REVERT (variance ratio < 1) | +0.280 (805) vs +0.278 (1,595) | +0.02 | 3/4 | $20.31 (835, −23%) | $41.12 (1,616, −22%) |
| DISORDER (entropy above random walk) | +0.015 (644) vs +0.376 (1,756) | **−2.62** | 0/4 | $10.05 (709, −26%) | $30.99 (1,616, −18%) |
| SQUEEZE (ATR below 75% of its 30-day mean) | +0.314 (1,792) vs +0.176 (608) | +0.76 | 4/4 | $79.58 (1,383, −31%) | $83.44 (1,616, −28%) |
| COIN_WHIPSAW (the coin's last 2 signals lost) | +0.162 (1,351) vs +0.442 (997) | −1.68 | 0/4 | $48.11 (1,277, −24%) | $53.58 (1,616, −22%) |
| SYSTEM_COLD (2 or fewer winners in the last 10) | +0.250 (1,360) vs +0.325 (1,029) | −0.43 | 1/4 | $44.27 (955, −29%) | $60.00 (1,616, −24%) |

- **No chop detector helps.** Kalman entries made out of disorderly, choppy 30-day windows did better than entries after orderly moves, which is the opposite of the hypothesis (t −2.6, all 4 holdouts). A fresh trend out of a range seems to beat a late entry into an orderly move. Using this would need its own pre-registered test.
- **Bug fixed:** in a first run, 3 portfolio rows were wrong because NumPy booleans failed an `is True` check. The per-signal statistics were identical after the fix.

## 2. Managing a trade in profit (`sit_exits_out.txt`): paired, the same 3,080 signals with a different exit
| variant | difference vs standard | t | wins | $10 2020-26 | $10 2024-26 |
|---|---|---|---|---|---|
| PART_2R (1/3 off at +2R) | −0.054R | −3.00 | 0/4 | $58.09 (1,616 trades, DD −28%) | $16.58 (765, −18%) |
| PART_3R (1/2 off at +3R) | −0.065R | −2.95 | 0/4 | $52.83 (1,616, −27%) | $15.67 (765, −18%) |
| BE_15 (stop to entry at +1.5R) | −0.023R | −1.11 | 2/4 | $85.96 (1,634, −27%) | $18.15 (773, −18%) |
| TRAIL (3-ATR trail after +2R) | −0.122R | −2.68 | 0/4 | $41.15 (1,665, −27%) | $14.76 (795, −19%) |
| TP3 (everything off at +3R, "TP Entire") | −0.130R | −2.95 | 0/4 | $32.73 (1,736, −25%) | $12.42 (831, −22%) |
| TP5 (everything off at +5R) | −0.077R | −2.16 | 2/4 | $53.96 (1,665, −27%) | $17.17 (792, −19%) |
| LADDER (1/3 at +2R, 1/3 at +4R) | −0.090R | −2.99 | 0/4 | $41.28 (1,616, −27%) | $15.05 (765, −18%) |
| LADDER_BE | −0.092R | −2.93 | 0/4 | $40.15 (1,622, −26%) | $14.57 (768, −18%) |

**Why they all lose.**
- The standard exit averages +0.293R per trade, but the median trade is −0.42R and only 35% of trades win.
- 7.0% of trades end at +3R or more, and those carry the system.
- Of the 826 trades that reached +2R, only 63 later closed at or below 0R. Locking in profit early therefore cuts more big winners than it saves givebacks.

## 3. Crashes and fast markets (`sit_exits_out.txt`)
Every scenario takes the same 1,616 trades.

| scenario | average R, all / holdouts (t) | $10 2020-26 (DD) | $10 2024-26 (DD) |
|---|---|---|---|
| standard (stop exactly −1R) | +0.293 / +0.279 (3.16) | $94.12 (−28%) | $19.37 (−19%) |
| GAP (stop gapped through fills at the open) | identical: of 788 stop exits, none was gapped (crypto trades 24/7) | | |
| GAP + 0.05% slippage per fill | +0.277 / +0.263 (2.99) | $83.55 (−30%) | $18.26 (−20%) |
| GAP + 0.10% | +0.262 / +0.248 (2.81) | $74.15 (−31%) | $17.22 (−21%) |
| GAP + 0.25% | +0.216 / +0.202 (2.29) | $51.82 (−35%) | $14.42 (−24%) |
| GAP + 0.50% | +0.139 / +0.125 (1.42) | $28.48 (−43%) | $10.73 (−33%) |
| GAP + 1.00% | −0.014 / −0.030 (−0.34) | $8.54 (−72%) | $5.91 (−57%) |
| GAP + FASTBAR (0.5 ATR more on wide-bar stops) | +0.267 / +0.254 (2.86) | $76.58 (−31%) | $17.75 (−21%) |
| FEES ×2 | +0.271 / +0.257 (2.92) | $79.75 (−30%) | $17.85 (−21%) |
| FUNDING ×2 | +0.267 / +0.253 (2.91) | $78.54 (−29%) | $18.36 (−20%) |
| COMBINED (GAP + 0.25% + FASTBAR + FEES ×2) | +0.169 / +0.155 (1.75) | $35.68 (−40%) | $12.17 (−29%) |

**Break-even slippage:** 0.95% per market fill for the average trade, 0.93% for the $10 portfolio. The pre-registered verdict is **robust**.

**The 10 largest 24-hour BTC falls** (mark-to-market of open trades):
- **Flat in 4:** 2020-03-13 −40.6%, 2020-03-16, 2021-02-23 and 2022-11-10.
- **Short in 4, gaining:**
  - 2021-05-19: +3.8%;
  - 2021-12-04: +4.2%;
  - 2022-06-13: +5.7%;
  - 2026-02-06: +6.9%.
- **Long in 2:**
  - 2021-01-11: 5 longs gave back −9.0% of equity in open profit, with no stop hit;
  - 2024-08-05: −0.5%.

## 4. Liquidation flush (`sit_flush_heat_out.txt`; open interest from 2021-12)
| setup | pooled holdouts | t | verdict | $10 standalone |
|---|---|---|---|---|
| LONG_FLUSH | +0.050R (254) | +0.45 | fail (positive in 3/3, too small) | $9.90 (325 trades, DD −17%) |
| SHORT_SQUEEZE_END | −0.497R (53) | −3.13 | fail | $8.00 (94, −20%) |
| LONG_FLUSH_BTCUP | −0.157R (120) | −0.86 | fail | $9.46 (135, −11%) |
| SHORT_SQUEEZE_BTCDOWN | −0.346R (16) | −1.30 | too rare | $9.54 (26, −5%) |

## 5. Relative strength (`sit_filters_out.txt`)
| filter | kept vs removed (n) | t | wins | skip: $10 2020-26 |
|---|---|---|---|---|
| RS_LEADER (alt longs only if beating BTC over 30 days) | +0.306 (572) vs +0.498 (902) | −0.85 | 1/4 | $32.87 (1,301 trades, DD −18%) |
| ALT_SEASON (alt longs only when most alts beat BTC) | +0.327 (420) vs +0.453 (1,013) | −0.45 | 1/4 | $25.18 (1,141, −16%) |
| RS_LAGGARD (alt shorts only if lagging BTC) | +0.015 (608) vs +0.055 (239) | −0.27 | 2/4 | $90.07 (1,518, −25%) |

## 6. Portfolio heat (`sit_flush_heat_out.txt`, `sit_corr_sens_out.txt`)
| rule | $10 2020-26 | $10 2024-26 | MAR 2020-26 / 2024-26 | years with higher MAR | verdict |
|---|---|---|---|---|---|
| current (max 8) | $94.12 (1,616 trades, DD −28%) | $19.37 (765, −19%) | 29.54 / 4.88 | - | - |
| DIR_CAP4 | $45.28 (911, −12%) | $15.72 (414, −11%) | 30.35 / 5.05 | 2/7 | fail |
| DIR_CAP6 | $68.87 (1,287, −22%) | $18.34 (595, −15%) | 26.76 / 5.48 | 5/7 | fail |
| RISK_TAPER | $67.40 (1,616, −21%) | $17.20 (765, −15%) | 27.33 / 4.76 | 3/7 | fail |
| **CORR_CAP** (skip if 3+ open same-direction trades correlate > 0.7) | **$82.32 (1,387, −19%)** | **$20.65 (662, −19%)** | **37.09 / 5.46** | 4/7 | **PASS** |

- The current system often runs full in one direction: when it took a trade, 7 same-direction trades were already open 364 times out of 1,616.
- **Exploratory sensitivity of CORR_CAP:** the improvement holds at 0.7 (MAR 29.5 with K=2, 37.1 with K=3, 40.1 with K=4), not at 0.6 (21.7-25.9, worse) or 0.8 (28.2-29.2, about the same).
- So it is promising but sensitive to its threshold. Forward-test it before using it.

## 7-9. Weekends, new listings, FOMC days (`sit_filters_out.txt`)
| filter | kept vs removed (n) | t | wins | skip: $10 2020-26 |
|---|---|---|---|---|
| WEEKEND | +0.309 (1,881) vs +0.170 (519) | +0.79 | 4/4 | $90.68 (1,423 trades, DD −31%) |
| NEW_LISTING (first 180 days) | +0.245 (2,269) vs +0.861 (131) | −1.97 | 2/4 | $70.25 (1,561, −28%) |
| FOMC (decision day and the day before) | +0.207 (2,302) vs +1.962 (98) | −2.36 | 1/4 | $67.31 (1,588, −28%) |

**FOMC and new listings point the other way:** the system's signals around FOMC decisions and in newly listed coins did *better*. Avoiding them would cost money. CPI release dates could not be downloaded.

## Conclusions
1. **The simple system is hard to improve.** Of 25 filters and exit changes, none passed; most removed good trades along with bad ones.
2. **Keep the standard exit.** Partial profits, breakeven, trailing and fixed take-profits all cost money here, because the edge is the few trades that run far. A take-profit order at "TP Entire" would cut those trades; the line is still useful as a reference marker on the chart.
3. **Execution matters more than situations.** The edge survives 0.25% slippage per fill and breaks even near 1%. Use liquid coins and avoid market orders in thin books.
4. **Shorts acted as crash insurance.** They were open, and gaining, in 4 of the 10 worst BTC days.
5. **Risk rule to forward-test:** CORR_CAP cut the drawdown from −28% to −19% with a higher return per unit of drawdown, but only near its pre-registered threshold.
