# Round 9: meta-labeling the sensitive panel on 5 / 15 / 30 minute bars (pre-registration)

**Request.** "Scalping: I need you to try more."

**Where things stand (rounds 1-8).** No rule on 5-30 minute bars is clearly profitable after a 14 bps round trip. The best entry is the
round-7 panel (nine estimators vote, slow Kalman exit): +25 to +33 bps gross per trade on 30-minute bars, about 2 trades a day on 10
coins, net +0.02 to +0.06R. A learned model on market states alone (round 8 analog forecaster) had no information. This round asks a
different question: not "where is price going" but **"which of the panel's own signals will pay?"** (meta-labeling). The panel produces thousands
of signals on fast bars, enough data for a second-stage selector, unlike the 4H system. Expectation written down now: a modest lift
at best; the test is whether any selected population earns well over 14 bps gross at a scale of several trades a day.

## Primary signals (unchanged from round 7, except that busy-ness is applied after selection)
- Bar sizes B in {5, 15, 30} minutes. The nine estimators, vote threshold THETA 1.0, candidate signal when the score reaches +-4 (it was below 4 the bar
  before), with the production BTC-daily-trend and funding gates, stop 3 ATR14 (min 0.5%), exit when the slow Kalman (drift variance 1e-4) z of the
  same bars crosses 0, 120 bars at most, 14 bps + real funding.
- **Every** candidate signal is walked on its own (no "one position at a time" while building the training data). The one-position-per-coin rule is
  applied afterwards, in time order, to the trades the model selects.

## The selector
- Features at the signal bar close (all causal; signed so + = in the trade direction): the score, the nine estimator z values, ATR/price and its ratio to
  its own 30-day average, hour and weekday (sin, cos), the coin's slow Kalman z on 60m and 240m bars and BTC's on 240m (multi-timeframe agreement),
  momentum over 4 / 16 / 64 bars in ATR units, position in the last 24 hours' range, relative volume, last-9 funding average, 24-hour open-interest
  change, and the side.
- Target: the trade's net R (net return / stop fraction, clipped to [-2, 5]). Model: sklearn HistGradientBoostingRegressor (200 iterations, learning rate 0.05,
  15 leaves, minimum 100 per leaf, L2 1.0). Trained once on SELECTION-A, never refitted.
- Rule: take a signal if predicted R >= T, T in {0.0, 0.1, 0.2, 0.3}. **Cells: 3 bar sizes x 4 thresholds = 12.**

## Sets and pass rules
- **SELECTION-A (training):** design coins (BTC ETH SOL XRP DOGE BNB ADA LINK AVAX LTC), signals 2021-01..2024-06. **SELECTION-B (validation):** 2024-07..2025-06.
- **Frozen cell per bar size:** the T with the highest B t (day-clustered) among those with n >= 300 and net R > 0 in B, after the one-position rule.
- **Judges, run once on frozen cells:** J1 = design coins 2025-07..2026-09; J2 = unseen coins (DOT NEAR ATOM APT ARB OP SUI INJ TIA WLD);
  **J3c = fresh coins (AAVE BCH CRV ETC FIL HBAR ICP RUNE TRX UNI) from 2024-07 only.** The fresh coins before 2024-07 are not used: a learned model
  trained on dates that other coins share is contaminated (round 8: +33 bps inside the training years against +1.9 bps after).
- **Judge pass (all):** J3c n >= 300, net R > 0, day-clustered t >= 2.0; J1 and J2 net R > 0 (n >= 30); $10 above $10 in all three with max drawdown < 40%;
  J3c net R > 0 with +2 bps slippage per side.
- **Always reported:** the unfiltered primary population (same trades without the model), the frozen cell's trades, trades per day, gross bps per trade,
  net R, $10 with trades taken and drawdown; realised net R by predicted-R decile on B and on the judges (does the model rank?); the same trades at 10 / 6 / 3 bps
  round trip (optimistic).
- Chance that a cell with no edge passes selection is about 0.25 per bar size; the judges decide.
