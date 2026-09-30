# Strategy library test: 42 widely-taught strategies, textbook parameters fixed in advance (no tuning)
Families: trend (EMA/MACD/Supertrend/PSAR/ADX/Ichimoku/Heikin-Ashi/EMA pullback), breakout (Donchian 20/55, Bollinger squeeze, Keltner, inside bar, NR7,
Asia-range at London, NY opening range), mean reversion (RSI2 Connors, RSI 30/70, Bollinger re-entry, stochastic, VWAP 2-sigma, z-score, Williams %R, CCI, MFI),
price action (pin bar, engulfing, three soldiers/crows, morning/evening star, turtle soup, fractal breakout, double bottom/top), volume (spike+strong close, OBV, VWAP reclaim),
divergence (RSI, MACD histogram), session (London continuation, NY reversal). SMC (OB, FVG, sweep+CHoCH, AMD) tested separately in earlier folders.
Exits: E1 stop 1.5 ATR / TP 2R; E2 stop 1 ATR / TP 3R; 48-bar timeout; min stop 0.3%; next-bar-open entry; one position per coin; long + short.
24 coins x 365d, 15m and 1H (1H resampled from 15m). 168 tests.
14 bps: 0/168 net positive (best 1H stochastic-in-trend -0.025R); 102/168 positive before fees; 15m uniformly worse than 1H.
4 bps: 16/168 positive, 5 positive in both halves, none significant (best t=+0.8). 8 bps: 4/168 positive, 0 in both halves.
$10 portfolio results with -60..-99% drawdowns are compounding path noise, not edge (see librep.txt).
