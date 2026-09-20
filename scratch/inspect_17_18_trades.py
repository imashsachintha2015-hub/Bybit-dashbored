import os
import sys
import json
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backend_lib.bybit_client import BybitDemoClient

env_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
env_vars = {}
with open(env_file, "r", encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env_vars[k.strip()] = v.strip().strip("\"'")

client = BybitDemoClient(env_vars.get("BYBIT_API_KEY"), env_vars.get("BYBIT_API_SECRET"), env_vars.get("BYBIT_BASE_URL"))

# Fetch 100 closed pnl
closed_pnl_res = client.get_closed_pnl(limit=100)
records = closed_pnl_res.get("result", {}).get("list", [])

# Let's also fetch orders history or recent orders
orders_res = client.signed_request("GET", "/v5/order/history", {"category": "linear", "limit": "50"})
orders = orders_res.get("result", {}).get("list", [])

# Filter 17th and 18th
target_records = []
for r in records:
    ts_ms = int(r.get("updatedTime", r.get("createdTime", 0)))
    dt = datetime.fromtimestamp(ts_ms / 1000.0)
    date_str = dt.strftime("%Y-%m-%d")
    if date_str in ["2026-09-17", "2026-09-18"]:
        target_records.append((dt, r))

target_records.sort(key=lambda x: x[0])

print(f"Total trades on 17th and 18th: {len(target_records)}")

wins = []
losses = []
gross_win = 0.0
gross_loss = 0.0

for dt, r in target_records:
    pnl = float(r.get("closedPnl", 0))
    entry = float(r.get("avgEntryPrice", 0))
    exit_p = float(r.get("avgExitPrice", 0))
    qty = float(r.get("qty", 0))
    side = r.get("side") # Side of closing order
    sym = r.get("symbol")
    order_id = r.get("orderId")
    exec_type = r.get("execType")
    
    # Position direction:
    # If closing side is "Buy", the original position was SHORT!
    # If closing side is "Sell", the original position was LONG!
    pos_side = "SHORT" if side.lower() == "buy" else "LONG"
    
    # Notional value
    notional = qty * entry
    
    if pnl > 0:
        wins.append((dt, sym, pos_side, qty, entry, exit_p, notional, pnl, exec_type))
        gross_win += pnl
    else:
        losses.append((dt, sym, pos_side, qty, entry, exit_p, notional, pnl, exec_type))
        gross_loss += abs(pnl)

print(f"\nSummary for 17th & 18th:")
print(f"Wins: {len(wins)} (Gross Profit: ${gross_win:.4f})")
print(f"Losses: {len(losses)} (Gross Loss: ${gross_loss:.4f})")
print(f"Net PnL: ${gross_win - gross_loss:.4f}")
print(f"Win Rate: {len(wins)/(len(wins)+len(losses))*100:.1f}%\n")

print("=== LOSS TRADES BREAKDOWN ===")
for dt, sym, pos_side, qty, entry, exit_p, notional, pnl, exec_type in losses:
    pct_move = ((exit_p - entry) / entry) * 100.0
    if pos_side == "SHORT":
        pos_pnl_pct = -pct_move
    else:
        pos_pnl_pct = pct_move
    print(f"[{dt.strftime('%Y-%m-%d %H:%M:%S')}] {sym:10} {pos_side:5} | Notional: ${notional:8.2f} (Qty: {qty}) | Entry: {entry} -> Exit: {exit_p} ({pct_move:+.2f}% move, Pos PnL: {pos_pnl_pct:+.2f}%) | PnL: ${pnl:+.4f}")

print("\n=== WIN TRADES BREAKDOWN ===")
for dt, sym, pos_side, qty, entry, exit_p, notional, pnl, exec_type in wins:
    pct_move = ((exit_p - entry) / entry) * 100.0
    if pos_side == "SHORT":
        pos_pnl_pct = -pct_move
    else:
        pos_pnl_pct = pct_move
    print(f"[{dt.strftime('%Y-%m-%d %H:%M:%S')}] {sym:10} {pos_side:5} | Notional: ${notional:8.2f} (Qty: {qty}) | Entry: {entry} -> Exit: {exit_p} ({pct_move:+.2f}% move, Pos PnL: {pos_pnl_pct:+.2f}%) | PnL: ${pnl:+.4f}")

