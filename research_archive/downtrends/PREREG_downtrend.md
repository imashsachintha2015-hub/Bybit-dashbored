# Short-side (downtrend) inventions: pre-registration, written 2026-10-08 after the DEV anatomy (dt_anatomy_out.txt) and before any test
Frame: OKX 1H d6 + d5 + d4 -> 4H, all coins, SHORT trades only (simulated as longs on mirrored prices). Market entry at the next open,
14 bps, REAL funding from Binance's archive (shorts receive positive funding, pay negative), one open trade per coin.
"BTC down" = BTC's 1D trend is down (close < EMA50 and EMA20 < EMA50 on daily closes). Words below are in real price terms.

| name | idea (from the anatomy) | entry (short) | stop | exit |
|---|---|---|---|---|
| KAL_S | benchmark: Kalman trend, short half of the improved system | Kalman z crosses below -1 and BTC down | 3 ATR above | z > 0 (120 bars max) |
| REJECT_LINE | rally into a falling trend line that is rejected (DEV: +0.59 ATR) | test of a downtrend line closing as a bearish rejection (close < open, close below the line) | above max(high, line) + 0.3 ATR | 3R target, 60 bars |
| LOWER_HIGH | bear rally fails: new lower high confirmed while the trend is down | 3-ATR swing high confirmed below the previous swing high while Kalman z < -1 | above the lower high + 0.2 ATR | z > 0 (120 bars) |
| BLOWOFF | climax top: huge volume, long upper wick after a run-up | volume >= 2.5x, upper wick >= 45% of the bar, close in the lower half, close >= 4 ATR above the close 20 bars earlier | above the bar's high + 0.2 ATR | 3R target, 60 bars |
| BULL_TRAP | breakout above a falling trend line that fails | close above a downtrend line, then a close back below it within 3 bars | above the highest high since the breakout + 0.2 ATR | 3R target, 60 bars |
| NEWTON_S | acceleration Kalman, short side | velocity z crosses below -1 while accelerating down | 3 ATR above | velocity z > 0 (120 bars) |
| PATH_S | least resistance downward | KAL_S entry and less volume (last 180 bars) in [price - 3 ATR, price) than in (price, price + 3 ATR] | 3 ATR above | z > 0 |
| WEAK_ALT | short the weakest coins | KAL_S entry and the coin underperformed BTC over the last 180 bars (30 days) | 3 ATR above | z > 0 |
| FUNDING | crowded longs pay us | KAL_S entry and the average funding rate of the last 9 payments (3 days) > 0 | 3 ATR above | z > 0 |

Every invention runs with and without "BTC down"; DEV (19 design coins, 2021-06 .. 2023-12) chooses on/off by t. KAL_S keeps the filter.
Holdouts: VAL = design coins 2024, FINAL = design coins 2025-01 .. 2026-09, UNSEEN = other coins 2021-06 .. 2026-09, OLD = all coins 2020-01 .. 2021-05.
"Beats the benchmark" = higher average net R than KAL_S in 4 of 4 holdouts (3 of 4 = partly). Standalone pass = pooled holdouts avg > 0, t >= 2.
$10 short-only portfolios: 0.5% risk, max 8 open, one per coin, for OLD (2020-21) and 2024-26. Known caveat: the holdout years' market
regimes (bull 2020-21 and 2024, mixed 2025-26) were already seen in earlier work; no short invention was tested on them before.
