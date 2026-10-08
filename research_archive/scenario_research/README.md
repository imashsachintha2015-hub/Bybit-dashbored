# Scenario setup research (run of research_archive/PROMPT_scenario_setup_research.md)
Data: OKX 1H, 32 USDT perps, 2024-04-09 .. 2026-09-30; 1D bias from closed days; BTC 1D trend as context. Longs + mirrored shorts.
Split: 19 design coins / 13 unseen coins; dev < 2025-07-11 <= val < 2026-02-19 <= final. Costs 14 bps market / 8 bps limit + funding 0.5 bp per 8h.
Limit fills need 0.05 ATR trade-through; fill bar can only stop out; stop-first on ambiguous bars.
Scenarios / setups (19): fib pullbacks 0.382/0.5/0.635 (golden pocket)/0.705 (OTE)/0.786; impulse fixed-range POC, VAL; anchored VWAP from the swing low;
range fade, range breakout with volume, accumulation spring, range sweep-reclaim; reversal CHoCH (market and retest), RSI divergence;
equal-lows stop hunt; body fakeout; AMD-FVG (frozen PRIMARY, NO_GATE). Variants: 1D bias any/with/against x BTC any/aligned x target 2R/structural/extension = 252 configs.
Results: 0 configs with DEV t>=3 (luck expects 0.3). Frozen DEV-best per setup, one look at FINAL + UNSEEN: 0 of 19 pass the prompt's bar.
Closest: AMD_NO_GATE (limit at FVG 50%, 2R): FINAL +0.129R (n=227), UNSEEN +0.138R (n=657, t=1.9), random control -0.097R, flipped -0.102R,
+0.087R even at 20 bps; $10 -> $46.83 (628 trades, max DD -36%); fails only "positive in 3 of 4 unseen quarters" (2 of 4).
Liquidity sweeps / stop hunts / range-low fades / RSI divergence lose (unseen -0.11..-0.36R); flipping them (trading continuation) also loses.
Golden pocket / OTE / fib pullbacks with the 1D trend: about 0R after costs. Note: a flipped-direction control taken at the signal bar is
look-ahead-biased for limit setups (only trades that later filled are included); controls are taken at the fill bar.
Not covered: 15m trigger (see mtf_confluence, failed), footprint/order flow (tick data), "PCI" (undefined).
