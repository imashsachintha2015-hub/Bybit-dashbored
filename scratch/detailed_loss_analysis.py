import json
from datetime import datetime

with open("scratch/api_dump_results.json", "r") as f:
    data = json.load(f)

closed = data.get("closed_pnl", [])
print(f"Total closed trades: {len(closed)}")

# Filter trades for 17th and 18th (and all others)
for r in closed:
    ts_ms = int(r.get("updatedTime", r.get("createdTime", 0)))
    dt = datetime.fromtimestamp(ts_ms / 1000.0)
    pnl = float(r.get("closedPnl", 0))
    entry = float(r.get("avgEntryPrice", 0))
    exit_p = float(r.get("avgExitPrice", 0))
    qty = float(r.get("qty", 0))
    side = r.get("side") # Note: Bybit closed-pnl "side" is the closing side or position side? Let's check!
    symbol = r.get("symbol")
    leverage = r.get("leverage")
    order_type = r.get("orderType")
    fill_count = r.get("fillCount")
    
    # price change %
    if entry > 0:
        pct_diff = ((exit_p - entry) / entry) * 100.0
    else:
        pct_diff = 0.0

    print(f"[{dt.strftime('%Y-%m-%d %H:%M:%S')}] {symbol:10} Side:{side:4} Qty:{qty:<8} Entry:{entry:<10} Exit:{exit_p:<10} Diff%:{pct_diff:+.2f}% PnL:${pnl:+.4f} Lev:{leverage} Type:{order_type}")
