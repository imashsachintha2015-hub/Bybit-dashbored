# Downtrends: anatomy, short-side inventions, and what to do when the market falls

**Data.** OKX 1H prices (2019-12 to 2026-09), aggregated to 4H, all coins.

**Funding is now real.** Funding rates come from Binance's public archive (`fetch_funding.py` and `funding.py`; data.binance.vision, 36 coins). Longs pay positive funding and shorts receive it. Earlier studies charged a flat 0.5 bp per 8h to both sides.

## 1. Real funding vs the flat model (`kal_funding_out.txt`)
| Kalman trend | Flat model | Real funding |
|---|---|---|
| Baseline, all trades | +0.129R | +0.130R |
| Baseline, longs | +0.253R | +0.238R |
| Baseline, shorts | +0.007R | +0.022R |
| BTC-filtered, all trades | +0.234R | +0.219R |
| BTC-filtered, longs | +0.435R | +0.401R (bull-market longs pay about 0.05R per trade) |
| BTC-filtered, shorts | +0.003R | +0.009R |

$10 grows to $76.53 at 0.5% risk with max 8 open (2020-2026, 1,797 trades, max drawdown −33%). Under the flat model it was $87.56.

## 2. Anatomy of downtrends (`dt_anatomy_out.txt`; DEV only: 19 design coins, 2021-06 to 2023-12, which includes the 2022 bear market)
- **Down-legs are not faster than up-legs in ATR terms.** Moves of at least 6 ATR take a median of 58 bars either way. Speed is 0.141 ATR per bar down vs 0.175 up, and the deepest bounce inside a down-leg is 3.5 ATR (the deepest dip inside an up-leg is 4.1).
- **Shorts have less room after detection.** After a top is detected (Kalman flip), on average 6.2 ATR of the down-move remains. After a bottom is detected, 10.7 ATR of the up-move remains, because up-moves have fatter tails.
- **Downtrend lines (falling resistance):**
  - A rally into the line that ends in a rejection candle is followed by a further fall of +0.59 ATR over 24 bars.
  - Breakouts above these lines tend to fail (−0.12 ATR follow-through).
- **Altcoins fall about as much as BTC, not more:** median −14.0% for alts vs −14.3% for BTC during BTC down-legs. During BTC up-legs alts rise only 0.62 times as much as BTC.
- **Tops and bottoms are climax bars:** about 3 times normal volume, with a long wick on the reversal side (45-49% of the bar's range). These are hindsight labels.

## 3. Short-side inventions (`PREREG_downtrend.md` written before the test; `dt_run_out.txt`)
Benchmark: KAL_S, the short half of the Kalman + BTC system. Costs include 14 bps per trade and real funding.

| invention | DEV 2021-23 (BTC down) | VAL 2024 | FINAL 2025-26 | UNSEEN | OLD 2020-21 | pooled holdouts |
|---|---|---|---|---|---|---|
| KAL_S (bench) | +0.132 | −0.183 | −0.083 | +0.041 | −0.248 | −0.033 (t −0.5) |
| NEWTON_S | +0.143 | −0.056 | −0.013 | +0.081 | −0.213 | +0.022 (beats bench 4/4) |
| FUNDING (crowded longs) | +0.250 | −0.091 | −0.052 | +0.132 | −0.225 | +0.027 (beats bench 4/4) |
| REJECT_LINE | (no filter) | +0.297 | −0.118 | +0.134 | +0.052 | +0.068 (t 0.8) |
| WEAK_ALT | +0.281 | −0.110 | −0.138 | +0.051 | −0.572 | −0.048 |
| PATH_S | +0.176 | −0.212 | −0.076 | +0.058 | −0.309 | −0.025 |
| BULL_TRAP | +0.325 | −0.665 | +0.178 | −0.285 | +0.134 | −0.151 |
| LOWER_HIGH | (no filter) | −0.166 | −0.045 | −0.067 | −0.258 | −0.101 (t −2.1) |
| BLOWOFF (short climax tops) | (no filter) | −0.216 | −0.359 | −0.250 | −0.245 | −0.276 (t −3.2) |

**No short setup passed** (pooled holdouts t ≥ 2). Shorts made money in the 2022 bear market (DEV) and lost almost everywhere else.

Two families lost in every holdout:
- shorting climax tops;
- shorting lower highs.

## 4. Short or stand aside? (`dt_longonly_out.txt`, exploratory)
Kalman + BTC filter with real funding. $10 at 0.5% risk, max 8 open:

| period | long + short | long only (flat while BTC trends down) |
|---|---|---|
| 2020-2026 | $76.53 (1,797 trades, DD −33%) | $67.65 (990 trades, DD −32%) |
| 2022 bear year | $10.84 (DD −20%) | $9.28 (21 trades, DD −7%) |
| 2024-2026 | $16.90 (DD −24%) | $18.71 (DD −18%) |
| 2025-2026 | $11.46 (DD −24%) | $12.31 (DD −16%) |

## Conclusion
In crypto, the reliable answer to a downtrend is to protect capital: stand aside while BTC trends down. Shorting adds money only in true bear markets like 2022 or May 2021, and costs money in corrections and chop.
