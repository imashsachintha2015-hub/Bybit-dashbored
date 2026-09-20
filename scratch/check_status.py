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
positions = client.get_positions()
active = [p for p in positions.get('result', {}).get('list', []) if float(p.get('size', 0)) > 0]
print(f"ACTIVE POSITIONS COUNT: {len(active)}")
for p in active:
    print(f"  {p['symbol']} {p['side']} size={p['size']} entry={p['avgPrice']} mark={p['markPrice']} unrealisedPnl={p['unrealisedPnl']} lev={p['leverage']}")

wallet = client.get_wallet_balance()
coins = wallet.get('result', {}).get('list', [{}])[0].get('coin', [])
for c in coins:
    if c.get('coin') == 'USDT':
        print(f"USDT Equity: {c.get('equity')} | Available: {c.get('availableToWithdraw')}")
