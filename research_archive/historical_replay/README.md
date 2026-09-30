# Historical replay of CME-X5 Model B and Championship (120 days, 15 symbols)

Data: OKX USDT-swap candles (Bybit REST is geo-blocked from the sandbox), 15m/1H/4H, ~2026-06-02 to 2026-09-30.
Run: `python fetch.py 120 && python replay.py && python analyze.py` (from a dir with `data/`; replay.py expects repo root on sys.path).
Real functions imported: detect_s2, detect_s7, SR/POC/FVG agents, ConfluenceScorer, ChampionshipDualRegimeEngine.
Executor rules replayed: BE ratchet 0.70R/0.8%, trail 1.2R/1.5%, 48-bar timeout, max 3 positions, 14 bps friction.
Pessimistic same-bar tie-break (stop first). Not replayed: order-book spread gate, forming-candle scans, slippage.
See analysis.txt for full output.
