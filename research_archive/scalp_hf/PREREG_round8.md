# Round 8: three out-of-the-box scalping mechanisms (pre-registration)

**Request.** "Create a situation, a method for scalping: creative, out of the box, advanced."

**What is already known (rounds 1-7).** Pattern setups on 1-15 minute bars have no edge before fees. Estimator panels on fixed 15/30
minute bars: the entry is only useful with a slow exit; the best 30-minute cell is +0.02 to +0.06R and fails the pass rule; 15 minutes
loses. A trade needs a gross move of well over 14 bps; moves of that size come from information arriving, forced flow, or a trend.
These three mechanisms are chosen because each attacks a different reason the earlier rounds failed.

## M3. SHOCK: the market-wide contagion situation (cheapest, run first)
**Idea.** When many coins jump in the same 5 minutes, the cause is common (news, a macro print, a cascade of liquidations), the moves
are large (hundreds of bps), and some coins have not reacted yet. Question: after a synchronized shock, do coins continue, reverse, or
do the laggards catch up?
- 5-minute bars of a 10-coin group (design / unseen / fresh groups are each used as their own group). Per coin, return r = log
  close/close(-1); sigma = exponentially weighted std of r (span 2016 bars = 7 days) taken before the bar; z = r / sigma.
- Breadth: UP = number of coins with z >= 2.5, DOWN = number with z <= -2.5 (need >= 6 valid coins). An event is UP >= N with DOWN < N (or the
  mirror). No new event within 60 minutes of the previous one in the group.
- Entry at the next 1m open after the 5m bar. Stop 3 ATR14(5m) (min 0.3% from the entry). Time exit H minutes after the entry. 14 bps +
  real funding. One position per coin.
- **Cells (12):** N in {6, 8} x response in {CONT (every valid coin in the shock direction), FADE (every coin against it), LAG (only coins
  with |z| < 1.0, in the shock direction)} x H in {15, 60} minutes.

## M1. DC: a volume clock (dollar bars) with the sensitive panel
**Idea.** Fixed-time bars mix quiet hours and bursts. If a bar closes whenever a fixed amount of money has traded (more bars when it is
active, fewer when it is quiet), the estimators see events. Uses the round-7 panel and slow exit unchanged, so only the clock changes.
- Per coin, a bar closes at the 1m bar where the cumulative quote volume since the last close reaches V = (mean daily quote volume of the
  previous 20 complete days) / D. Bars with a duration over 24 hours are invalid. The first 20 days of a coin are skipped.
- On those bars the nine round-7 estimators (Kalman, SuperSmoother, KAMA, Laguerre, HMA, ZLEMA, MAMA, Fisher, taker flow) vote with
  THETA 1.0; entry when the score reaches +-K; stop 3 ATR14 of the bars (min 0.5%); exit when the slow Kalman (drift variance 1e-4) z of the
  same bars crosses 0; 120 bars at most; BTC daily trend and funding rules as production; 14 bps + funding. Entry at the 1m open after the
  closing minute.
- **Cells (4):** D in {48, 96} bars per day (about 30 and 15 minutes on average) x K in {6, 8}.
- Reference: the same panel on fixed-time bars (round 7: 30m, THETA 1.0, K 8, slow exit; 15m as well).

## M2. KNN: an analog forecaster of the market state
**Idea.** Rather than a rule, ask: of all past moments (all coins, all years) that look like now, what happened next 60 minutes?
- Every 60 minutes per coin, a 12-dimensional state: returns over 30m / 2h / 8h / 24h (each divided by the trailing 7-day volatility scaled to
  the horizon), 2h/24h volatility ratio, 1h taker imbalance, 4h open-interest change (0 if missing), last funding rate, hour of day (sin, cos),
  BTC's 2h return, distance to the daily VWAP in sigmas. Standardised on SELECTION-A.
- Target: the return from the next open to the close 60 minutes later, in bps. Prediction = mean of the 400 nearest SELECTION-A neighbours
  (design coins, 2021-01..2024-06, one sample every 60 minutes per coin). The model is trained once on SELECTION-A and never refitted.
- Trade when |prediction| >= TAU, in the predicted direction; stop 3 ATR14(30m) (min 0.5%); time exit at 60 minutes; 14 bps + funding.
  **Cells (2):** TAU in {15, 25} bps.

## Sets and pass rules (as rounds 6-7)
- **SELECTION-A:** design coins (BTC ETH SOL XRP DOGE BNB ADA LINK AVAX LTC) 2021-01..2024-06; **SELECTION-B:** 2024-07..2025-06.
- **Selection:** n >= 150 (M3, M2) or >= 300 (M1) in A+B, net R > 0 in A and in B separately; the highest A+B t is frozen: one per response
  type for M3 (CONT, FADE, LAG), one for M1, one for M2.
- **Judges (run once on frozen cells):** J1 design coins 2025-07..2026-09; J2 unseen coins (DOT NEAR ATOM APT ARB OP SUI INJ TIA WLD);
  J3 fresh coins (AAVE BCH CRV ETC FIL HBAR ICP RUNE TRX UNI, 2021-2026; shares regimes with selection, so a coin test; J1 and J2 are the time tests).
- **Judge pass (all):** J3 n >= 150 (M1: 300), net R > 0, day-clustered t >= 2.0; J1 and J2 net R > 0 with n >= 30; $10 above $10 in all three
  with max drawdown < 40%; J3 net R > 0 with +2 bps slippage per side.
- Always reported: trades, trades per day, gross bps per trade, net R, $10 equity with trades taken and drawdown, for every frozen cell.
- Chance of a no-edge cell passing selection: about 0.25; the judges decide. Cells: 12 + 4 + 2 = 18.
