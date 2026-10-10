# Round 11: wait for the liquidity sweep to finish, then enter (pre-registration, written before any code ran)

**Request.** "Try that: wait for the liquidity sweep, then enter."

**Where things stand.** Earlier rounds entered on the sweep itself (or on a limit near it) and lost: sweep-and-reclaim setups were -0.11R to -0.36R on unseen coins,
the previous-week-low sweep had +0.12R gross but -0.02R after fees. The cost is the tight stop under the sweep low: 14 bps is a large share of each trade's risk.
This round changes two things: (1) the entry waits for the sweep to be proven finished (a close back inside the level, then a taker-flow confirmation), and (2) the
targets are wider (2R and 4R, up to 48 hours) so the fee is a smaller share of the move. Expectation written down now: little. The question is whether
confirmation (the absorption signature of the new indicator) separates sweeps that reverse from sweeps that continue.

## Definitions (all causal, 15-minute bars, bar known at its close)
- **Level** at bar j: the lowest low (for a low sweep) or highest high (high sweep) of the previous N bars, N in {96, 288} (24 h, 72 h).
- **Sweep bar** j: low[j] < level - 0.1 ATR14 (a high sweep mirrors it). ATR14 is Wilder, measured before bar j.
- **Reclaim bar** k: the first bar in j..j+2 whose close is back above the level (below, for a high sweep). The signal is at the close of k. No reclaim within 3 bars: no signal.
- **Extreme:** lowest low (highest high) over j..k.
- **Confirmation (CONF):**
  - C0: none.
  - C1: taker flow on the reclaim bar turned with the trade: delta ratio (2 x taker buy - volume) / volume >= +0.15 for a long, <= -0.15 for a short.
  - C2: absorption on the sweep bar: volume >= 1.8 x the median of the previous 50 bars and the sweep bar's taker flow was one-sided against the trade (ratio <= -0.25 for a long, >= +0.25 for a short): aggression that failed to hold the break.
  - C3: both C1 and C2.
- **Trade:** market entry at the open after bar k. Stop: extreme -/+ 0.1 ATR14 (minimum 0.5% of price). Target: TM x risk, TM in {2, 4}. Time exit at the close of the 192nd bar.
  Stop checked before target in the same bar; a gap past the stop exits at the open. 14 bps round trip plus real funding. One position per coin.
- **Trend gate TG:** none, or the production BTC daily trend (long only when up, short only when down).

## Cells (32)
N {96, 288} x CONF {C0, C1, C2, C3} x TM {2, 4} x TG {none, BTC trend}. Long and short sweeps are pooled inside a cell.

## Sets and pass rules (as rounds 6-10)
- **SELECTION-A:** design coins (BTC ETH SOL XRP DOGE BNB ADA LINK AVAX LTC) 2021-01..2024-06; **SELECTION-B:** 2024-07..2025-06.
- **Selection rule:** n >= 300 in A+B and net R > 0 in A and in B separately. Frozen: the best A+B day-clustered t within each CONF group (up to 4 cells), so the
  value of confirmation can be read off. The best cell overall among them is the headline.
- **Judges, run once on the frozen cells:** J1 design coins 2025-07..2026-09; J2 unseen coins (DOT NEAR ATOM APT ARB OP SUI INJ TIA WLD), 2021-01..2026-09;
  J3 fresh coins (AAVE BCH CRV ETC FIL HBAR ICP RUNE TRX UNI), 2021-01..2026-09 (fixed rules, nothing learned, so overlapping dates only make J3 a coin test).
- **Pass (all):** J3 n >= 300, net R > 0, t >= 2.0; J1 and J2 net R > 0 (n >= 30); $10 above $10 in all three with max drawdown < 40%; J3 net R > 0 with +2 bps slippage per side.
- **Always reported:** trades, trades per day, gross bps, net R, $10 final equity with trades taken and drawdown, for every frozen cell and for the C0 baseline of the same (N, TM, TG).
- Chance a no-edge cell passes selection is about 0.25; 32 cells, so the judges decide.
