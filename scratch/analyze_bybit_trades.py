import os
import sys
import json
import time
from datetime import datetime

# Add project root to sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend_lib.bybit_client import BybitDemoClient

# Load env file manually
env_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
env_vars = {}
if os.path.exists(env_file):
    with open(env_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env_vars[k.strip()] = v.strip().strip("\"'")

key = env_vars.get("BYBIT_API_KEY")
secret = env_vars.get("BYBIT_API_SECRET")
base_url = env_vars.get("BYBIT_BASE_URL", "https://api-demo.bybit.com")

print(f"Connecting to: {base_url} with key: {key[:4]}***")
client = BybitDemoClient(key, secret, base_url)

# 1. Wallet Balance
wallet = client.get_wallet_balance()
print("\n=== WALLET BALANCE ===")
if wallet.get("retCode") == 0:
    coins = wallet.get("result", {}).get("list", [{}])[0].get("coin", [])
    for c in coins:
        equity = float(c.get("equity", 0))
        if equity > 0 or c.get("coin") == "USDT":
            print(f"Coin: {c.get('coin')}, Equity: {c.get('equity')}, WalletBalance: {c.get('walletBalance')}, Available: {c.get('availableToWithdraw')}")
else:
    print(f"Error fetching wallet: {wallet}")

# 2. Current Positions
positions = client.get_positions()
print("\n=== CURRENT OPEN POSITIONS ===")
pos_list = positions.get("result", {}).get("list", [])
active_positions = [p for p in pos_list if float(p.get("size", 0)) > 0]
print(f"Active positions count: {len(active_positions)}")
for p in active_positions:
    print(f"Symbol: {p.get('symbol')}, Side: {p.get('side')}, Size: {p.get('size')}, Entry: {p.get('avgPrice')}, Mark: {p.get('markPrice')}, UnrealisedPnL: {p.get('unrealisedPnl')}, LiqPrice: {p.get('liqPrice')}, Leverage: {p.get('leverage')}")

# 3. Closed PnL
print("\n=== RECENT CLOSED PNL (Last 50) ===")
closed_pnl = client.get_closed_pnl(limit=50)
records = closed_pnl.get("result", {}).get("list", [])
print(f"Total closed trades retrieved: {len(records)}")

trades_by_date = {}
for r in records:
    # updatedTime or createdTime is in ms
    ts_ms = int(r.get("updatedTime", r.get("createdTime", 0)))
    dt = datetime.fromtimestamp(ts_ms / 1000.0) if ts_ms else None
    date_str = dt.strftime("%Y-%m-%d") if dt else "Unknown"
    trades_by_date.setdefault(date_str, []).append(r)

for date_str, tr_list in sorted(trades_by_date.items(), reverse=True):
    print(f"\n--- Date: {date_str} (Count: {len(tr_list)}) ---")
    daily_pnl = sum(float(x.get("closedPnl", 0)) for x in tr_list)
    print(f"Daily Closed PnL: ${daily_pnl:.4f}")
    for t in tr_list[:15]:
        ts = int(t.get("updatedTime", 0)) / 1000.0
        time_str = datetime.fromtimestamp(ts).strftime("%H:%M:%S")
        print(f"  [{time_str}] {t.get('symbol')} {t.get('side')} | Qty: {t.get('qty')} | Entry: {t.get('avgEntryPrice')} | Exit: {t.get('avgExitPrice')} | PnL: ${float(t.get('closedPnl', 0)):.4f} | ExitType: {t.get('execType', '')}")

# 4. Also check execution history (v5/execution/list)
print("\n=== RECENT EXECUTIONS (Trade Fills) ===")
exec_res = client.signed_request("GET", "/v5/execution/list", {"category": "linear", "limit": "50"})
exec_list = exec_res.get("result", {}).get("list", [])
print(f"Retrieved {len(exec_list)} execution records")
for ex in exec_list[:15]:
    ex_ts = int(ex.get("execTime", 0)) / 1000.0
    time_str = datetime.fromtimestamp(ex_ts).strftime("%Y-%m-%d %H:%M:%S") if ex_ts else "Unknown"
    print(f"  [{time_str}] {ex.get('symbol')} {ex.get('side')} | Price: {ex.get('execPrice')} | Qty: {ex.get('execQty')} | Fee: {ex.get('execFee')} | Type: {ex.get('execType')}")

# Save full dumps to scratch for deep inspection
with open(os.path.join(os.path.dirname(__file__), "api_dump_results.json"), "w") as f:
    json.dump({
        "wallet": wallet,
        "positions": active_positions,
        "closed_pnl": records,
        "executions": exec_list
    }, f, indent=2)
print("\nFull output dumped to scratch/api_dump_results.json")
