# Open interest, long/short ratios and taker flow as crowding filters: pre-registration
Written 2026-10-09, before any of this data was downloaded or compared with trade results.

**Why.** Funding, a crowding measure, gave the only short filter that held up (SHORT_FUND beat the short benchmark in 4 of 4 holdouts
and in 7 of 7 years). This test asks whether other crowding data, which we have never looked at, adds information beyond funding.

## Frame
- **System under test:** the final funding-aware Kalman trend (`longshort/README.md`).
  - Kalman z crosses ±1 on 4H, in the direction of BTC's 1D trend.
  - Shorts need the last 9 funding payments to average > 0; longs are skipped when they average > 0.03%.
  - 3-ATR stop, exit when z crosses back through 0 (120 bars max).
- **Trades:** the same simulated trades (`ls_entries.pkl`: 36 OKX coins, 2020-01 to 2026-09, 14 bps, real Binance funding). Nothing is re-simulated.
- **Data:** Binance USDT-M `futures/um/daily/metrics` (data.binance.vision), 5-minute rows, mapped to coins as for funding (POL: POLUSDT, then MATICUSDT).
  - Fields used: open interest in coins (`sum_open_interest`), the all-account long/short ratio (`count_long_short_ratio`), and the taker buy/sell volume ratio (`sum_taker_long_short_vol_ratio`).
  - Aggregated to hourly rows: the last snapshot of each hour for levels, the mean of the log taker ratio for flow.
- **Causality:** for a signal on the 4H bar opening at t (closing at t + 4h, entry at the next open), only hourly rows whose hour ends by t + 3h are used. That leaves one hour of margin before the signal close.
- **Gaps:** a window needs at least 90% of its hourly rows, otherwise the feature is missing.

## Features
| name | definition |
|---|---|
| OI24 | ln(OI now / OI 24 hours earlier) |
| OI30 | ln(OI now / mean OI over the last 30 days) |
| LSR | ln(retail long/short account ratio now / its 30-day median) |
| TK24 | mean of ln(taker buy volume / taker sell volume) over the last 24 hours |

## Filters (keep the trade only if the condition holds; thresholds are the natural zero points, nothing is tuned)
| family | name | keep if | idea |
|---|---|---|---|
| primary: shorts | S_RETAIL_LONG | LSR > 0 | retail is more long than usual: crowded longs to liquidate, the same logic as funding |
| primary: shorts | S_OI_BUILD | OI24 > 0 | positions are being added into the fall: fresh fuel for a liquidation cascade |
| primary: shorts | S_OI_HIGH | OI30 > 0 | leverage above its monthly normal |
| primary: shorts | S_TAKER_SELL | TK24 < 0 | aggressive sellers in control over the last day |
| secondary: longs | L_RETAIL_SHORT | LSR < 0 | mirror of S_RETAIL_LONG (squeeze fuel) |
| secondary: longs | L_OI_BUILD | OI24 > 0 | mirror of S_OI_BUILD |
| secondary: longs | L_OI_HIGH | OI30 > 0 | mirror of S_OI_HIGH |
| secondary: longs | L_TAKER_BUY | TK24 > 0 | mirror of S_TAKER_SELL |

## Segments (as in the earlier tests; open-interest data starts 2020-09 for BTC, later for other coins)
| segment | coins | period | role |
|---|---|---|---|
| DEV | 19 design coins | 2021-06 to 2023-12 | reported only (nothing is chosen) |
| VAL | design coins | 2024 | holdout |
| FINAL | design coins | 2025-01 to 2026-09 | holdout |
| UNSEEN | the other 17 coins | 2021-06 to 2026-09 | holdout |
| OLD | all coins | 2020-09 to 2021-05 | holdout |

## Statistics and pass rule
- **Unit:** net R per signal (fees and real funding included), for all signals of the final system before the 8-position limit, one per coin, with day-clustered standard errors (`prereg_run.tstat`). Only signals with the feature available enter.
- **Benchmark:** all of that side's signals with the feature available (kept plus removed).
- **PASS** needs both:
  - **separation:** t of (kept − removed), pooled over the 4 holdouts, ≥ 2.0, with t = (mean kept − mean removed) / √(se kept² + se removed²);
  - **consistency:** kept average > benchmark average in at least 3 of the 4 holdouts.
- **PASS strict:** the same with t ≥ 2.5, which allows for testing 8 filters.
- **Reported with every filter (not part of the rule):** the $10 portfolio (0.5% risk, max 8 open, one per coin), final system against final system + filter.
  - Signals without data stay as in the final system.
  - Compounded 2020-01 to 2026-09, plus per year, with trade counts and max drawdown.
- **For information only:** the same filters on the BASE system (no funding filters), to see whether these measures substitute for funding.

## Adoption
- A filter that passes becomes a record-only forward-lab candidate, if the user approves.
- A filter that fails is not used.
- If several pass, they are not combined without a new pre-registered test.

## Checks before any result is read
- **Timestamps:** the implied price (OI value / OI) must track OKX's 1H close best at lag 0.
- **No look-ahead:** features recomputed after deleting all data past the cut-off must be identical.
- **Coverage:** signals with features, per segment and side.

## Known caveats, stated before running
- Binance positioning stands in for the whole market, as with funding.
- Open interest and the long/short ratio are related to funding. The test runs on top of the funding filters, so a pass means information beyond funding.
- Samples are small (about 1,200 short signals in total), so a filter needs a large effect to pass.
- The holdout years' market regimes were seen in earlier work, but this data never was.
