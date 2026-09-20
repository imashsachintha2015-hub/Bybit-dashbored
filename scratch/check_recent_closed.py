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
res = client.get_closed_pnl(15)
trades = res.get('result', {}).get('list', [])
print(f"Total closed records fetched: {len(trades)}")
for t in trades:
    sym = t.get('symbol')
    side = t.get('side')
    qty = t.get('qty')
    entry = t.get('avgEntryPrice')
    exit_p = t.get('avgExitPrice')
    pnl = float(t.get('closedPnl', 0))
    exec_type = t.get('execType')
    ts = t.get('updatedTime')
    print(f"{sym:10} | {side:4} | Qty: {qty:<8} | Entry: {entry:<9} | Exit: {exit_p:<9} | PnL: ${pnl:+.4f} | Type: {exec_type}")
