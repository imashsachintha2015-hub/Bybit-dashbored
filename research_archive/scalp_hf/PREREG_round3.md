# Round 3: four new mechanisms (pre-registration, written before any round-3 code touched data)

**Request.** "Try something clever, creative, create something new."

**What is already known.** Rounds 1-2 (`README.md`, `README_round2.md`): price-pattern setups on 1m-15m bars have no edge before costs.
The research archive already covers the CME gap, calendar effects, funding carry, market making, patient entries, forced flow, basis
and cross-venue. So round 3 tests mechanisms that are not price patterns and were not tried: (A) a liquidation map estimated from
open interest, (B) statistical arbitrage between coins, (C) a better entry for the one system that works (4H Kalman), (D) intraday
trend days. Each one is chosen so that a typical trade moves much more than the 14 bps cost. Prior expectation: most fail.

## Shared rules (as rounds 1-2)
- Binance USDT-M 1m klines, 5-minute open interest (from 2021-12 for most coins), real funding. Design coins BTC ETH SOL XRP DOGE BNB ADA
  LINK AVAX LTC; unseen coins DOT NEAR ATOM APT ARB OP SUI INJ TIA WLD (2025-07..2026-09 only).
- OLD 2021-01..2022-12, **DEV 2023-01..2024-06 (all selection)**, VAL 2024-07..2025-06, FINAL 2025-07..2026-09, UNSEEN = unseen coins on
  FINAL. Code is debugged on BTC and ETH 2023-01..2023-03 only. VAL is looked at only for cells that pass the DEV screen.
- Taker 7 bps per side (14 round trip), maker 2 bps (limit entries in C only), real funding. Stress: +2 bps per side.
- $10, risk 0.5% of equity per trade (1% also reported), at most 5 open, one per coin (one per pair in B), notional caps 3x / 6x.
- **DEV screen:** n >= 60, average net R > 0, day-clustered t >= 2.0. **VAL confirmation:** n >= 30, net R > 0, t >= 1.5. Chance that a
  cell with no edge passes both: about 0.023 x 0.067 = 0.0015; with 76 cells, about 0.1 false passes expected.
- **Pass (all):** FINAL n >= 60, net R > 0, t >= 1.5; UNSEEN net R > 0 (n >= 30); positive in at least 3 of 5 FINAL quarters; positive
  with +2 bps per side; $10 at 0.5% ends above $10 with max drawdown < 40%.
- **Reporting:** the best DEV cell of every idea x side (n >= 60) is frozen and run once on VAL, FINAL, UNSEEN and OLD with $10
  results and trade counts, pass or not.

## A. LIQ_MAP: estimated liquidation levels (32 cells)
Exchanges liquidate leveraged positions at known prices, and traders watch "liquidation heatmaps". No liquidation history is
available, so the map is estimated from open interest:
- On 5m bars. OI stamped T is known at T + 5 min, so at the close of bar j the OI change of bar j-1 is known and is applied with bar j-1's
  typical price p and taker-buy share s = taker buy / quote volume.
- OI up by dOI: new notional dOI x p, a share s opened long and 1-s short, spread equally over leverage 10, 25, 50 and 100x. A long
  opened at p is liquidated at p(1 - 1/L + 0.005), a short at p(1 + 1/L - 0.005). Two maps (long-liquidation, short-liquidation) on
  a log price grid of 0.1% bins.
- OI down: both maps are scaled by OI_new / OI_old. Each hour both maps decay with half-life HL in {1 day, 3 days}.
- A bin inside the range of bar j is triggered and cleared; the cleared notional of bar j is that bar's estimated liquidation volume.
- Sizes are divided by the coin's last-24h quote volume V24.

**MAGNET** (16 cells). At each 15m close with price P: A = short-liquidation notional in (1.005P, 1.03P], B = long-liquidation notional in
[0.97P, 0.995P). Long if A >= R x B (R in {2, 4}) and A / V24 is above its trailing-30-day 80th percentile (updated daily). The target
is the centre of the densest 0.5% window above (the 5 bins with the largest sum). Stop at an equal distance below (target = 1R) or half
of it (target = 2R). At most 24 h. Short mirrored.

**SWEEP** (16 cells). Bar j's cleared long-liquidation notional / V24 is at or above its trailing-30-day q-th percentile (q in {98, 99.5},
over all bars, and > 0), and at the close of bar j+1 the OI change of bar j is negative (positions really closed). Long at the next
open, betting that the forced selling is over. Stop: the lower of the two bars' lows - 0.25 ATR14(5m), at least 0.3%, at most 4%.
Target 1.5R or 3R; at most 4 h. Short mirrored with short-liquidations.

## B. PAIRS: statistical arbitrage between coins (8 cells)
On 1H closes, all 45 pairs of design coins:
- Hedge ratio beta = OLS slope of log A on log B over the previous 30 days, recomputed at each UTC midnight.
- Spread s = log A - beta log B. z = (s - mean) / sd over the last Z hours (Z in {72, 168}), computed with today's beta.
- Entry when z crosses beyond +-k (k in {2.0, 2.5}): short the spread above +k, long below -k. Exit at the next hourly open after z
  crosses 0; stop when |z| >= k + 2 at an hourly close; at most 72 h.
- Filter in {none, HL}: HL = the half-life of the 30-day spread's AR(1) fit, between 2 and 72 hours.
- Legs: weight 1/(1+|beta|) in A and |beta|/(1+|beta|) in B, in opposite directions for beta > 0. Each leg pays its fees and funding,
  so the round trip is 14 bps of gross notional.
- Risk for sizing = 2 sd / (1+|beta|) (the move from entry to stop), at least 0.3%. One trade per pair at a time.
- UNSEEN = the 45 pairs of unseen coins on FINAL.

## C. KALMAN_ENTRY: a better entry for the 4H Kalman signals (12 cells)
Production 4H Kalman signals (`hf_ladder.py`: z crosses +-1, BTC daily filter, funding filter). Baseline: market entry at the next 4H
open. Variants:
- A limit order p x ATR14(4H) better than the signal close (p in {0.25, 0.5, 1.0}), placed at the next 4H open, alive for W 4H bars
  (W in {1, 3}).
- It fills on a 1m trade-through of 1 bp, at the limit price with a 2 bps maker fee. If the open is already through the limit, it fills
  at the open, taker.
- If z crosses 0 first, the order is cancelled.
- Unfilled: skip, or enter at market at the open after the window if the signal is still alive.
- After entry: the production stop (signal close - 3 ATR, at least 0.5% from the actual entry), exit at the 4H open after z crosses 0,
  at most 120 bars from the signal. Walked on 1m bars, baseline too; the baseline must reproduce `hf_ladder.py`'s trades.
- **Test:** paired per signal, delta R = variant R - baseline R (unfilled skip = 0). DEV: mean delta R > 0 with t >= 2.0; VAL: delta > 0,
  t >= 1.5. Pass: FINAL delta R > 0 and the variant's $10 above the baseline's $10; UNSEEN delta R > 0.

## D. TREND_DAY: ride intraday trend days (24 cells)
At T in {08:00, 12:00, 16:00 UTC} on 1m data, with the UTC day's open O, high H, low L and the price P at T:
- Long if P - O >= k x ATR14(daily, completed days) (k in {0.5, 0.8}) and (P - L) / (H - L) >= 0.75 (trading near the high).
- Entry at the next 1m open. Stop: P - 0.5 ATR(daily), or the day's low so far - 0.1 ATR(daily) (skipped if more than 6%).
- Exit at the UTC day's last close.
- Short mirrored. One trade per coin per day per cell.
