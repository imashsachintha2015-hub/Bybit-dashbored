# Round 10: order-book imbalance (Binance bookDepth, free)

**Answer.** No pass. The book imbalance carries almost no direction information on 5-minute-to-4-hour horizons, and it does not improve either of the two systems that work.

Rules: `PREREG_round10.md`, committed before any data file was opened. Code: `r10_book.py` (causal test passed: alignment, and no value changes when later book data is cut). Data: `fetch_bookdepth.py`, 30 coins, 2023-01 to 2026-10, last snapshot of each 5-minute bin, known at bin start + 5 minutes.

**Deviation from the plan:** Binance publishes the ±0.2% depth level only from 2026. The 12 cells at L = 0.2% therefore have no data in selection sets A and B and could not qualify. Only L = 1% and 5% were tested (12 cells).

## Part 1: signal quality (design coins, `r10_part1_out.txt`)
Forward return (bps, long) by quintile of the imbalance z-score, top minus bottom:

| Level, horizon | Set A (2023-01..2024-06) | Set B (2024-07..2025-06) |
|---|---|---|
| 1%, 30 min | +0.5 (t +1.2) | +0.4 (t +0.6) |
| 1%, 120 min | −3.7 (t −2.8, reversal) | +0.2 (t +0.1) |
| 1%, 240 min | −8.5 (t −3.4, reversal) | +2.2 (t +0.6) |
| 5%, 30 min | +0.7 (t +1.5) | +0.9 (t +1.4) |

- At 30 minutes the effect is under 1 bp against a 14 bps fee. At 120–240 minutes the sign flips between the two sets, so it is not stable.
- A thin book (the 1% depth far below its 7-day mean) does predict larger moves: the thinnest quintile has about 35% bigger absolute 30-minute returns (44 vs 33 bps in A, 55 vs 40 in B). That is volatility, not direction.

## Part 2: 24 trading cells (`r10_select_out.txt`)
Entry when |z| ≥ K, with or against the imbalance, hold 30 or 120 minutes, stop 3 ATR(30m), 14 bps + funding. All 12 testable cells lose in both A and B: net R −0.06 to −0.09, gross edge −3 to +2 bps per trade, A+B t from −12 to −53. Against-imbalance cells have the negative gross edge, with-imbalance cells about +1 bp. **No cell qualified, so no judge was run.**

## Part 3: imbalance as a filter (`r10_part3_out.txt`)
Skip a trade when the book leans against it by z ≥ 1 (or 2). Sets from 2023-01 on.

| System | Filter | Kept vs removed (pooled) | Difference t | $10 filtered beats plain |
|---|---|---|---|---|
| 30-min panel | z ≥ 1 | +0.048R vs −0.023R | +0.78 | 2 of 3 |
| 30-min panel | z ≥ 2 | +0.036R vs −0.022R | +0.54 | 3 of 3 |
| 4H Kalman | z ≥ 1 | +0.195R vs +0.234R | −0.21 | 1 of 3 |
| 4H Kalman | z ≥ 2 | +0.190R vs +0.340R | −0.64 | 1 of 3 |

The pass bar was a pooled difference t ≥ 1.5 with kept above removed in each set. No filter passes: the panel filter is not significant and on the fresh coins at z ≥ 1 the kept trades are worse than the removed ones; for the Kalman system the removed trades are, if anything, the better ones. **The Kalman system should not use a book filter.**

## What this adds to ten rounds
Order-book depth at the 1% and 5% levels is not an edge for taker trading at these horizons. The one thing it does show, a thin book coming before large moves, is a volatility signal that the existing systems do not need. The remaining open lever is cost (fee tier or maker fills), not signal.

Files: `PREREG_round10.md`, `r10_book.py`, `fetch_bookdepth.py`, `r10_part1_out.txt`, `r10_select_out.txt`, `r10_frozen.json`, `r10_part3_out.txt`.
