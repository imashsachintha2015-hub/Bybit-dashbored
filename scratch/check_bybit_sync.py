import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sqlite3
from backend_lib.bybit_client import BybitDemoClient

ENV_PATH = '.env'
env = {}
with open(ENV_PATH, 'r') as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            k, v = line.split('=', 1)
            env[k.strip()] = v.strip().strip("'\"")

client = BybitDemoClient(env['BYBIT_API_KEY'], env['BYBIT_API_SECRET'], env['BYBIT_BASE_URL'])
res = client.get_closed_pnl(limit=100)
bybit_trades = res.get('result', {}).get('list', [])

conn = sqlite3.connect('market_knowledge.db')
c = conn.cursor()
c.execute('SELECT COUNT(*) FROM trade_episodes')
ep_count = c.fetchone()[0]
print(f"Bybit returned {len(bybit_trades)} trades. SQLite has {ep_count} episodes.")

# Check how many distinct symbols in Bybit
from collections import Counter
bb_counts = Counter(t['symbol'] for t in bybit_trades)
print("Bybit recent closed counts per symbol:", dict(bb_counts))
conn.close()
