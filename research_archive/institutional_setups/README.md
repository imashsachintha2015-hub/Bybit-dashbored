# Trend + institutional-concept entry setups (POC, FVG CE, order block, EMA20 pullback, wick)
Trend filter: EMA50>EMA200, close>EMA50, EMA50 rising (shorts mirrored via price inversion).
Data (OKX): 15m x 19 coins x 120d; 1H x 12 coins x 365d. Entry = close of confirmation bar, cost 14 bps.
Two measures: geometry-free forward return at 16 bars vs. the trend-only baseline, and a structure-stop / 2R-target sim.
Split: first 60% (train) / last 40% (test), day-clustered bootstrap CI. Results: study_m15.txt, study_h1.txt.
Finding: no setup beats the trend-only baseline; every net sim result is negative.
Not tested: limit entries at the zone with maker fees, session/liquidity context, order-book data.
