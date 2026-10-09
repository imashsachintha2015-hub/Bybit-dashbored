# Scalp / high-frequency study: 1-minute data, 20 coins, 12 families

**Answer.** No scalping setup is profitable after costs. The best simulated trade of every family loses its $10 in every period and on coins it was never tested on, and loses about what random entries lose. Statistically real effects exist (BTC leading altcoins by a minute, reversals across coins, open-interest flushes) but they are 0.4 to 5.5 bps against a cost of 14 bps (taker) or 4 to 11 bps (maker). The one result that survives is old news: the 4H Kalman trend works, and it fades as the bar gets smaller. It is flat or negative below 1H and wipes out the account at 5 minutes.

**Round 2** (25 structural setups tested by market situation, custom levels, ML, setups aligned with the 4H trend): `README_round2.md`. It also found nothing. **Round 3** (liquidation map from open interest, pairs arbitrage, trend days, limit entries for the Kalman signals): `README_round3.md`. Nothing passes. **Round 4** (why they lose and whether fixing the causes helps, judged on 10 fresh coins): `README_round4.md`. Fees on a zero edge; no fix survives. **Round 5** (trend situations, trend-birth indicators, level add-ons; the 4H trend held on 10 fresh coins): `README_round5.md`.

Rules, data and pass criteria were written down before any data was loaded: `PREREG_scalp_hf.md` (and Addendum 1, also written before any run). Earlier scalp studies (`scalp_master`, `scalp_ml_15m`) used 30 to 120 days of data; this one uses 2021-01 to 2026-09.

## Data and protocol
- Binance USDT-M 1-minute bars (price, quote volume, trades, taker-buy volume), real funding rates, 5-minute open interest. 850 monthly files, none missing.
- Design coins: BTC ETH SOL XRP DOGE BNB ADA LINK AVAX LTC. Unseen coins (looked at only at the end, 2025-07..2026-09): DOT NEAR ATOM APT ARB OP SUI INJ TIA WLD.
- Periods: OLD 2021-22, DEV 2023-01..2024-06 (all selection), VAL 2024-07..2025-06, FINAL 2025-07..2026-09.
- Costs: taker 14 bps round trip (5.5 fee + 1.5 slippage per side); maker 2 bps per side with conservative fills (trade-through, adverse selection, 3-minute wait); stops and time exits taker; real funding. $10 start, 0.5% risk per trade, at most 5 open, one per coin, position caps of the strategy runner.
- Stage 1 screen: gross edge of every cell (262 of them) at fixed horizons, day-clustered t. Pass = DEV gross >= 4 bps and t >= 3.6, replicated in VAL. **0 of 262 cells pass.**
- Stage 2: the best DEV cell of each family gets an exit rule (stop 1 or 2 ATR, target none or 1.5R, taker or maker) chosen on DEV only, then runs once on VAL, FINAL, OLD and the unseen coins.
- Checks (`test_core.py`): simulator rules (stop first, gaps, targets, maker fills, funding), cluster statistics, rolling statistics against brute force, and a random-entry control that loses 13.7 bps on average against a 14 bps cost. In every Stage 2 run the random-entry control with the same exits loses about as much as the strategy.

## The families, best cell on DEV, and the frozen rule on every period
Trade rows show net bps per trade, the $10 result and the number of trades, after **taker** costs (the pass test). Rows are sorted as in the pre-registration; none passes. "fund" has too few events on major coins to say anything (16 to 37 trades in VAL and FINAL).

| family (best DEV cell) | best gross edge DEV | DEV | VAL | FINAL | OLD 2021-22 | UNSEEN coins |
|---|---|---|---|---|---|---|
| flow `['n', 3, 'k', 2, 'exh']` h=5 | +1.8 bps (t 3.2) | -12.85 bps, $0.00 (7122 tr) | -13.48 bps, $0.00 (8711 tr) | -13.47 bps, $0.00 (9484 tr) | -13.49 bps, $0.00 (12590 tr) | -13.50 bps, $0.00 (9180 tr) |
| fund `['f', 5]` h=5 | +8.0 bps (t 2.1) | -5.93 bps, $6.76 (488 tr) | -20.65 bps, $9.63 (37 tr) | -26.30 bps, $9.79 (16 tr) | -14.33 bps, $2.52 (2154 tr) | -13.70 bps, $7.64 (297 tr) |
| lead `['m', 5, 'k', 2, 'lag', 1]` h=1 | +0.4 bps (t 5.2) | -13.62 bps, $0.00 (68782 tr) | -13.89 bps, $0.00 (41770 tr) | -13.79 bps, $0.00 (51240 tr) | -13.16 bps, $0.00 (87294 tr) | -13.69 bps, $0.00 (49692 tr) |
| oi `['w', 3, 'pct', 97.5, 'flush']` h=60 | +5.5 bps (t 3.4) | -12.33 bps, $0.00 (10870 tr) | -15.40 bps, $0.00 (7786 tr) | -13.14 bps, $0.00 (10122 tr) | -15.31 bps, $0.02 (7949 tr) | -11.84 bps, $0.00 (10090 tr) |
| orb `['utc00', 'R', 30]` h=10 | +3.5 bps (t 3.5) | -9.93 bps, $0.07 (3826 tr) | -13.20 bps, $0.27 (2613 tr) | -16.24 bps, $0.02 (3229 tr) | -7.59 bps, $0.56 (5083 tr) | -16.98 bps, $0.12 (3426 tr) |
| pullback `['adx', 1]` h=60 | +0.8 bps (t 1.1) | -13.39 bps, $0.00 (25345 tr) | -13.20 bps, $0.00 (16146 tr) | -14.84 bps, $0.00 (19824 tr) | -15.83 bps, $0.00 (34121 tr) | -14.73 bps, $0.00 (20617 tr) |
| rsi2 `['var', 1]` h=30 | +0.6 bps (t 1.8) | -13.91 bps, $0.00 (80504 tr) | -13.77 bps, $0.00 (52933 tr) | -12.97 bps, $0.00 (67644 tr) | -13.50 bps, $0.00 (113865 tr) | -12.84 bps, $0.00 (70259 tr) |
| squeeze `['vol', 0]` h=10 | -0.2 bps (t -0.8) | -14.07 bps, $0.00 (29164 tr) | -14.67 bps, $0.00 (19900 tr) | -13.90 bps, $0.00 (23313 tr) | -14.26 bps, $0.00 (41098 tr) | -14.46 bps, $0.00 (25034 tr) |
| sweep `['N', 120, 'vol', 1]` h=30 | +0.5 bps (t 1.0) | -14.36 bps, $0.00 (59872 tr) | -13.70 bps, $0.00 (39758 tr) | -13.62 bps, $0.00 (49285 tr) | -14.35 bps, $0.00 (81211 tr) | -12.87 bps, $0.00 (46733 tr) |
| vwap `['k', 2, 'vol', 1]` h=10 | +0.9 bps (t 2.0) | -13.29 bps, $0.00 (36066 tr) | -14.40 bps, $0.00 (24491 tr) | -13.68 bps, $0.00 (29859 tr) | -12.46 bps, $0.00 (46541 tr) | -12.11 bps, $0.00 (31955 tr) |
| xsec `['m', 30, 'rev']` h=15 | +0.4 bps (t 5.8) | -13.64 bps, $0.00 (204273 tr) | -13.65 bps, $0.00 (139143 tr) | -13.68 bps, $0.00 (170246 tr) | -13.29 bps, $0.00 (277510 tr) | n/a |

The same rules with maker execution lose 10 to 15 bps per trade as well (`stage2_eval_*.txt`): the fee saving is smaller than the edge lost to adverse selection, as in `limit_orders/`.

## What the effects are worth (Stage 1, DEV, gross, before any cost)
| family | what it measures | best cell | gross edge | vs 14 bps taker |
|---|---|---|---|---|
| lead | BTC move predicts the next minute of an altcoin | 5-minute BTC impulse, 1-minute hold | +0.4 bps (t 5.2) | 3% |
| xsec | past-30-minute loser beats winner over 15 minutes | reversal, 15 minutes | +0.4 bps (t 5.8) | 3% |
| oi | open-interest flush then reversal | 15-minute drop, 60 minutes | +5.5 bps (t 3.4) | 40% |
| orb | opening range at 00:00 UTC | 30-minute range, 10 minutes | +3.5 bps (t 3.5) | 25% |
| fund | funding settlement | funding >= 5 bps | +8.0 bps (t 2.1), 603 events | 57% |
| flow, vwap, rsi2, sweep, pullback, squeeze | classic scalps and order-flow | best cells | -0.2 to +1.8 bps | under 13% |

Full tables: `stage1_dev_out.txt`. The open-interest flush is the largest real effect and fits the forced-liquidation story that earlier bar-based tests could not confirm; it is still a third of the cost.

## Frequency ladder: the production Kalman trend on smaller bars
Same rules as the live Kalman mode (BTC daily filter, funding filter, 3 ATR stop, exit when the trend flips, 14 bps plus funding), only the bar size changes. Parity with the production code: identical trades for every coin without gaps in the archive (`parity_ladder_out.txt`; SOL and XRP differ by one trade after a 5-day gap in their archive).

**segment DEV, design coins**

| bar (min) | trades | per month | win % | avg net R | net bps/trade | t | $10 -> | trades taken | max DD |
|---|---|---|---|---|---|---|---|---|---|
| 240 | 299 | 17 | 32.4 | +0.406 | 131.8 | 1.5 | 16.82 | 275 | 14% |
| 120 | 613 | 34 | 26.3 | +0.051 | -2.0 | -0.1 | 10.27 | 556 | 22% |
| 60 | 1256 | 70 | 28.5 | -0.030 | -15.6 | -0.8 | 8.54 | 1127 | 41% |
| 30 | 2370 | 132 | 30.7 | +0.028 | 1.2 | 0.1 | 9.41 | 2153 | 39% |
| 15 | 5010 | 279 | 27.0 | -0.070 | -12.4 | -1.8 | 0.84 | 4539 | 92% |
| 5 | 15001 | 835 | 26.3 | -0.192 | -13.4 | -5.9 | 0.00 | 13272 | 100% |

**segment VAL, design coins**

| bar (min) | trades | per month | win % | avg net R | net bps/trade | t | $10 -> | trades taken | max DD |
|---|---|---|---|---|---|---|---|---|---|
| 240 | 220 | 18 | 35.5 | +0.249 | 202.6 | 1.2 | 11.50 | 198 | 11% |
| 120 | 416 | 35 | 35.6 | +0.307 | 153.4 | 1.8 | 16.77 | 377 | 12% |
| 60 | 816 | 68 | 33.9 | +0.276 | 78.2 | 1.6 | 21.77 | 745 | 22% |
| 30 | 1641 | 137 | 31.6 | +0.059 | 22.5 | 1.0 | 11.37 | 1500 | 39% |
| 15 | 3292 | 275 | 31.3 | -0.031 | 1.0 | 0.1 | 4.12 | 3004 | 73% |
| 5 | 10288 | 858 | 28.8 | -0.140 | -9.9 | -3.1 | 0.01 | 9251 | 100% |

**segment FINAL, design coins**

| bar (min) | trades | per month | win % | avg net R | net bps/trade | t | $10 -> | trades taken | max DD |
|---|---|---|---|---|---|---|---|---|---|
| 240 | 252 | 17 | 36.9 | +0.325 | 159.3 | 1.6 | 12.94 | 241 | 12% |
| 120 | 498 | 33 | 32.9 | +0.194 | 67.4 | 1.1 | 13.87 | 459 | 20% |
| 60 | 984 | 66 | 31.7 | -0.024 | 1.0 | 0.0 | 8.44 | 903 | 51% |
| 30 | 1962 | 131 | 28.8 | -0.084 | -9.6 | -0.7 | 3.81 | 1804 | 79% |
| 15 | 3921 | 261 | 29.2 | -0.081 | -8.3 | -1.3 | 1.85 | 3585 | 91% |
| 5 | 12209 | 813 | 26.2 | -0.219 | -15.2 | -7.2 | 0.00 | 10933 | 100% |

**segment OLD, design coins**

| bar (min) | trades | per month | win % | avg net R | net bps/trade | t | $10 -> | trades taken | max DD |
|---|---|---|---|---|---|---|---|---|---|
| 240 | 329 | 14 | 42.6 | +0.367 | 348.5 | 2.2 | 17.08 | 320 | 14% |
| 120 | 610 | 25 | 38.0 | +0.254 | 169.2 | 2.4 | 18.32 | 587 | 21% |
| 60 | 1151 | 48 | 37.8 | +0.103 | 43.9 | 1.5 | 15.27 | 1107 | 32% |
| 30 | 2164 | 90 | 37.8 | +0.150 | 36.2 | 2.0 | 38.47 | 2077 | 29% |
| 15 | 4476 | 187 | 32.9 | +0.036 | -0.9 | -0.1 | 12.43 | 4312 | 66% |
| 5 | 13401 | 559 | 29.8 | -0.074 | -8.0 | -2.3 | 0.02 | 12893 | 100% |

**segment FINAL, unseen coins**

| bar (min) | trades | per month | win % | avg net R | net bps/trade | t | $10 -> | trades taken | max DD |
|---|---|---|---|---|---|---|---|---|---|
| 240 | 203 | 14 | 38.9 | +0.206 | 173.4 | 1.3 | 11.53 | 190 | 14% |
| 120 | 432 | 29 | 35.4 | +0.172 | 113.5 | 1.5 | 13.14 | 404 | 20% |
| 60 | 865 | 58 | 32.8 | +0.032 | 29.7 | 0.8 | 9.66 | 809 | 47% |
| 30 | 1694 | 113 | 30.8 | -0.020 | 0.8 | 0.0 | 6.80 | 1584 | 66% |
| 15 | 3356 | 224 | 32.2 | -0.029 | -3.7 | -0.4 | 4.61 | 3127 | 77% |
| 5 | 10315 | 687 | 29.8 | -0.149 | -15.9 | -4.8 | 0.00 | 9581 | 100% |

Reading: 4H and 2H make money in every period and on unseen coins (about 17 and 33 trades a month on 10 coins, $10 -> $10.3 to $18.3 over the periods), but with small t-statistics on 10 coins; 1H is mixed; 30 minutes and below is flat to negative, and at 5 minutes the account is wiped out. Trading more often with this signal only adds cost.

## First-run bug (kept for transparency)
A zero-volume outage made the VWAP deviation infinite and my running-sum statistics never recovered, so the VWAP family fired on every minute afterwards. Found because its event count jumped 4 to 6 times in later periods. Fixed (non-finite values ignored, sums re-synced), tested against brute force, and everything re-run. The first-run outputs are in `first_run_bug/`; no family's best DEV cell changed except VWAP (+0.65 -> +0.89 bps), and no conclusion changed.

## Limits of this study
- Bars, not order books: queue position, spoofing and sub-minute effects are out of reach (no free history). True market making needs rebate tiers and latency this account does not have.
- Binance prices stand in for Bybit; for 1-minute scalps basis differences are small next to 14 bps but not zero.
- Survivorship: all coins exist today. The unseen coins and the 2021-22 period are the check.
- Only the pre-registered families and grids were tested. Anything new needs a new pre-registration.

## Where this leaves the idea
With retail fees, a scalp needs a gross edge above about 14 bps per trade (4 to 11 bps with perfect maker fills). The best price and order-flow edges found are 0.4 to 8 bps. The remaining levers are cost (fee tier, rebates), speed and access, not signals. Open-interest flushes at longer holds and a forward test of the 2H Kalman rung are the only leads worth a new, pre-registered round.

## Files
`PREREG_scalp_hf.md` rules; `fetch_klines.py` data; `hf_core.py` library; `hf_fam.py` the 11 signal families; `hf_stage1.py`, `hf_report1.py`, `hf_stage2.py` the runs; `hf_ladder.py` + `parity_ladder.py` family 5; `test_core.py` checks; outputs `stage1_dev_out.txt`, `stage2_dev_out.txt`, `stage2_eval_*.txt`, `ladder_*_out.txt`, `summary_table.md`; `first_run_bug/`.
