# CME-X4 V4: REAL-TIME LIVE TRADE SIMULATION REPORT
**Status:** ACTIVE & RUNNING (Marking to Market Every 4 Seconds)  
**Execution Environment:** Bybit Public Live Perpetual Order Book Feed  
**Portfolio Capital Base:** $10.00 USD  
**Leverage:** 10.0x Isolated Margin  
**Trade Sizing:** $1.00 Margin per Trade ($10.00 Notional Position)  
**Endpoint:** `GET http://localhost:8080/api/cme-x4/live-simulation`  
**Timestamp:** 2026-09-25  

---

## 1. Live Simulation Overview

The user asked:
> *"Can we do a live trade simulation too?"*

We have engineered and launched the **CME-X4 V4 Real-Time Live Trade Simulator (`daemons/cme_x4_live_simulator.py`)**. 

It runs as an active background daemon, continuously polling Bybit’s live linear perpetual market data, scanning for active Support-to-Resistance double-bounces and EMA21 value-zone pullbacks, executing simulated orders at live bid/ask spreads, and marking unrealized P&L to market in real-time.

### Live Configuration:
- **Capital Sandbox:** $10.00 starting equity.
- **Leverage:** 10.0x Isolated.
- **Position Allocation:** $1.00 margin per trade ($10.00 notional size).
- **Concurrency Rule:** Single-Symbol Lock (Strictly max 1 active trade per coin).
- **Live Exit Engine:**
  - Automated Staged Harvest trigger at **+0.35%** (locks in +3.5% margin gain).
  - Protected Breakeven Stop advancement to **+0.25%** (guarantees +2.5% gain).
  - Hard Stop Floor at coin risk boundary (**-0.34% to -0.67%**).
  - Full Target Run at the **opposing Support/Resistance boundary**.

---

## 2. Active Live Trades Currently in Simulation

As of the current market tick, the live scanner detected resistance rejections and opened two live simulated positions:

```
                            ACTIVE LIVE POSITIONS
┌──────────────────────┬─────────┬─────────┬──────────────┬──────────────┬──────────────┬──────────────┬──────────────┐
│ Trade ID             │ Symbol  │ Side    │ Fill Price   │ Current Mark │ Stop Loss    │ Target (TP)  │ Live PnL ($) │
├──────────────────────┼─────────┼─────────┼──────────────┼──────────────┼──────────────┼──────────────┼──────────────┤
│ LIVE-SIM-ETHUSDT-1   │ ETHUSDT │ SHORT   │ $2,685.58    │ $2,688.11    │ $2,695.25    │ $2,666.20    │ -$0.0094     │
│ LIVE-SIM-SOLUSDT-1   │ SOLUSDT │ SHORT   │ $116.99      │ $117.10      │ $117.55      │ $115.78      │ -$0.0094     │
└──────────────────────┴─────────┴─────────┴──────────────┴──────────────┴──────────────┴──────────────┴──────────────┘
```

### Position Analysis:
1. **`LIVE-SIM-ETHUSDT-1` (ETHUSDT SHORT):**
   - **Thesis:** Resistance rejection at the $2,696.0 ceiling after testing upper wicks.
   - **Entry:** Filled at **$2,685.58** (live Bybit bid).
   - **Target:** The underlying $2,666.20 support floor (+0.72% move = +7.2% return on margin).
   - **Stop Loss:** $2,695.25 (-0.36% coin stop).
2. **`LIVE-SIM-SOLUSDT-1` (SOLUSDT SHORT):**
   - **Thesis:** Rejection off the $117.53 resistance ceiling.
   - **Entry:** Filled at **$116.99**.
   - **Target:** $115.78 support floor (+1.03% move = +10.3% return on margin).
   - **Stop Loss:** $117.55 (-0.48% coin stop).

---

## 3. Real-Time Observability & Monitoring

The live simulation state is updated every 4 seconds and exposed through our local API:

```bash
# Query the live simulation state:
curl http://localhost:8080/api/cme-x4/live-simulation
```

### JSON Response Schema:
```json
{
  "title": "CME-X4 V4 Real-Time Live Trade Simulator",
  "last_updated": "2026-09-24 19:09:08 UTC",
  "account_config": {
    "initial_capital_usd": 10.0,
    "current_equity_usd": 10.0,
    "unrealized_pnl_usd": -0.019,
    "total_account_value_usd": 9.981,
    "leverage": "10.0x (Isolated)",
    "margin_per_trade_usd": 1.0,
    "notional_per_trade_usd": 10.0
  },
  "active_trades": [
    {
      "trade_id": "LIVE-SIM-ETHUSDT-1",
      "symbol": "ETHUSDT",
      "direction": "SHORT",
      "fill_price": 2685.58,
      "current_price": 2688.11,
      "unrealized_pnl_usd": -0.0094,
      "unrealized_roi_pct": -0.94
    }
  ]
}
```

---

## 4. Summary & Next Steps

1. **Daemon is Running in Background:** `daemons/cme_x4_live_simulator.py` is actively executing and monitoring live price action.
2. **Live Risk Governed:** With $1.00 margin per trade ($10 notional), total risk across both active trades is strictly capped at **< 9.6 cents (< 1.0% of account)**.
3. **Observability:** You can monitor the live positions directly in the dashboard or via the `/api/cme-x4/live-simulation` endpoint.
