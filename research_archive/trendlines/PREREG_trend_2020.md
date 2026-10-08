# Pre-registered tests on 2020-01-01 .. 2021-05-31 (OKX 1H -> 4H), written 2026-10-08 BEFORE that data was downloaded
No bar of this period was used to design, select or tune anything. Rules are frozen exactly as in tl_setups.py (DEV-best per setup),
same engine (tl_core.py, zigzag 3 ATR, 4H), both price orientations, market entry next open, 14 bps + 0.25 bp funding per 4H bar,
one open trade per coin and side. Only signals dated 2020-01-01 .. 2021-05-31 count (features may warm up on earlier bars).
Pass = avg net R > 0 and t >= 2.0 (strict, Bonferroni for 5 tests: t >= 2.33). t clustered by day.
1. KALMAN1  : Kalman trend z crosses above 1 -> long (and mirrored short); 3-ATR protective stop; exit at the open after the first close with z < 0; 120 bars max
2. STRONG   : close above a resistance line with 3+ touches or 2+ tests; stop 2 ATR; target 2R; 60 bars max
3. OPTBRK   : close above the optimized no-violation resistance line (48 bars) with the 1D trend up; stop 2 ATR; target 3R; 60 bars max
4. CHOCH    : close above the last confirmed swing high; stop below the last confirmed swing low - 0.2 ATR; target 3R; 60 bars max
5. ENSEMBLE : all trades of 1-4 in one $10 portfolio (1% risk, max 3 open, one position per coin), avg net R per trade and $10 result
Known caveats, stated now: 2020-21 was a strong bull market; only BTC ETH LTC XRP BCH ETC TRX trade the whole period (others start
when listed); short history before 2020-03 means warm-up eats the first weeks.
