# Situation tests on the funding-aware Kalman trend: pre-registration
Written 2026-10-09, before any of these tests ran.

**Data gathered first:** FOMC decision dates (`fomc_dates.json`, 57 decisions from 2020 to 2026, from federalreserve.gov). CPI release dates could not be downloaded (bls.gov blocked), so CPI is not tested.

## Common frame
- **System:** the final funding-aware Kalman trend (`longshort/README.md`): 4H bars, 36 OKX coins, 2020-01 to 2026-09, 14 bps, real Binance funding.
- **Signals:** 3,080 signals, one per coin before the 8-position limit (1,883 long, 1,197 short).
- **Re-simulation:** where a test changes exits or fills, trades are re-simulated from prices. The standard exit must reproduce the stored result of every signal (parity).
- **Segments:**

| segment | coins | period | role |
|---|---|---|---|
| DEV | 19 design coins | 2021-06 to 2023-12 | reported only |
| VAL | design coins | 2024 | holdout |
| FINAL | design coins | 2025-01 to 2026-09 | holdout |
| UNSEEN | the other 17 coins | 2021-06 to 2026-09 | holdout |
| OLD | all coins | 2020-01 to 2021-05 | holdout |

  A holdout with fewer than 20 signals with data on the tested set does not count as a win.
- **Unit:** net R per signal, with day-clustered standard errors.
- **Two pass levels:** PASS needs t ≥ 2.0 plus consistency; PASS strict needs t ≥ 2.9 plus consistency.
  - 25 t-based tests run in this round. By luck about 0.6 PASS and 0.05 PASS strict are expected.
- **$10 portfolio for every test:** 0.5% risk, max 8 open, one per coin.
  - Compounded 2020-2026 and 2024-2026, with trade counts and max drawdown.
  - Also per year, restarting from $10 each year.

## Filter rule (tests 1, 5, 7, 8, 9)
- **Separation:** kept vs removed, pooled over the holdouts. t = (mean kept − mean removed) / √(se kept² + se removed²) ≥ 2.0 (strict 2.9).
- **Consistency:** kept average > benchmark average (all signals with data) in at least 3 of the 4 holdouts.
- **Also reported:** each side (long, short) separately, and for test 1 the half-risk version in the portfolio (chop signals taken at 0.25% risk instead of skipped).

## Test 1: sideways and choppy markets (skip the signal when the condition holds)
All measures use only data up to the signal bar's close, on real (not mirrored) prices.

| name | chop condition |
|---|---|
| COIN_CHOP | the coin's efficiency ratio over 180 bars (abs(net log move) / sum of abs(bar log moves)) < 1/√180 = 0.0745, i.e. choppier than a random walk |
| BTC_CHOP | the same for BTC |
| MEAN_REVERT | variance ratio of 6-bar vs 1-bar log returns over 120 bars (`inv_core.variance_ratio`) < 1 |
| DISORDER | permutation entropy of order 3 over 180 bars (Miller-Madow corrected, normalized) > 0.967, the random-walk value |
| SQUEEZE | ATR14 / mean true range of the last 180 bars < 0.75 |
| COIN_WHIPSAW | the coin's last 2 system signals that had closed (by the signal bar's close) both lost |
| SYSTEM_COLD | the last 10 trades closed by the unfiltered $10 portfolio had 2 or fewer winners |

## Test 2: managing a trade in profit (paired: the same signals with a different exit)
- **Order within each bar:** stop first, then targets (limit fills at the target price), then the z exit at the close.
- **Stop changes** take effect from the next bar.
- **Costs:** fees per portion as before; funding per portion up to that portion's exit.

| name | rule (everything else standard) |
|---|---|
| PART_2R | take 1/3 off at +2R |
| PART_3R | take 1/2 off at +3R |
| BE_15 | stop to the entry price once +1.5R has been reached |
| TRAIL | once +2R has been reached, the stop trails at the highest close since entry − 3 ATR (only upward) |
| TP3 | whole position off at +3R ("TP Entire") |
| TP5 | whole position off at +5R |
| LADDER | 1/3 off at +2R, 1/3 at +4R, the last third standard |
| LADDER_BE | LADDER plus stop to entry after the first partial |

- **BETTER** needs both:
  - the paired difference (variant − standard), pooled over the holdouts, has t ≥ 2.0 (strict 2.9);
  - the difference is > 0 in at least 3 of the 4 holdouts.
- **SMOOTHER** needs all three:
  - paired t > −1.0;
  - the $10 portfolio's max drawdown is smaller, in both 2020-2026 and 2024-2026;
  - MAR (return / |max drawdown|) is higher, in both 2020-2026 and 2024-2026.

## Test 3: crashes and fast markets (stress profile, standard exits)
| name | change |
|---|---|
| GAP | a stop that the bar opens through fills at the open |
| SLIP x | GAP plus x = 0.05 / 0.1 / 0.25 / 0.5 / 1.0% worse on every market fill (entry, stop, z exit, time exit); R stays in units of the planned risk (open − stop) |
| FASTBAR | GAP plus an extra 0.5 ATR of slippage on stops hit in bars wider than 2 ATR |
| FEES×2 | 28 bps per round trip |
| FUNDING×2 | funding costs doubled (receipts unchanged) |
| COMBINED | GAP + 0.25% slippage + FASTBAR + FEES×2 |

- **Break-even slippage:** the x at which the average net R falls to 0, and the x at which $10 2020-2026 ends at $10.
- **Crash table:** the 10 largest non-overlapping 24-hour BTC falls, with positions open, stops hit and R lost in each.
- **Verdict:**
  - "robust" if with GAP + 0.25% slippage the pooled average stays ≥ +0.10R and $10 2020-2026 stays above $40;
  - "fragile" if the break-even slippage is below 0.25% per fill.

## Test 4: liquidation flush (new standalone setups; open interest from `openinterest/`)
**Common rules:**
- Only the first bar of an event counts (the condition was false on the bar before).
- One trade per coin at a time.
- 14 bps plus real funding; stop first within a bar.
- Minimum stop distance 0.5%.

| name | entry at the next open after the signal bar | stop | exit |
|---|---|---|---|
| LONG_FLUSH | price fell ≥ 3 ATR over the last 6 bars and open interest fell ≥ 10% over 24 hours | 0.5 ATR below the 6-bar low | target +2R, else after 18 bars |
| SHORT_SQUEEZE_END | price rose ≥ 3 ATR over the last 6 bars and open interest fell ≥ 10% over 24 hours (short) | 0.5 ATR above the 6-bar high | same |
| LONG_FLUSH_BTCUP | LONG_FLUSH only while BTC's 1D trend is up | same | same |
| SHORT_SQUEEZE_BTCDOWN | SHORT_SQUEEZE_END only while BTC's 1D trend is down | same | same |

- **Open interest** uses the cut-off of `openinterest/` (hourly rows ending by the signal close − 1h).
- **Standalone pass** needs all of:
  - pooled holdouts (VAL, FINAL, UNSEEN) average net R > 0 with t ≥ 2.0 (strict 2.9);
  - positive in all 3 holdouts;
  - at least 30 events in the holdouts, otherwise "too rare".
- **A passing setup** is also tested as an add-on inside the system's $10 portfolio.

## Test 5: alt season vs BTC season (filters on alt coins; BTC's own signals are not part of the test)
| name | keep the signal only if |
|---|---|
| RS_LEADER | long: the coin's 30-day (180-bar) log return beat BTC's |
| ALT_SEASON | long: more than half of the coins with data (at least 10) beat BTC over 30 days |
| RS_LAGGARD | short: the coin's 30-day return lagged BTC's (a repeat of WEAK_ALT, which failed on the plain short benchmark) |

## Test 6: too many trades in one direction (portfolio rules)
| name | rule |
|---|---|
| DIR_CAP4 | at most 4 open trades in the same direction |
| DIR_CAP6 | at most 6 |
| RISK_TAPER | risk 0.5% with fewer than 4 open, 0.35% with 4-5 open, 0.25% with 6-7 open |
| CORR_CAP | skip if 3 or more open same-direction trades are in coins whose 30-day correlation of 4H log returns with this coin is > 0.7 (at least 150 shared returns) |

- **Pass** needs all three:
  - higher MAR than the current rule over 2020-2026;
  - higher MAR over 2024-2026;
  - higher MAR in at least 4 of 7 single years.
- **Drawdown** is measured on realized equity, as in all earlier tests.

## Tests 7-9: lower priority (filters; skip when the condition holds)
| name | condition |
|---|---|
| WEEKEND | the signal bar closes on a Saturday or Sunday (UTC) |
| NEW_LISTING | within 180 days of the coin's first bar, for coins whose data starts after 2020-02-01 |
| FOMC | entry on an FOMC decision day or the day before (UTC); for the two unscheduled 2020 meetings also the day after |

## Adoption
- Only PASS strict results are adoption candidates: record-only in the forward lab first, if the user approves.
- PASS results are reported as promising.
- Nothing is combined without a new test.

**Tests:** chop 7 + management 8 + flush 4 + relative strength 3 + heat 4 + lower priority 3 = 29, plus the stress profile.
