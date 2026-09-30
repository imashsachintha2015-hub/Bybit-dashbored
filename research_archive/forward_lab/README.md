# Forward lab (record-only, running on Railway since deployment)
No orders are placed. `daemons/forward_lab.py` runs next to the engine; scoreboard: `GET /api/forward-lab`.

## 1. AMD-FVG 1H forward test -- rules declared BEFORE any forward data
- Rule: `backend_lib/amd_fvg.py`, frozen. PRIMARY = the configuration selected in research_archive/discovery_loop
  (counter-drift gate); NO_GATE = same rule without the gate (secondary, logged for comparison only).
- Universe: 33 Bybit USDT perpetuals. Data: Bybit mainnet public 1H candles, closed bars only.
- Counting: only setups whose signal bar opens after the lab's start time. Limit fill requires price to trade
  0.05 ATR through the entry; fill bar can only stop out; 8 bps round-trip cost; 2R target; 96h timeout.
- Pass/fail (PRIMARY): after >= 60 closed trades, average net R >= +0.10 -> PASS (then demo orders at 1% risk
  may be considered); otherwise FAIL -> drop it. Backtest reference on unseen data: +0.19R/trade.
- Parity: `parity_check.py` confirms the live module reproduces the backtest trade-for-trade (1,107/1,107).
- Expected pace from the backtest: roughly 1 PRIMARY setup per day -> ~2 months to 60 trades.

## 2. Order-flow recorder
Per minute for the engine's 15 symbols: taker buy/sell quote volume and trade counts (publicTrade websocket),
liquidations (allLiquidation; liq_buy = long positions liquidated), book depth within 0.5% / 1% of mid,
best bid/ask, funding rate, open interest, mark price (REST snapshots). Purpose: re-run the 15m/scalp models with
information candles never contained, once several weeks are recorded.

## 3. Engine
`daemons/cme_x5_pure_engine.py` now defaults to RECORD-ONLY (`CME_X5_RECORD_ONLY=1`): Model B / Championship
signals are paper-tracked locally; set the variable to 0 to re-enable demo execution. Reason: both lost after
costs in the historical replay (research_archive/historical_replay).
Persistence: databases live in DATA_DIR (Railway volume mounted at /data).
