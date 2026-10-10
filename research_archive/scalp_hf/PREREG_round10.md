# Round 10: order-book imbalance from Binance bookDepth (pre-registration, written before any bookDepth file was opened)

**Why.** The institutional-execution report (attached by the user) names order-book imbalance as the main short-term pressure signal. Rounds 1-9 had
no book data. `bookDepth` (free, `data.binance.vision`) gives, every ~30 s, the bid and ask depth within 0.2% / 1% / 2% / 3% / 4% / 5% of the mid price;
we keep the last snapshot of each 5-minute bin (`fetch_bookdepth.py`), 2023-01-01 to 2026-10-08, 30 coins. It is coarse: no top of book, no micro-price.
Expectation written down now: little. Book signals at these horizons are normally worth a few bps against a 14 bps round trip; the fair question is whether
the imbalance carries information about the next 30-240 minutes that no earlier signal had, and whether it improves the two systems that do work.

## Definitions (all causal)
- A 5-minute bin starting at T holds the last snapshot taken before T+5min; it is treated as known at **T+5min**, and a trade enters at the next 1m open.
- For level L in {0.2, 1, 5} (%): bid = notional at -L, ask = notional at +L. **IMB_L = (bid - ask) / (bid + ask)** (-1..+1).
  **Z_L** = IMB_L divided by its trailing 7-day standard deviation (taken from bins before this one), the value used for thresholds.
  **THIN** = (bid + ask at 1%) / its trailing 7-day mean (a liquidity vacuum when low).

## Part 1: signal quality (descriptive, selection years only)
Forward return (bps, from the entry open) at 30, 120 and 240 minutes by quintile of Z_L, for each L, pooled over coins, day-clustered t of the top-minus-bottom
quintile spread, in SELECTION-A and B. Reported in full, whether or not anything works. Also: does Z_1 predict the next 30-minute absolute return (volatility)?

## Part 2: trading cells (24)
Entry when |Z_L| >= K at a bin, **direction WITH** the imbalance (long if bids dominate) or **AGAINST** it; hold H minutes; stop 3 ATR14 (30m bars) (min 0.5%);
14 bps + funding; one position per coin; cooldown = H.
L in {0.2, 1, 5} x K in {1.5, 2.5} x H in {30, 120} x direction {WITH, AGAINST} = 24 cells.

## Part 3: as a filter on the systems that work (4 cells)
(a) the round-7 panel (30-minute bars, THETA 1.0, K 8, slow exit): skip a signal when Z_1 is against its side by >= 1.0 (or 2.0); (b) the same for the 4H
Kalman trend signals (production rules). Paired: the same signals, with and without the filter; the filtered-out ones are what the filter removes.
Report mean R kept vs removed. Cells: 2 systems x 2 thresholds.

## Sets and pass rules (as rounds 6-9)
- **SELECTION-A:** design coins (BTC ETH SOL XRP DOGE BNB ADA LINK AVAX LTC) 2023-01..2024-06; **SELECTION-B:** 2024-07..2025-06. (Book data starts 2023-01.)
- **Part 2 selection:** n >= 300 in A+B, net R > 0 in A and in B separately; the best A+B t per direction (WITH, AGAINST) is frozen.
- **Judges, run once:** J1 design coins 2025-07..2026-09; J2 unseen coins (DOT NEAR ATOM APT ARB OP SUI INJ TIA WLD); J3 fresh coins (AAVE BCH CRV ETC FIL HBAR ICP RUNE TRX UNI,
  2023-01..2026-09; the rules are fixed, not learned, so the date overlap only means J3 is a coin test, not a time test).
- **Part 2 pass (all):** J3 n >= 300, net R > 0, t >= 2.0; J1 and J2 net R > 0 (n >= 30); $10 above $10 in all three with max drawdown < 40%; J3 net R > 0 with +2 bps slippage.
- **Part 3 pass:** in each of J1, J2, J3 the kept signals have a higher mean R than the removed signals, pooled difference t >= 1.5, and the $10 portfolio with the filter beats the one without in at least 2 of 3.
- Always reported: trades, trades per day, gross bps, net R, $10 with trades taken and drawdown; the baseline systems on the same sets.
- Chance of a no-edge Part 2 cell passing selection is about 0.25; 24 cells, so the judges decide.
