# Improving the long + short Kalman trend system (4H, all coins, 2020-2026, real Binance funding, 14 bps)

**BASE** (from `trendlines/` and `downtrends/`):
- **Signal:** the Kalman trend z crosses above +1 for a long and below −1 for a short, and only in the direction of BTC's 1D trend.
- **Stop:** 3 ATR.
- **Exit:** when z crosses back through 0 (120 bars max).
- **Sizing:** 0.5% risk per trade, at most 8 open trades, one per coin.

**How candidates were judged** (`ls_improve.py`; all nine candidates written in the docstring before running):
- the $10 result, year by year;
- a factor walk-forward, which switches a candidate on in a year only if it had beaten BASE over all earlier years.

## Per-year $10 growth (`ls_improve_out.txt`)
| system | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 | years better |
|---|---|---|---|---|---|---|---|---|
| BASE | 13.85 | 22.45 | 10.84 | 13.34 | 14.75 | 9.01 | 12.28 | - |
| SHORT_FUND (short only when funding > 0) | 14.11 | 22.56 | 10.86 | 13.68 | 14.86 | 9.30 | 13.04 | **7/7** |
| SHORT_HALF | 14.53 | 21.12 | 10.11 | 13.09 | 14.97 | 9.53 | 12.30 | 4/7 |
| SHORT_Z2 / SHORT_QUICK / LONG_PATH / LONG_ONLY | | | | | | | | 3/7 |
| SHORT_BEAR / LONG_LT | | | | | | | | 2/7 |
| NEWTON2 (second engine) | | | | | | | | 1/7 |

**Walk-forward combination** (switch on everything that had looked better so far), 2021-2026: $10 -> $37.23, against BASE $52.95. Stacking filters that were inconsistent hurts.

**SHORT_FUND is the one consistent improvement.** It also beat the short benchmark in all 4 holdouts of the pre-registered downtrend test (`downtrends/dt_run_out.txt`). The reason is mechanical: when funding is positive, longs are crowded and pay the shorts. When funding is negative, shorts are crowded and squeezes hurt them.

## Mirror test for longs (`ls_fundcap.py`; one test, threshold fixed before running)
**LONG_FUND_CAP** skips a long when the last 9 funding payments average more than 0.03% per payment (overcrowded longs).

**Where it acted:** it skipped 295 long signals, all in 2020, 2021, 2023 and 2024, and improved the result in each of those years. In the other three years it had nothing to skip.

| system | $10, 2020-2026 | trades | max DD | $10, 2024-2026 | max DD |
|---|---|---|---|---|---|
| BASE | $76.53 | 1,797 | −33% | $16.90 | −24% |
| + SHORT_FUND | $86.84 | 1,708 | −28% | $18.32 | −19% |
| + SHORT_FUND + LONG_FUND_CAP | **$94.12** | 1,616 | −28% | **$19.37** | −19% |

## Final rules: "funding-aware Kalman trend" (4H)
- **Long:** Kalman z crosses above +1 and BTC's 1D trend is up. Skip the trade if the last 9 funding payments average more than 0.03%.
- **Short:** Kalman z crosses below −1, BTC's 1D trend is down, and the last 9 funding payments average more than 0.
- **Exits and sizing:** the same as BASE.

## How often it trades (`ls_monthly.py`, `ls_monthly_out.txt`)
Final rules, with the same portfolio limits as the $94.12 result (0.5% risk, max 8 open, one per coin), 81 months from 2020-01 to 2026-09 (data ends 2026-09-30):
- **20.0 trades per month on average** (11.1 long, 8.9 short). Median 20, busiest 38.
- 7 months had fewer than 10 trades, all in 2020-21. The quietest was 2020-01, with 0 trades during the 200-bar warm-up.
- 1,616 trades taken from 3,080 signals. All 1,464 skipped signals came while 8 trades were already open.
- Average hold 5.8 days (median 4.7), so about 3.9 trades are open at once on average. 35% of trades win, about 7 winners per month.

| year | trades per month | long | short | winners per month |
|---|---|---|---|---|
| 2020 | 11.2 | 8.9 | 2.3 | 3.7 |
| 2021 | 17.2 | 10.4 | 6.8 | 7.4 |
| 2022 | 19.4 | 1.8 | 17.7 | 6.8 |
| 2023 | 23.1 | 18.2 | 4.9 | 7.2 |
| 2024 | 21.2 | 16.2 | 5.0 | 7.5 |
| 2025 | 24.2 | 12.6 | 11.7 | 7.2 |
| 2026 (9 months) | 24.4 | 9.1 | 15.3 | 9.0 |

- Fewer coins had data in 2020, which is why that year trades less.
- The BTC filter switches the system between long months and short months.
- At the end of the data, 7 trades opened in 2026-09 were still open. They are valued at the last price, as in the $94.12 result.

## Do the shorts earn their place? (`ls_sides.py`, `ls_sides_out.txt`, exploratory)
The final system split by side, after 14 bps and real funding:

| | longs | shorts |
|---|---|---|
| average per signal | +0.423R (t 3.5, n 1,883) | +0.088R (t 1.2, n 1,197) |
| without the best 1% of trades | +0.228R | +0.038R |
| dollars made inside the $10 → $94.12 portfolio | +$75.62 (898 trades) | +$8.50 (718 trades) |

Shorts by year (average R per signal): 2020 −0.54, 2021 +0.26, 2022 +0.25, 2023 +0.18, 2024 −0.09, 2025 −0.02, 2026 +0.02.

| $10, 0.5% risk, max 8 open | 2020-2026 | 2024-2026 |
|---|---|---|
| long + short (final) | $94.12 (1,616 trades, DD −28%) | $19.37 (765 trades, DD −19%) |
| long only (with the funding cap) | $73.29 (898 trades, DD −31%) | $19.79 (427 trades, DD −18%) |
| short only | $12.90 (718 trades, DD −14%) | $9.83 (338 trades, DD −13%) |

- **The two sides almost never compete for a slot.** 897 of the 898 longs are the same trades with or without shorts, because the BTC filter puts them in different periods.
- **Shorts made their money in 2021-23** (the May 2021 crash and the 2022 bear market). Since 2024 they are about break-even, and long-only did slightly better.
- **Verdict:** the short edge is not proven (t 1.2). Shorts act as bear-market insurance, not as a second profit engine.
- **Possible next data:** Binance's public archive has 5-minute open interest, long/short ratios and taker buy/sell volume (`futures/um/daily/metrics`) from about 2020-09. These are crowding measures like funding, the only short filter that has worked so far.

**Caveats.**
- SHORT_FUND has two independent confirmations.
- LONG_FUND_CAP is its mirror image and was tested once.
- Both act only in crowded markets.
- Profits still depend on a small number of large trends.
