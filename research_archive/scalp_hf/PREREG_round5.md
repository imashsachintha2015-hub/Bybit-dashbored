# Round 5: where do trends pay? A situation map, a "trend birth" indicator set, and level-based add-ons (pre-registration)

**Request.** "Search for any situations where we can make profit. Focus on trends, combine all knowledge, create something unique:
indicators, levels we can identify trades."

**What is already known** (`longshort/`, `situations/`, `lowfreq_trend/`, `README_round4.md`):
- The funding-aware 4H Kalman trend is the only rule that survived every holdout: +0.29R per trade, 35% win rate, 7% of trades reach 3R or more
  and carry the result.
- Exits cannot be improved (8 profit-taking variants all lose); generic chop filters fail; a fresh trend out of a range did better than a
  late entry into an orderly move.
- 1,464 of 3,080 signals were skipped because the 8 slots were full: the system is capacity-limited.

**The idea.** Keep the one proven edge and ask which *trend situations* are worth more or less, using indicators built around the
trend's birth (the bar where the Kalman z last crossed 0): how far it has run, what the average participant paid, whether the move
breaks real levels, and whether new money or short-covering drives it. Then test whether (a) those situations can size the risk and
(b) pullbacks to trend-anchored levels give extra entries inside a trend that is already working.

## Data and sets (as the earlier rounds; 4H bars from 1m, real funding, 14 bps round trip)
- Production signals and trades = `hf_ladder.coin_rung(..., 240)` (z crosses ±1, BTC daily trend filter, SHORT_FUND / LONG_FUND_CAP funding
  rules, stop 3 ATR (min 0.5%), exit at z crossing 0, 120 bars, one trade per coin).
- **SELECTION-A:** design coins (BTC ETH SOL XRP DOGE BNB ADA LINK AVAX LTC), entries 2021-01..2024-06. **SELECTION-B:** design coins, 2024-07..2025-06.
- **Judges, each run once on frozen rules:** J1 = design coins FINAL (2025-07..2026-09; the baseline results of these were seen in earlier
  rounds, the new rules were not); J2 = UNSEEN coins (DOT NEAR ATOM APT ARB OP SUI INJ TIA WLD, 2025-06+; seen for baseline only);
  J3 = HOLDOUT2 coins (AAVE BCH CRV ETC FIL HBAR ICP RUNE TRX UNI, 2021-01..2026-09; never used for a trend rule). Caveat written now:
  J3 shares market regimes with SELECTION in time (crypto coins move together), so it tests coin-specificity, not regime change; J1 and J2
  are the time tests.
- The baseline is reported on every set whether or not it is any good.

## Part 1: the trend-birth indicators (11, all computed at the signal bar's close, signed so that + means "in the trade direction")
birth b = first bar of the current run of the same-sign z. Using 4H bars; ATR = ATR14 (RMA) at the signal bar.
1. AGE = signal bar − b (bars).
2. MAT = side × (close − close_b) / ATR (how far the trend has already run).
3. AVD = side × (close − AVWAP_b) / ATR, AVWAP_b = volume-weighted average typical price from b to the signal bar (what the average
   participant of this trend paid).
4. RNG = (highest high − lowest low of the last 60 bars) / ATR (compression: low = coiled).
5. BRK = 0 / 1 / 2: the signal close is beyond the prior 180-bar (30-day) extreme / beyond the prior 540-bar (90-day) extreme (in direction).
6. VP = side × (close − VAH) / ATR for longs (VAL for shorts) from the previous 180 bars' volume profile (60 price bins, 70% value area):
   outside value in the trade direction when > 0.
7. OI24 = log(OI now / OI 24 h ago) (not signed; rising = new positions, falling = covering); missing before 2021-12 → its own group.
8. CVD = side × taker imbalance (2·taker-buy − volume)/volume over the last 6 bars.
9. BREADTH = share of the coin group with side × z > 0 at the signal bar.
10. VOLR = (ATR/close) / its median over the previous 540 bars.
11. ZJUMP = side × (z_i − z_{i-1}).

**Analysis (SELECTION only).** Cut points of terciles are fixed on SELECTION-A (BRK and the OI missing flag are categorical). For every
indicator: net R by group in A and B. **Accepted as a "danger" group** only if its A t ≤ −2.0 and its B mean R < 0 and skipping it raises
the mean R of the remaining trades in both A and B. **Accepted as a "bonus" group** with the mirrored rule (A t ≥ +2.0, B mean > 0,
removing everything else would not be needed: it is used only for sizing). At most 2 danger and 2 bonus groups in total (strongest A |t|).
Chance of an indicator-group passing by luck: about 0.02 x 0.5 x 33 groups = 0.3 passes expected under no effect.

**Rules built from them (frozen after SELECTION):**
- SKIP = skip signals in accepted danger groups.
- SIZE = risk multiplier 0.5 for danger groups, 1.5 for bonus groups, 1.0 otherwise (a trade in both: they cancel to 1.0), keeping the
  3x / 6x notional caps. The portfolio is the production one (0.5% base risk, 8 open).
- Both rules are evaluated only if at least one group was accepted.

## Part 2: level-based add-ons inside a running trend
For every production trade (signal bar i, birth b, parent exit bar x), within bars i+2 .. x−2, the first bar j that touches a level and
rejects it (long; mirrored for short): low ≤ level, close > level, close > open. Entry at the next 4H open.
- **Levels (3):** L1 = AVWAP_b (up to bar j); L2 = EMA20 of the 4H closes; L3 = Fibonacci 0.5 of the run from close_b to the highest high
  since b (excluding bar j).
- **Stops (2):** S3 = 3 ATR from the entry (min 0.5%), as the parent; STRUCT = below the lowest low of bars j−1 and j minus 0.5 ATR (min 0.5%,
  skipped if over 8%).
- **Exit:** as the parent (z crosses 0 → next open; 120 bars from the add-on signal at most; stop first inside a bar). 14 bps + real funding.
- One add-on per parent trade per level. 6 cells.
- **Test:** stand-alone net R of the add-ons (day-clustered t). Cells with net R > 0 and t ≥ 2.0 on SELECTION-A and net R > 0 on B are
  frozen (the best by A t per level, at most one stop variant per level). Portfolio: baseline@8 (production), baseline@12, baseline+add-ons@12
  (add-ons take their own slot per coin so they can run alongside the parent). Add-ons are a separate cap only to isolate their effect.
- Chance of a cell with no edge passing: about 0.023 x 0.5 = 1% per cell, 6 cells.

## Pass rules (judges J1, J2, J3, each once)
- **SKIP / SIZE pass:** (a) in each judge the mean R of kept trades is above the mean R of the removed trades (SKIP) or the $10 portfolio is
  above the baseline's (SIZE); (b) $10 above baseline in at least 2 of 3 judges and in the pooled J1+J2+J3 compounding, with maximum
  drawdown not more than 10 points worse; (c) for SKIP, kept − removed pooled t ≥ 1.5.
- **ADD-ON pass:** net R > 0 in each judge with pooled t ≥ 2.0, and "baseline + add-ons @12" above "baseline @12" in at least 2 of 3 judges.
- Everything is reported with $10 results and trade counts, pass or fail. No threshold is changed after seeing a judge.
