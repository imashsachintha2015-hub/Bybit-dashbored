# Open interest, long/short ratios and taker flow as crowding filters (pre-registered)

**Question.** Funding gave the only short filter that held up. Does other crowding data add information beyond funding?

**Rules.** `PREREG_openinterest.md` was committed before the data was downloaded. Its coverage addendum was committed after the file listing and before any file was opened.

## Data (`fetch_oi.py`)
- **Source:** Binance USDT-M `futures/um/daily/metrics` from data.binance.vision, 5-minute rows, 57,980 daily files for the 36 coins.
- **Coverage:** BTC from 2020-09-01; most coins from 2021-12-01; newer listings later.
- **Symbol mapping:** POL uses POLUSDT, with MATICUSDT filling the hours before POLUSDT existed. Its open-interest features around the September 2024 switch are distorted.
- **Hourly rows:** the last snapshot of each hour for open interest and the long/short ratio (at minute 55), and the mean of the log taker buy/sell ratio for flow.
- **Safety:** downloaded files are handled as data only. Listing keys are validated against the exact expected file name, and the zips are read in memory by `python3 -I`.

## Features (`oi_feat.py`), causal
- **Cut-off:** for a signal on the 4H bar opening at t, only hourly rows ending by t + 3h are used, one hour before the signal close.
- **Gaps:** a window needs at least 90% of its rows.

| feature | definition |
|---|---|
| OI24 | ln(open interest now / 24 hours earlier) |
| OI30 | ln(open interest now / its 30-day mean) |
| LSR | ln(retail long/short account ratio now / its 30-day median) |
| TK24 | mean ln(taker buy / taker sell volume) over 24 hours |

**Data gaps.** TON's and MKR's usable rows end early (2026-06-23 and 2025-09-08); later signals count as having no data.

## Checks before any result was read (`oi_checks_out.txt`): all passed
- **Timestamps:** BTC, ETH, SOL, XRP and DOGE all match OKX's 1H close best at lag 0.
  - At lag 0 the median gap is 3.8-4.4 bp and the 1h return correlation 0.95-0.97.
  - At ±1 hour the gap is 22-44 bp and the correlation about 0.
- **No look-ahead:** 400 of 400 sampled signals gave identical features after all later rows were deleted, and again after they were scrambled.
- **Coverage:** OLD (2020-09 to 2021-05) has only 2 short and 9 long signals with data, so it is not counted.

## Result (`oi_test_out.txt`, one run)
- **Unit:** average net R per signal of the final system, pooled over the holdouts VAL, FINAL and UNSEEN.
- **t:** the kept-minus-removed separation.

| filter | kept | removed | t | holdout wins | verdict |
|---|---|---|---|---|---|
| S_RETAIL_LONG | +0.027 (476) | −0.001 (275) | +0.18 | 2/3 | fail |
| S_OI_BUILD | +0.041 (462) | +0.013 (329) | +0.21 | 1/3 | fail |
| S_OI_HIGH | +0.044 (413) | +0.018 (357) | +0.18 | 1/3 | fail |
| S_TAKER_SELL | −0.011 (578) | −0.028 (169) | +0.12 | 2/3 | fail |
| L_RETAIL_SHORT | +0.400 (816) | +0.162 (473) | +1.07 | 2/3 | fail |
| L_OI_BUILD | +0.346 (844) | +0.249 (445) | +0.43 | 2/3 | fail |
| **L_OI_HIGH** | **+0.571 (681)** | **+0.023 (608)** | **+2.52** | **3/3** | **PASS strict** |
| L_TAKER_BUY | +0.328 (442) | +0.312 (840) | +0.07 | 2/3 | fail |

About 0.05 strict passes were expected by luck across 8 tests.

**L_OI_HIGH by holdout** (kept vs removed): VAL +1.263 vs +0.202, FINAL +0.321 vs −0.034, UNSEEN +0.406 vs −0.039. **DEV, reported only, disagrees:** +0.331 vs +0.362.

**$10 portfolio** (0.5% risk, max 8 open, one per coin; signals without data stay as in the final system):

| system | 2020-2026 | 2024-2026 |
|---|---|---|
| final system | $94.12 (1,616 trades, DD −28%) | $19.37 (765 trades, DD −19%) |
| + L_OI_HIGH | $93.56 (1,472 trades, DD −21%) | $19.60 (683 trades, DD −19%) |

- Some failed filters show higher portfolio numbers: L_OI_BUILD $100.71 and S_OI_HIGH $100.30. Those came from a few years and are not supported by the per-signal test.
- L_OI_HIGH keeps the money the same with 9% fewer trades and a smaller drawdown. The longs it removes earned about zero.

## Exploratory follow-up on L_OI_HIGH (`oi_followup_out.txt`, not part of the rule)
- **Not an outlier effect.** Kept minus removed in the holdouts:

| trades dropped from each group | gap |
|---|---|
| none | +0.549R |
| best 1% | +0.478R |
| best 2% | +0.433R |
| best 5% | +0.339R |

- **Win rate:** 37% kept vs 28% removed.
- **Every year:** kept beat removed in 2022 through 2026, all coins with data (2023 +0.490 vs +0.206, 2024 +0.901 vs +0.189, 2025 +0.189 vs −0.262, 2026 +0.707 vs +0.344).
- **Design coins in DEV:** no effect.

## Conclusion
- **Open interest, the long/short ratio and taker flow do not help shorts beyond funding** (all four separations t ≤ 0.21). Shorts stay unproven.
- **For longs, "open interest above its 30-day mean" passed the strict bar.** It reduces drawdown rather than adding money.
- **Next step:** under the pre-registration it becomes a record-only forward-lab candidate if the user approves.
  - The filter compares open interest with its own 30-day mean. A live version could use Bybit's own open interest, but that venue differs from the tested Binance data.
