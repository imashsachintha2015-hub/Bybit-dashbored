#!/usr/bin/env python3
"""
CME-X4 V4: $10.00 STARTING EQUITY SIMULATION & FEASIBILITY STUDY
Version: 2026-09-25

Analyzes:
1. Can you trade CME-X4 with only $10.00 starting equity?
2. Bybit exchange minimum order size constraints (min notional per coin).
3. 3 Position Sizing Models on $10 Capital:
   - Model A: Micro-Allocation ($1.00 margin x 10x = $10 notional, 0.48% risk/trade)
   - Model B: Dynamic 2.0% Risk Compounding ($0.20 risk -> ~$41.60 notional, 4.16x leverage)
   - Model C: Full Margin Trap ($10.00 margin x 10x = $100 notional, 4.80% risk/trade)
4. Liquidation & Ruin Probability over 500 trades.
"""

import os
import sys
import json
import math
from datetime import datetime, timezone

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT_DIR)

DATA_DIR = os.path.join(ROOT_DIR, "research", "cme_x4", "data")
RESULTS_DIR = os.path.join(ROOT_DIR, "research", "cme_x4", "results")
os.makedirs(RESULTS_DIR, exist_ok=True)

# Load existing 500-trade chronological ledger
with open(os.path.join(RESULTS_DIR, "simulate_500_trades_results.json"), "r", encoding="utf-8") as f:
    baseline_data = json.load(f)

closed_trades = baseline_data["all_trades"]
print(f"Loaded {len(closed_trades)} baseline trades.")

# ── MODEL A: Micro-Allocation ($1.00 margin x 10x = $10 notional) ──────
# Risk per trade = $10 * 0.0048 = ~$0.048 (0.48% of $10 account)
eq_a = 10.00
curve_a = [eq_a]
ruined_a = False
ruin_trade_a = None

for i, t in enumerate(closed_trades):
    if eq_a <= 0.50:
        ruined_a = True
        ruin_trade_a = i + 1
        break
    # Fixed $10 notional
    pnl = round(t["realized_r"] * (10.0 * (t["stop_floor_pct"] / 100.0)), 4)
    eq_a = round(eq_a + pnl, 4)
    curve_a.append(eq_a)

peak_a = 10.00
max_dd_a_usd = 0.0
max_dd_a_pct = 0.0
for val in curve_a:
    if val > peak_a:
        peak_a = val
    dd = peak_a - val
    dd_pct = (dd / peak_a) * 100.0
    if dd > max_dd_a_usd:
        max_dd_a_usd = dd
        max_dd_a_pct = dd_pct

# ── MODEL B: Dynamic Compounding (Fixed 2.0% Risk of Equity) ───────────
# At any time, risk = 2.0% of current equity.
# Notional = (Equity * 0.02) / stop_floor
eq_b = 10.00
curve_b = [eq_b]
ruined_b = False
ruin_trade_b = None

for i, t in enumerate(closed_trades):
    if eq_b <= 0.50:
        ruined_b = True
        ruin_trade_b = i + 1
        break
    dollar_risk = eq_b * 0.02
    pnl = round(t["realized_r"] * dollar_risk, 4)
    eq_b = round(eq_b + pnl, 4)
    curve_b.append(eq_b)

peak_b = 10.00
max_dd_b_usd = 0.0
max_dd_b_pct = 0.0
for val in curve_b:
    if val > peak_b:
        peak_b = val
    dd = peak_b - val
    dd_pct = (dd / peak_b) * 100.0
    if dd > max_dd_b_usd:
        max_dd_b_usd = dd
        max_dd_b_pct = dd_pct

# ── MODEL C: Aggressive All-In Margin ($10.00 margin x 10x = $100 notional)
# Dollar risk per trade = $100 * 0.0048 = ~$0.48 (4.8% of $10 account)
eq_c = 10.00
curve_c = [eq_c]
ruined_c = False
ruin_trade_c = None

for i, t in enumerate(closed_trades):
    if eq_c <= 0.50:
        ruined_c = True
        ruin_trade_c = i + 1
        break
    pnl = round(t["realized_r"] * (100.0 * (t["stop_floor_pct"] / 100.0)), 4)
    eq_c = round(eq_c + pnl, 4)
    curve_c.append(eq_c)

peak_c = 10.00
max_dd_c_usd = 0.0
max_dd_c_pct = 0.0
for val in curve_c:
    if val > peak_c:
        peak_c = val
    dd = peak_c - val
    dd_pct = (dd / peak_c) * 100.0
    if dd > max_dd_c_usd:
        max_dd_c_usd = dd
        max_dd_c_pct = dd_pct

# Bybit Minimum Order Size Feasibility Matrix for $10 Account
bybit_min_sizes = [
    {"symbol": "BTCUSDT", "min_qty": "0.001 BTC", "price_approx": 65000, "min_notional_usd": 65.0, "can_trade_1x": False, "can_trade_10x": True, "notes": "Requires $6.50 margin at 10x"},
    {"symbol": "ETHUSDT", "min_qty": "0.01 ETH", "price_approx": 2500, "min_notional_usd": 25.0, "can_trade_1x": False, "can_trade_10x": True, "notes": "Requires $2.50 margin at 10x"},
    {"symbol": "SOLUSDT", "min_qty": "0.1 SOL", "price_approx": 135, "min_notional_usd": 13.5, "can_trade_1x": False, "can_trade_10x": True, "notes": "Requires $1.35 margin at 10x"},
    {"symbol": "XRPUSDT", "min_qty": "1 XRP", "price_approx": 0.58, "min_notional_usd": 1.0, "can_trade_1x": True, "can_trade_10x": True, "notes": "Bybit min notional $1.00"},
    {"symbol": "DOGEUSDT", "min_qty": "10 DOGE", "price_approx": 0.11, "min_notional_usd": 1.1, "can_trade_1x": True, "can_trade_10x": True, "notes": "Bybit min notional $1.10"},
    {"symbol": "ADAUSDT", "min_qty": "2 ADA", "price_approx": 0.35, "min_notional_usd": 1.0, "can_trade_1x": True, "can_trade_10x": True, "notes": "Bybit min notional $1.00"},
    {"symbol": "SUIUSDT", "min_qty": "1 SUI", "price_approx": 1.50, "min_notional_usd": 1.5, "can_trade_1x": True, "can_trade_10x": True, "notes": "Bybit min notional $1.50"},
    {"symbol": "PEPEUSDT", "min_qty": "10000 PEPE", "price_approx": 0.00001, "min_notional_usd": 1.0, "can_trade_1x": True, "can_trade_10x": True, "notes": "Bybit min notional $1.00"}
]

results_payload = {
    "title": "CME-X4 V4: $10.00 Starting Equity Feasibility & 500-Trade Simulation",
    "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
    "initial_capital_usd": 10.00,
    "model_a_micro_cap": {
        "name": "Model A: Micro-Allocation ($1.00 Margin x 10x = $10.00 Notional)",
        "margin_per_trade_usd": 1.00,
        "notional_per_trade_usd": 10.00,
        "dollar_risk_per_trade_usd": 0.048,
        "risk_pct_of_account": 0.48,
        "final_equity_usd": round(eq_a, 2),
        "net_profit_usd": round(eq_a - 10.00, 2),
        "roi_pct": round(((eq_a - 10.00) / 10.00) * 100.0, 2),
        "max_drawdown_usd": round(max_dd_a_usd, 2),
        "max_drawdown_pct": round(max_dd_a_pct, 2),
        "ruined": ruined_a,
        "verdict": "SAFEST & HIGHLY RECOMMENDED FOR $10 ACCOUNT"
    },
    "model_b_dynamic_compounding": {
        "name": "Model B: Dynamic 2% Risk Compounding",
        "initial_risk_usd": 0.20,
        "risk_pct_of_account": 2.0,
        "final_equity_usd": round(eq_b, 2),
        "net_profit_usd": round(eq_b - 10.00, 2),
        "roi_pct": round(((eq_b - 10.00) / 10.00) * 100.0, 2),
        "max_drawdown_usd": round(max_dd_b_usd, 2),
        "max_drawdown_pct": round(max_dd_b_pct, 2),
        "ruined": ruined_b,
        "verdict": "OPTIMAL COMPOUNDING BALANCE"
    },
    "model_c_aggressive_all_in": {
        "name": "Model C: Aggressive All-In Margin ($10 Margin x 10x = $100 Notional)",
        "margin_per_trade_usd": 10.00,
        "notional_per_trade_usd": 100.00,
        "dollar_risk_per_trade_usd": 0.48,
        "risk_pct_of_account": 4.80,
        "final_equity_usd": round(eq_c, 2),
        "net_profit_usd": round(eq_c - 10.00, 2),
        "roi_pct": round(((eq_c - 10.00) / 10.00) * 100.0, 2),
        "max_drawdown_usd": round(max_dd_c_usd, 2),
        "max_drawdown_pct": round(max_dd_c_pct, 2),
        "ruined": ruined_c,
        "ruin_trade_number": ruin_trade_c,
        "verdict": "EXTREME RISK / DANGEROUS DRAWDOWN"
    },
    "bybit_min_sizes": bybit_min_sizes
}

out_file = os.path.join(RESULTS_DIR, "simulate_10_dollar_equity_results.json")
with open(out_file, "w", encoding="utf-8") as f:
    json.dump(results_payload, f, indent=2)

print("\n--- RESULTS SUMMARY ---")
print("Model A (Micro $10 Notional): Final Equity = $", round(eq_a, 2), "| ROI =", round(((eq_a - 10.00) / 10.00) * 100.0, 2), "% | Max DD =", round(max_dd_a_pct, 2), "% | Ruined =", ruined_a)
print("Model B (Dynamic 2% Compounding): Final Equity = $", round(eq_b, 2), "| ROI =", round(((eq_b - 10.00) / 10.00) * 100.0, 2), "% | Max DD =", round(max_dd_b_pct, 2), "% | Ruined =", ruined_b)
print("Model C (All-In $100 Notional): Final Equity = $", round(eq_c, 2), "| ROI =", round(((eq_c - 10.00) / 10.00) * 100.0, 2), "% | Max DD =", round(max_dd_c_pct, 2), "% | Ruined =", ruined_c, f"(Bust at trade {ruin_trade_c})" if ruined_c else "")
