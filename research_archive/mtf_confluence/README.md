# Multi-timeframe confluence system (1H bias -> 15m sweep into POI -> confirmation candle -> high-R target)
1H: BOS state (pivot k=2), premium/discount of the dealing range, unmitigated 1H FVGs (72h), 48h 1H POC.
15m: liquidity sweep of the prior 16/48-bar low into the POI; confirmation within 6 bars = reclaim / bullish engulfing / CHoCH (close above last 15m pivot high);
entry next open (market, 14 bps), stop below sweep (min 0.4%), targets 2/3/4/5R or nearest 1H liquidity, optional breakeven at 1R, optional London/NY kill-zone filter. Shorts mirrored.
1,440 configs, 24 coins x 365d; search coins dev/val/final + 12 unseen coins.
Result: median dev avgR -0.15..-0.27 by component; 1 config passes dev t>=2.5 (chance ~9), 0 pass val.
CHoCH confirmation looked best on dev (median -0.03R, 40% of configs >0) but collapsed on val (-0.25R), final (-0.18R) and unseen coins (-0.09..-0.18R).
