# Resonance test: rebuild of the "Quantum AI – Resonance Engine" TradingView indicator idea
Source: a promo video (TV Indicators Studio / ChainPath) claiming "multi-cycle resonance confirmation effectively avoids false breakouts and
institutional trap areas". The indicator is closed-source, so this tests a rebuild of what its screen shows. Rules were frozen before any result.

Rules (res.py): trend per timeframe 15m / 30m / 1H / 4H / 1D from closed bars only (+1 if close > EMA50 and EMA20 > EMA50, -1 opposite, 0 else;
50 bars warm-up). Resonance = number of timeframes agreeing with the trade (0-5). Strength = 15m ADX(14) (strategy_library/lib.py formula).
Trigger: 15m CHoCH (close crosses the last confirmed k=2 pivot high) or 15m EMA20 cross. Entry next 15m open (market). Stop below the last
confirmed swing / 8-bar low - 0.1 ATR, min 0.3%. Exits: ladder TP1 1R / TP2 1.5R / TP3 2.25R (a third each, breakeven after TP1, as in the
video), or all at 1.5R, or all at 2.25R; 96 bars max. Grid: resonance >=3 / >=4 / =5 x ADX none / >=20 / >=25 x 2 triggers x 3 exits = 54.
Shorts = mirrored prices. One open trade per coin and side.
Data: OKX 15m, 24 coins, 2025-09-30 .. 2026-09-30. Design coins BTC ETH SOL XRP DOGE BNB ADA AVAX LINK DOT LTC NEAR, the other 12 unseen;
dev < 2026-03-31 <= val < 2026-07-01 <= final. Costs 14 bps market + 0.5 bp funding per 8h.
Checks (res_check_out.txt): no-repaint test (features on data cut at 300 random bars == full run): 0 mismatches; 1D trend parity with
scenario_research/scen.daily_trend: 0 of 364 days differ (BTC, ETH, SOL); ladder P&L hand-check on synthetic paths: correct.

Results (res_an_out.txt):
- 0 of 54 configs positive after fees; 53 of 54 positive before fees. Stricter resonance (all 5) and ADX >= 25 raise the gross edge
  (best: EMA cross, all 5 agree, ADX >= 25, 2.25R: +0.155R gross, -0.043R net). Agreement across timeframes carries some information,
  but less than the fees.
- Selection on dev: 0 configs with dev t >= 2 (luck expects 1.2). Frozen variants (mostly all-5 + ADX >= 25) all fail on final + unseen
  (-0.03 .. -0.25R per trade).
- Controls on final + unseen: setup -0.08R vs resonance alone -0.19R, random entries with the same filter -0.14R, flipped -0.14R.
  The trigger adds a little before fees; at 4 bps (maker-only fees) the best frozen variants are about 0 .. +0.06R.
- Setup closest to the video (CHoCH, all 5 timeframes agree, ADX >= 25, TP1/TP2/TP3 ladder + breakeven), all 24 coins, full year:
  2,300 trades (6.3/day), 50% winners, -0.065R per trade; $10 -> $3.36 at 1% risk (1,026 trades taken, max drawdown -68%),
  $10 -> $0.98 at 2% risk (1,018 trades, -91%).
Not run: the planned 1H long-history check (only if a 15m variant passed).
Run: from a folder holding s15/ (15m OKX JSON), `python3 res.py check`, `python3 res.py`, `python3 res_an.py`.
