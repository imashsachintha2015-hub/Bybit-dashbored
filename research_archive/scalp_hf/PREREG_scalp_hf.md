# Scalp / high-frequency study: pre-registration (written before any of this data was loaded)

**Question.** Is there a scalping setup on 1-minute data that is profitable after realistic costs, out of sample, on coins it was not designed on?

**What was already known (so this is not a repeat).** `scalp_master` used 5m data for 120 days and 1m data for 8 coins over 30 days; `scalp_ml_15m` used 15m for one year. Both found nothing after costs. `README.md` Part I shows the arithmetic: net = N x (edge - cost), and the best edge measured at 15m was 0.26 bps against 4 to 11 bps of cost. Liquidation reversion from bar signatures had inconsistent signs; market making modelled honestly lost to adverse selection. The samples were short. This study uses about 3.75 years of 1m data for 20 coins, new data (taker volume, trade counts, 5-minute open interest, funding times), and new families. Prior expectation: most or all families fail on costs. The rules below say what counts as an exception, and they are not loosened afterwards.

## Data (Binance USDT-M perpetuals archive, data.binance.vision; Bybit prices track it within a few bps)
- 1m klines (open, high, low, close, volume, quote volume, trades, taker-buy quote volume). Derived bars (5m, 15m, 1H...) are built from 1m and aligned to UTC.
- Monthly funding rates; daily `metrics` (5-minute open interest, taker buy/sell volume ratio) for the OI family.
- **Design coins (10):** BTC ETH SOL XRP DOGE BNB ADA LINK AVAX LTC. **Unseen coins (10):** DOT NEAR ATOM APT ARB OP SUI INJ TIA WLD. The unseen coins are loaded but not looked at until the final step.
- **Segments (design coins):** OLD 2021-01..2022-12, DEV 2023-01..2024-06, VAL 2024-07..2025-06, FINAL 2025-07..2026-09. **UNSEEN:** unseen coins, 2025-07..2026-09 only.
- Code is debugged only on BTC and ETH, 2023-01..2023-03. Nothing from VAL, FINAL, OLD or UNSEEN is examined before a rule is frozen.
- Missing minutes are NaN; an event needing a NaN value is skipped; a path that hits a gap closes at the last valid price.

## Conventions
- Decision at the close of a completed bar; entry at the next 1m open; no feature uses a bar that is not closed.
- Gross edge of an event = side x (close after h minutes / entry open - 1), in bps. Horizons h in {1, 3, 5, 10, 20, 30, 60, 120} minutes.
- **Costs.** Taker (T): 5.5 bps fee + 1.5 bps slippage per side = 14 bps round trip, as in every earlier test. Maker (M), as in `limit_orders/lx_core.py`: maker 2 bps per side, a limit entry fills only if price trades through it by max(0.05 ATR, 1 bp), checked before any cancel on that bar; a limit take-profit needs a 0.02 ATR trade-through; stops and time exits are taker. Real funding is charged when a trade spans a settlement.
- Statistics: pooled over coins, **day-clustered** standard errors (clusters = UTC days). A cooldown of 15 minutes per coin after an event. Ties inside a bar: stop first.
- Every test is counted. Every table is reported, not only the winners.

## Study 0 (descriptive, DEV only)
Cost wall: median |move| and ATR in bps by timeframe, and the stop distance at which 14 bps costs 0.1R. Predictability map: correlation of the last m-minute return with the next h-minute return, and of BTC's return with each coin's return at lags 1..10 minutes. It informs the discussion and changes no rule below.

## Stage 1: signal-quality screen (design coins, fixed-horizon exit at market, no stops)
Each family is frozen as below. A "cell" = a family variant x horizon. The cell count K is the multiple-testing budget.
1. **FUND_SETTLE.** Funding settles at 00/08/16 UTC. If last funding is >= f bps (f in {5, 10, 20, 30}) hold the side that receives it, short for positive, long for negative, from 2 minutes before to h minutes after settlement; P&L = price move + funding received. 4 x 4 horizons.
2. **BTC_LEAD.** BTC's return over m minutes (m in {1, 3, 5}) has |z| >= k (k in {2, 3, 4}; z against the trailing 60-minute standard deviation of m-minute returns). Trade each other design coin in BTC's direction; variant "lag": only if that coin's own m-minute move is less than BTC's. 3 x 3 x 2 variants x 4 horizons.
3. **FLOW.** Taker imbalance over n minutes (n in {3, 10, 30}) = (taker buy - taker sell) / total, standardised against a trailing day. |z| >= k (k in {2, 3}). "Continuation": trade with the imbalance. "Exhaustion": the imbalance is at its n-minute extreme with price at an n-minute high/low and volume >= 2x its trailing mean: trade against it. 3 x 2 x 2 variants x 3 horizons.
4. **OI_FLUSH** (5-minute bars). Open interest change over 5 or 15 minutes at or below the coin's trailing 97.5th or 99th percentile of drops, with price moving against it by >= 1 ATR(5m): "flush" = trade the reversal; mirror for squeezes. "Build": OI up at that percentile with price breaking its 30-minute high/low: trade the breakout. 2 x 2 x 2 variants x 3 horizons.
5. **FREQ_LADDER.** The production Kalman trend rules (z entry 1, BTC daily filter, funding filter, stop 3 ATR with 0.5% minimum, exit at z crossing 0, 120 bars) on bars of 4H (control), 2H, 1H, 30m, 15m, 5m. Reported as trades, not fixed-horizon cells (6 cells).
6. **ORB.** Opening range of R minutes (15, 30) at session opens 00:00 UTC, 08:00 UTC and 09:30 New York time; first close beyond the range within 60 minutes, direction of the break. 2 x 3 x 4 horizons.
7. **VWAP_FADE.** Deviation of the close from the rolling 60-minute VWAP, z-scored against its trailing day, |z| >= k (k in {2, 3, 4}); fade; variant "volume falling". 3 x 2 x 4 horizons.
8. **SWEEP.** Price exceeds the prior N-minute high/low (N in {30, 60, 120}) by >= 0.2 ATR(5m) and closes back inside within 3 bars on volume >= 2x the 30-minute mean: trade the reversal. 3 x 2 variants x 4 horizons.
9. **SQUEEZE.** 5m Bollinger bandwidth <= its trailing 3-day 20th percentile, then a close outside the band: trade the break. 2 variants (volume filter) x 4 horizons.
10. **PULLBACK.** 5m EMA 9 > 21 > 55 (or the mirror), pullback to EMA 21, close back over EMA 9. 2 variants (ADX >= 20 or not) x 4 horizons.
11. **RSI2_FADE.** 1m RSI(2) <= 5 (>= 95) with the close outside the Bollinger(20, 2.5) band: fade. 2 variants x 4 horizons.
12. **XSEC.** Every 15 minutes rank the 10 design coins by the past m-minute return in ATR units (m in {15, 30, 60}); trade the bottom 2 long / top 2 short ("reversal") or the reverse ("momentum"). 3 x 2 x 3 horizons.

**Screen pass (all required):** DEV gross mean >= 4 bps (the break-even of the optimistic maker model) with day-clustered t >= 3.6 (Bonferroni for about 300 cells); n >= 300 events; VAL same sign with gross >= 2 bps and t >= 2.0.

## Stage 2: trading rules for survivors, and best-of-family reporting
- **Reporting rule (the standing request):** for every family, the cell with the highest DEV t-statistic (n >= 300) is run through Stage 2 whether or not it passed the screen, so every family gets a $10 result with trade counts. Selection uses DEV only.
- Exit grid, 8 combinations: stop 1 or 2 x ATR(5m) (minimum 8 bps), time exit = the cell's horizon, take-profit none or 1.5R, cost model T or M. The combination with the best DEV net equity (n >= 300 trades) is frozen.
- Portfolio: $10 start, risk 0.5% of equity per trade (1% also reported), one position per coin, at most 5 open, entry at the next open; final equity, trade count, maximum drawdown, trades per month, win rate, net R, day-clustered t.
- Frozen rules are then run on VAL, FINAL, OLD (design coins) and UNSEEN coins, each once.

## Pass for a trading system (all required)
1. FINAL: n >= 500 trades, net average R > 0 after cost model T, day-clustered t >= 2.5.
2. UNSEEN coins: n >= 300, net average R > 0 after T, t >= 1.5.
3. Positive net R in at least 4 of the 5 FINAL quarters.
4. Still net positive with +2 bps slippage per side.
5. $10 compounding at 0.5% risk ends above $10 with maximum drawdown below 40%.
6. Control: random entries with the same exits and trade frequency lose about the cost (checks the simulator).
7. OLD (2021-22) is reported; the same sign is expected but not required.
A system that passes only under cost model M is reported as a "maker-only candidate": it would need live fill-rate evidence before any use.

## What happens next
If nothing passes, the report says so, with the best gross edge per family against its cost. Nothing is loosened after the fact. If something passes, the next step is a paper forward test like the Kalman engine's, not live money.
