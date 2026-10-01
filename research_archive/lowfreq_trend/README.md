# Low-frequency tests (daily / 4H, 33 OKX coins, 14 bps round trip + 0.5bp/8h funding drag)
Protocol: parameters chosen on DEV (first 50%) and VAL (next 25%); the last 25% (FINAL) was touched once, for pre-declared ensembles only.
Caveat: universe = coins listed today (survivorship bias flatters longs, hurts shorts).
- e1/e2: daily trend (EMA / Donchian). Long-only ensembles: Sharpe 1.2 / 0.7 / 0.08 (dev/val/final), max drawdown -11..-17% vs buy&hold -53..-100%, beta ~0.15, CIs include 0. Long/short versions lose (shorts squeezed).
- e3: cross-sectional momentum / reversal, dollar-neutral: reversal negative everywhere; momentum inconsistent (L=14: dev +0.06, val +1.77); daily rebalance dies to turnover costs.
- e4: 4H trend ensembles just track the market regime (val -2.5, final +1.8), no alpha of their own.
Conclusion: trend exits work as risk control, not as a profit source.
