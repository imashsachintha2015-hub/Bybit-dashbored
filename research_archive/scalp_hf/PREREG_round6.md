# Round 6: the fastest version of the trend rule that still pays (pre-registration)

**Request.** "I want something for scalping."

**What is already known.** Rounds 1-4: no setup on 1-15 minute bars beats the 14 bps round trip (0 of about 4,000 variants). Round 5: the
4H Kalman trend is positive on 30 coins. The old frequency ladder (`hf_ladder.py`, one parameter set for every bar size) found 4H and 2H
profitable, 1H mixed, 30m and faster flat to negative. That ladder never tuned the rule to the bar size. A faster bar needs a different
setting (a wider entry threshold, a stronger Kalman drift, a different stop) because noise per bar and cost per ATR change.

**Question.** For bar sizes 15, 30, 60 and 120 minutes, is there a parameter set of the same Kalman trend rule that is profitable out
of sample on coins it was not tuned on, and how many trades per day would it give?

## Rule family (everything else as production: `hf_ladder.py`)
Kalman local linear trend on log closes (span 100), z = slope / its standard deviation; entry when z crosses +-Z_IN in the direction of
BTC's daily trend (with the funding rules); stop STOP_ATR x ATR14 from the signal close (min 0.5% from the entry); exit at the next open
after z crosses 0, 120 bars at most; 14 bps + real funding; one trade per coin; 0.5% risk, 8 open at most.

## Grid (48 cells)
- Bar size B in {15, 30, 60, 120} minutes.
- LAM (Kalman drift variance) in {1e-4, 1e-3}.
- Z_IN in {1.0, 1.5, 2.0}.
- STOP_ATR in {2, 3}.
The production setting (LAM 1e-4, Z_IN 1.0, STOP_ATR 3) is one of the cells for every B and is the reference.

## Sets
- **SELECTION-A:** design coins (BTC ETH SOL XRP DOGE BNB ADA LINK AVAX LTC), entries 2021-01..2024-06. **SELECTION-B:** same coins, 2024-07..2025-06.
- **Judges, run once on frozen cells:** J1 = design coins 2025-07..2026-09; J2 = unseen coins (DOT NEAR ATOM APT ARB OP SUI INJ TIA WLD);
  J3 = fresh coins (AAVE BCH CRV ETC FIL HBAR ICP RUNE TRX UNI, 2021-01..2026-09; their regimes overlap SELECTION in time, so J3 tests
  coin-specificity; J1 and J2 are the time tests).

## Selection and pass rules
- **Selection (per bar size):** cells with n >= 300 in A+B, net R > 0 in A and in B separately. The cell with the highest A+B t is frozen. If
  none qualifies, that bar size is reported as "no profitable setting".
- **Chance:** a cell with no edge passes both signs with probability about 0.25; 12 cells per bar size give about 3 qualifying by chance,
  so the judges are the real test.
- **Judge pass (all):** J3 n >= 300, net R > 0, day-clustered t >= 2.0; J1 and J2 net R > 0; $10 above $10 in all three with max drawdown
  below 40%; net R > 0 with +2 bps slippage per side in J3.
- **Reported always:** the frozen cell and the production reference for each bar size, in all three judges: trades, trades per day (all
  coins together), win rate, net R, $10 final equity, trades taken and max drawdown.
- **Parity:** the production setting at 240 and 60 minutes must reproduce `hf_ladder.coin_rung` exactly before anything is run.
