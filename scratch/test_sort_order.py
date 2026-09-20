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
res = client.get_closed_pnl(10)
raw = res.get('result', {}).get('list', [])

# Sort descending by updatedTime or createdTime
sorted_trades = sorted(raw, key=lambda x: int(x.get('updatedTime') or x.get('createdTime') or 0), reverse=True)

print(f"Total Bybit Closed Records: {len(sorted_trades)}")
for t in sorted_trades[:6]:
    sym = t.get('symbol')
    side = t.get('side')
    pnl = float(t.get('closedPnl', 0))
    ts = int(t.get('updatedTime') or t.get('createdTime') or 0)
    import time
    dt_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts / 1000))
    print(f"  [{dt_str}] {sym:10} {side:4} | PnL: ${pnl:+.4f} | OrderId: ...{t.get('orderId')[-8:]}")
