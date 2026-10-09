# Scalp study, round 2: setups by market situation (pre-registration, written before any round-2 code touched data)

**Request.** "Try everything possible: indicators, tools, custom levels (Fibonacci, volume...), remove noise, only proper setups, and test
situation by situation: long trends, short trends, breakouts, fakeouts, liquidity hunts, trend reversals."

**What round 1 showed.** 262 fixed-horizon cells on 1m data: the best gross edges were 0.4 to 8 bps per trade against 14 bps of cost; every
frozen rule went to about $0 from $10. The same production trend rules make money on 4H and 2H bars and lose below 1H. The arithmetic: a
scalp needs an average move much larger than 14 bps per trade. So round 2 does what discretionary traders do: wait for a structural
setup on 5m or 15m bars, put the stop behind the structure, and aim at a level. Stops are then 0.3% to 3% wide, and the 14 bps cost is
0.05R to 0.5R instead of 1R or more. Prior expectation: most cells fail; some may survive DEV by luck; the two-stage DEV/VAL rule below
is there to catch that.

## Data, segments, discipline (unchanged from round 1)
- Binance USDT-M 1m klines with taker-buy volume, 5-minute open interest, funding. Design coins BTC ETH SOL XRP DOGE BNB ADA LINK AVAX LTC;
  unseen coins DOT NEAR ATOM APT ARB OP SUI INJ TIA WLD.
- OLD 2021-01..2022-12, **DEV 2023-01..2024-06 (all selection)**, VAL 2024-07..2025-06, FINAL 2025-07..2026-09, UNSEEN = unseen coins on
  FINAL. Code is debugged only on BTC and ETH 2023-01..2023-03. VAL is looked at only for cells that passed the DEV screen. FINAL, UNSEEN
  and OLD are run once, on frozen rules.
- A trade's period is the period of its entry time.

## Bars, regimes, levels (all causal: a value is used only after the bar or day that produces it has closed)
- **Signal timeframes:** 5m and 15m bars built from 1m (UTC-aligned). Decision at the bar close; entry at the next 1m open; the trade is
  then walked on 1m bars (stop first inside a bar).
- **Indicators per timeframe:** EMA 20/50/200, ATR14 (Wilder), RSI14, ADX14, Bollinger(20,2), Keltner(20, 1.5 ATR), Donchian 48 and 20,
  Supertrend(10,3), MACD(12,26,9), relative volume (quote volume / mean of the previous 20 bars), CVD (cumulative taker buy - sell
  quote volume), swing pivots (a high/low that is the extreme of 3 bars on each side, known 3 bars after the pivot).
- **Levels:** previous UTC day high/low/close (PDH/PDL/PDC); previous ISO week high/low (PWH/PWL); Asia range = 00:00-08:00 UTC high/low,
  usable 08:00-24:00; daily and weekly anchored VWAP from 1m typical price with the volume-weighted standard deviation bands;
  previous-day volume profile from 1m bars (100 price bins over the day range, each minute's volume spread evenly over the bins it covers):
  POC, value area high/low (70%); Fibonacci retracements 0.5/0.618/0.786 and extension 1.272 of the last confirmed swing leg; round
  numbers (the nearest multiple of 10^(floor(log10 price) - 1)).
- **Confluence score** of a price p: the number of levels in {PDH, PDL, PDC, PWH, PWL, Asia H/L, POC, VAH, VAL, daily VWAP, weekly VWAP,
  fib 0.5, fib 0.618, round number} within 0.25 ATR of p.
- **Situation (regime) tag** from the last completed 1H bar: **UP** = close > EMA200, EMA50 > EMA200, EMA50 rising over 5 bars, ADX14 >= 20;
  **DOWN** = the mirror; **RANGE** = ADX14 < 20; otherwise MIXED. Cells are run for regime in {ALL, UP, DOWN, RANGE}. A volatility tag
  (1H ATR% vs its trailing 30-day median: LOW < 0.8x, HIGH > 1.25x) is reported, not selected on.

## Setup library (each has a long and a mirrored short; S = structural stop, T = level target)
Scenario **BREAKOUT**
1. BRK_DON: close above the prior 48-bar high. S: low of the last 2 bars - 0.1 ATR. T: level + the channel height.
2. BRK_PD: first close of the UTC day above PDH (below PDL). S: break-bar low - 0.1 ATR. T: PDH + 0.5 x previous-day range.
3. BRK_ASIA: 08:00-16:00 UTC, first close beyond the Asia high/low. S: break-bar low - 0.1 ATR. T: Asia high + Asia range.
4. BRK_RETEST: within 12 bars of a BRK_DON break, a bar's low comes within 0.1 ATR of the broken level and it closes above it. S: retest
   low - 0.1 ATR. T: level + channel height.
5. SQZ_BRK: Bollinger inside Keltner for at least 6 of the last 8 bars, then a close beyond the 20-bar Donchian. S: the squeeze-box low
   (lowest low of the last 8 bars) - 0.1 ATR. T: break level + box height.
6. ORB: 15-minute opening range at 00:00, 08:00 and 13:30 UTC; first close beyond it within 2 hours. S: opposite side of the range.
   T: level + range height.

Scenario **FAKEOUT**
7. FAKE_DON: the previous 1-3 bars closed above the prior 48-bar high, and this bar closes back below that level: short. S: highest high
   since the break + 0.1 ATR. T: channel midpoint.
8. FAKE_PD: the same with PDH/PDL. T: previous-day midpoint.

Scenario **LIQUIDITY HUNT** (a wick through a level that closes back inside, on relative volume >= 1.5)
9. SWEEP_PD: high > PDH and close < PDH: short. S: wick high + 0.1 ATR. T: previous-day midpoint.
10. SWEEP_ASIA: 08:00-16:00 UTC, the same at the Asia high/low. T: the opposite side of the Asia range.
11. SWEEP_EQ: two confirmed swing highs in the last 48 bars within 0.1 ATR of each other (equal highs); a wick above both that closes below.
    S: wick high + 0.1 ATR. T: the lowest swing low between/after them.
12. SWEEP_SWING: a wick through the last confirmed swing high that closes back below, relative volume >= 2. T: the last swing low.

Scenario **TREND CONTINUATION** (long trend / short trend: the regime cells UP and DOWN)
13. FIB_PB: last confirmed leg swing low -> swing high of at least 3 ATR; a bar's low enters the 0.5-0.786 zone, it closes above the 0.618
    level and above its open; once per leg. S: the swing low - 0.1 ATR. T: the 1.272 extension.
14. VWAP_PB: the last 12 closes above the daily VWAP; a bar's low touches it and it closes above it. S: bar low - 0.1 ATR. T: VWAP + 2 sd.
15. EMA_PB: EMA20 > EMA50 > EMA200; low <= EMA20, close > EMA20 and close > open. S: min(bar low, last swing low) - 0.1 ATR. T: last swing high.
16. ST_FLIP: Supertrend(10,3) flips up. S: the Supertrend line. T: none of its own (3R).
17. MACD_X: MACD histogram crosses above 0 with close > EMA200. S: lowest low of the last 10 bars - 0.1 ATR. T: last swing high.

Scenario **TREND REVERSAL**
18. CHOCH: the last two swing highs and lows are both lower (down structure); a close above the last swing high (change of character).
    S: the last swing low - 0.1 ATR. T: the swing high before the last one.
19. RSI_DIV: a bar's low undercuts the last swing low while RSI14 is higher than at that swing low and the bar closes above its open.
    S: bar low - 0.1 ATR. T: the last swing high.
20. CVD_DIV: the same with the CVD instead of RSI (selling absorbed).
21. EXHAUST: low below daily VWAP - 3 sd, relative volume >= 3 and a lower wick >= 50% of the bar range: long. S: bar low - 0.1 ATR. T: VWAP.
22. OI_FLUSH (5m data needed; on 15m the 15-minute OI change): OI change over the bar in the trailing-30-day lowest 2.5%, price down >= 1.5
    ATR over the last 3 bars, bar closes above its open: long. S: bar low - 0.25 ATR. T: daily VWAP.

Scenario **RANGE / CUSTOM LEVELS**
23. VP_FADE: price opens inside the previous-day value area, the high reaches VAH and the bar closes back below it: short. S: high + 0.1 ATR. T: POC.
24. CONF_BOUNCE: the bar's low is within 0.25 ATR of a price with confluence score >= 3 that lies below the close; the bar closes in its top
    third and above its open: long. S: bar low - 0.25 ATR. T: the nearest level above with score >= 2 (else 2R).
25. BB_FADE: low below the lower Bollinger band and close back inside, ADX14 (signal TF) < 20: long. S: bar low - 0.1 ATR. T: the middle band.

**Control.** RANDOM: random bars (about one per 4 hours per coin), random side, stop 1 ATR, same exits.

## Trades
- Entry at the next 1m open. Stop distance d = |entry - S|. If d < max(0.5 ATR, 0.15%) the stop is widened to that; if d > min(4 ATR,
  4%) or the entry is already beyond S the trade is skipped.
- **Exits (4 variants):** R15 = target at 1.5R; R3 = target at 3R; LVL = the setup's level target T, or 1R if T is closer than 1R
  (no T: 2R); TRAIL = chandelier stop at the highest high since entry - 3 ATR (never below the initial stop), no target. All exits:
  stop-market, at most 48 signal bars (4h on 5m, 12h on 15m), then the close.
- Costs: taker 5.5 bps fee + 1.5 bps slippage per side (14 bps round trip) plus real funding. Stress test: +2 bps per side. A maker
  version (limit entry at the signal close, round-1 fill model) is reported for survivors only, not selected on.
- One position per coin per cell (a signal during an open trade of the same cell and coin is skipped).
- **Noise filter (2 variants):** NONE, or VOL = relative volume >= 1.5 on the signal bar.
- $10 portfolio as in round 1: risk 0.5% of equity (1% also reported), max 5 open, one per coin, notional cap 3x per position, 6x total.

**Cells:** 25 setups x 2 sides x 2 timeframes x 4 regimes x 2 filters x 4 exits = **3200 cells**.

## Selection and pass rules
- **DEV screen:** n >= 150 trades, average net R > 0 after taker costs, day-clustered t (of net R per trade) >= 3.0.
- **VAL confirmation** (only for screen survivors): n >= 50, average net R > 0, t >= 2.0. Probability that a cell with no edge passes both
  is about 0.0013 x 0.023 = 3e-5, so about 0.1 false passes in 3200 cells.
- Among confirmed cells, at most one per setup x side (the highest VAL t) is frozen.
- **Pass for a trading system (all required):** FINAL n >= 100, average net R > 0, t >= 2.0; UNSEEN n >= 60 and average net R > 0; net R
  positive in at least 3 of the 5 FINAL quarters; still positive with +2 bps per side; $10 at 0.5% risk ends above $10 with maximum
  drawdown < 40%. The RANDOM control must lose about its cost.
- **Reporting (the standing request):** every scenario x regime gets a $10 result with trade counts: the cell with the highest DEV t (n >=
  150) for each scenario x regime x side is frozen on DEV and run on VAL, FINAL, UNSEEN and OLD whether or not it passed. Full DEV tables of
  all 3200 cells are saved.

## Kitchen sink: walk-forward gradient boosting
All setup events (TRAIL exit excluded; exit R15 and LVL), with features known at entry: setup, side, timeframe, regime, vol tag, ADX 1H,
relative volume, RSI, stop distance in ATR and %, distance to daily VWAP / PDH / PDL / POC in ATR, confluence score at entry, hour,
weekday, funding of the last settlement, 1h OI change, BTC 1h and 24h return, the coin's 24h return. Model: sklearn
HistGradientBoostingRegressor on net R (max_iter 300, learning rate 0.05, max_leaf_nodes 31, min_samples_leaf 200). The threshold is
the 90th percentile of out-of-fold predictions on DEV (3 time-ordered folds). Trained on DEV and tested on VAL; then retrained on DEV+VAL
and tested once on FINAL and UNSEEN, same pass rule as above (non-overlap applied per coin after selection).

## What happens next
If nothing passes: say so, report the best scenario x regime cells with their $10 results, and do not loosen anything. If something
passes: a paper forward test like the Kalman engine's, not live money.

## Addendum 2 (written after the DEV screen and the pre-registered final runs; VAL/FINAL/UNSEEN of these new cells not yet examined)
**What DEV showed.** 0 of 3200 cells passed (best t +1.1). Before costs, the 25 setups pooled have a gross edge of -0.005R (t -1.0),
the same as random entries, at every stop width; the best setups make about +5 bps per trade against 14 bps of cost
(`r2_gross_dev.txt`). The setups do not predict direction on 5m / 15m bars, so tuning their stops cannot help.

**One more test: HTF-aligned scalps ("scale-in entries for the 4H trend").** The one rule with an out-of-sample edge in this project
is the production Kalman trend on 4H bars (`hf_ladder.py`, rung 240). Stage B takes every setup event whose entry falls inside a 4H
Kalman trade (production rules incl. BTC daily filter and funding filter, `hf_ladder.coin_rung(..., 240)`), in the same direction.
Regime = KALMAN (aligned) only; cells = 25 setups x 2 sides x 2 timeframes x 2 filters x 4 exits = 800, with the same DEV screen
(n >= 150, net R > 0, t >= 3.0), VAL confirmation (n >= 50, net R > 0, t >= 2.0), and pass rule. Also reported: the same events
pooled over all setups (one position per coin) per timeframe and exit, and the best DEV cell per scenario x side, frozen and run once.
Expectation written down now: the aligned events earn the 4H drift for a few hours, a few bps, so they fail on cost.
