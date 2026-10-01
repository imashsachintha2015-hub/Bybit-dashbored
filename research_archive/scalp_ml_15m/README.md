# 15m scalps via walk-forward ML (custom indicators), ~10 trades/day target
Data (OKX): 15m x 24 coins x 365d; set A (12 search coins) / set B (12 unseen coins, model trained on A only).
Features (34, causal): ATR-normalised returns 1..96, body/range/close-location/wicks, relative volume, CVD proxy (4/16), efficiency ratio, vol-of-vol, distance to 96-bar VWAP / high / low, liquidity-sweep depth, z-score, BTC returns & ATR, coin-vs-universe relative strength, hour/dow.
Labels: net R of a fixed-geometry scalp (stop = max(k*ATR, min%), TP = m*R, 16-bar timeout, next-open entry, fee round-trip), long and short.
Model: HistGradientBoostingRegressor (depth 3, strong regularisation), expanding-window retrain every 30 days, 4h purge; threshold calibrated on the first test block by frequency only; daily cap 10; portfolio: max 3 open, 1 per coin, 2% risk, $10 start.
Results (test span 210 days): rank-IC +0.07..+0.15 out-of-sample (real but small; top-vs-bottom decile spread ~0.1R), but every decile is net negative at 12 bps.
12 bps: $10 -> $1.4-$5.0 (set A/B, 300-530 trades); 8 bps: $5.02 / $3.37; 4 bps: $11.13 (560 trades) / $7.53 (639 trades). Random selection control: $1-$3.
Achieved ~2-3 taken trades/day, not 10 (raw signals ~5/day after threshold, concurrency/one-per-coin limits).

## Advanced-math feature pack (mathfeat.py, MATH=1) -- 73 features total
Calculus (Savitzky-Golay velocity/acceleration/jerk, VWAP-deviation integrals, volume impulse), physics (kinetic energy, Kyle lambda, impact residual, Amihud),
stochastic (Ornstein-Uhlenbeck z/kappa/half-life, variance ratio, Hawkes self-excitation), information (permutation/spectral entropy), spectral cycle phase,
Kalman local-linear-trend slope/innovation, Hill tail index, Katz fractal dimension, volume-profile potential-well gradient/curvature/POC distance.
Per-feature rank-IC (icstudy.py, 12 monthly blocks, search + unseen coins): magnitude of the next 4h move is strongly predictable (atr, Kyle lambda, vrel, KE, fractal dim: |t| 5-12, 100% same-sign blocks);
direction only weakly (impulse32 fade t~-3.5, Hawkes direction fade, well gradient t~+2.5..3, cycle phase).
Walk-forward ($10, 2% risk, 210 days), search / unseen coins: 12 bps $8.74 (120 trades) / $9.53 (175); 8 bps $9.32 / $8.70; 4 bps $9.00 / $11.53 (264 trades).
Wide stop (3 ATR, 1%, 8h) at 12 bps: $6.46 / $7.57. Random control $3-7. Loss roughly halved vs the base pack; still just below breakeven at realistic fees; ~0.5-1.3 trades/day.
