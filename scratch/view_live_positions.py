import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backend_lib.bybit_client import BybitDemoClient

with open('.env') as f:
    env = {}
    for line in f:
        line = line.strip()
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1)
            env[k.strip()] = v.strip().strip("'\"")

client = BybitDemoClient(env['BYBIT_API_KEY'], env['BYBIT_API_SECRET'], env['BYBIT_BASE_URL'])
res = client.get_positions()
positions = [p for p in res.get('result', {}).get('list', []) if float(p.get('size', 0)) > 0]

print(f"=== TOTAL ACTIVE LIVE POSITIONS ON BYBIT: {len(positions)} ===")
total_unrealised = sum(float(p.get('unrealisedPnl', 0)) for p in positions)

for p in positions:
    sym = p.get('symbol', '')
    side = p.get('side', '')
    size = p.get('size', '')
    entry = float(p.get('avgPrice', 0))
    mark = float(p.get('markPrice', 0))
    pnl = float(p.get('unrealisedPnl', 0))
    tp = p.get('takeProfit', '')
    sl = p.get('stopLoss', '')
    notional = float(size) * entry
    print(f"{sym:10} | {side:4} | Qty: {size:<8} | Entry: {entry:<9.4f} | Mark: {mark:<9.4f} | Notional: ${notional:<6.2f} | PnL: ${pnl:+.4f} | TP: {tp} | SL: {sl}")

print(f"\nTotal Portfolio Unrealised PnL: ${total_unrealised:+.4f}")
