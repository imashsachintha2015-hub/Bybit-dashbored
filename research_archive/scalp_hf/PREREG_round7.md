# Round 7: a panel of highly sensitive estimators on 15 and 30 minute bars (pre-registration)

**Request.** "I want a 15 to 30 minute strategy. Try something sensitive, use highly sensitive things like Kalman. You do not have to
depend on that; try new things like that."

**What is already known (measured, `r6_fast.py` data).** The production Kalman trend rule has a real gross edge on 15m and 30m bars
(per trade, before fees): 15m +9 bps (t 2.4 on design coins, t 2.7 on fresh coins), 30m +33 bps (t 3.5) and +21 bps (t 2.6). The round
trip costs 14 bps, so 15m nets about -0.03R and 30m about +0.01R to +0.08R. Trades hold 7 hours (15m) and 14 hours (30m) because the
exit waits for the trend signal to fade. So the open question is whether a **sharper trend signal** lifts the gross edge per trade
well above 14 bps at these speeds.

## The panel: 9 causal estimators (each gives a normalised trend score z; |z| is how strongly it sees a trend)
For each filter F of the log price, slope = F_t - F_{t-1}, z = slope / EWMA root-mean-square of the slope (span 200). The estimators:
1. KAL: Kalman local linear trend (drift variance 1e-3, span 100), its slope state.
2. SS: Ehlers SuperSmoother, period 12.
3. KAMA: Kaufman adaptive moving average (10, 2, 30).
4. LAG: Laguerre filter, gamma 0.4.
5. HMA: Hull moving average, period 14.
6. ZLEMA: zero-lag EMA, period 14.
7. MAMA: Ehlers MESA adaptive moving average (fast 0.5, slow 0.05; Hilbert-transform cycle period), z from (MAMA - FAMA) / its RMS.
8. FISH: Fisher transform of the price position in its 10-bar range (smoothed 0.67), z = Fisher value / its RMS.
9. FLOW: EMA(8) of the taker-buy imbalance (2 x taker-buy volume - volume) / volume, z = EMA / its RMS (order flow, not price).
Each estimator votes +1 if z > THETA, -1 if z < -THETA, else 0. **Score S = the sum of the 9 votes** (-9..+9).

## Rule (everything else as production: z entry replaced by the score)
- Long when S rises to >= K (it was < K at the previous bar), BTC daily trend up, last-9 funding average <= 0.03% per payment; short mirrored
  (S falls to <= -K, BTC daily trend down, funding average > 0). Entry at the next open.
- Stop 3 ATR14 from the signal close (min 0.5% from the entry). Exit at the next open after S falls to <= X (long; mirrored for short),
  or 120 bars at most. One trade per coin. 14 bps + real funding. $10, 0.5% risk, 8 open at most.
- Warm-up of 201 bars as production. Decision at the close of a bar, no look-ahead (checked by a no-repaint test: every estimator's output
  before a cut-off is identical when computed on the cut data).

## Grid (24 cells)
Bar B in {15, 30} minutes; THETA in {0.5, 1.0}; K in {4, 6, 8}; X in {0, 2}.
Reference table (descriptive, not selected): each estimator alone (vote >= 1, THETA 0.5, X 0), gross and net R.

## Sets and rules (as round 6)
- **SELECTION-A:** design coins (BTC ETH SOL XRP DOGE BNB ADA LINK AVAX LTC) entries 2021-01..2024-06; **SELECTION-B:** 2024-07..2025-06.
- **Selection (per bar size):** n >= 300 in A+B, net R > 0 in A and in B separately; the highest A+B t is frozen. None qualifying: "no setting".
- **Judges (run once on frozen cells):** J1 design coins 2025-07..2026-09; J2 unseen coins (DOT NEAR ATOM APT ARB OP SUI INJ TIA WLD);
  J3 fresh coins (AAVE BCH CRV ETC FIL HBAR ICP RUNE TRX UNI, 2021-2026; shares regimes with selection, so a coin test; J1 and J2 are the
  time tests).
- **Judge pass (all):** J3 n >= 300, net R > 0, day-clustered t >= 2.0; J1 and J2 net R > 0; $10 above $10 in all three with max
  drawdown < 40%; J3 net R > 0 with +2 bps slippage per side.
- **Always reported** for the frozen and the best-gross cell of each bar size: trades, trades per day, gross and net R, $10 equity with
  trades taken and drawdown, and the same trades if the round trip cost 10 / 6 / 3 bps (optimistic, same fills).
- Chance of a cell with no edge passing selection: about 0.25 per cell; the judges decide.

## Follow-up rule written now (exploratory, only if a frozen cell has positive net R in J1, J2 and J3)
A maker version: a limit entry at the signal close (round-1 fill model: fills only on a trade-through of max(0.05 ATR(5m), 1 bp) within 3
minutes), 2 bps maker fee on entry, taker exit. Reported but never counted as a pass.
