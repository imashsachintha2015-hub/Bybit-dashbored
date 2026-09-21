import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sqlite3
import json
from collections import Counter
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

# 1. Bybit closed trades
res = client.get_closed_pnl(limit=100)
trades = res.get('result', {}).get('list', [])
print(f"Bybit returned {len(trades)} recent closed trades.")

# 2. SQLite trade_episodes
conn = sqlite3.connect('market_knowledge.db')
conn.row_factory = sqlite3.Row
cur = conn.cursor()

symbols = ['LINKUSDT', 'DOGEUSDT', 'ADAUSDT', 'SUIUSDT', 'NEARUSDT', 'SOLUSDT', 'AVAXUSDT', 'XRPUSDT']
print("\n--- COIN TRADE HISTORIES & MFE PROFILES ---")
for sym in symbols:
    cur.execute("""
        SELECT 
            COUNT(*) as total,
            SUM(CASE WHEN pnl_net > 0 THEN 1 ELSE 0 END) as wins,
            SUM(CASE WHEN pnl_net <= 0 THEN 1 ELSE 0 END) as losses,
            AVG(CASE WHEN pnl_net > 0 THEN mfe_pct ELSE NULL END) as win_mfe_avg,
            MAX(CASE WHEN pnl_net > 0 THEN mfe_pct ELSE NULL END) as win_mfe_max,
            AVG(CASE WHEN pnl_net <= 0 THEN mfe_pct ELSE NULL END) as loss_mfe_avg,
            AVG(CASE WHEN pnl_net > 0 THEN mae_pct ELSE NULL END) as win_mae_avg,
            AVG(pnl_net) as avg_pnl
        FROM trade_episodes
        WHERE symbol = ?
    """, (sym,))
    r = cur.fetchone()
    if r and r['total'] > 0:
        tot = r['total']
        w = r['wins'] or 0
        l = r['losses'] or 0
        wr = round((w / tot) * 100, 1)
        win_mfe = round(r['win_mfe_avg'] or 0.0, 2)
        win_max_mfe = round(r['win_mfe_max'] or 0.0, 2)
        loss_mfe = round(r['loss_mfe_avg'] or 0.0, 2)
        win_mae = round(r['win_mae_avg'] or 0.0, 2)
        print(f"Symbol: {sym:10} | Trades: {tot:3} (Wins: {w:2}, Losses: {l:2}, WR: {wr:5.1f}%) | "
              f"Winner Avg MFE: +{win_mfe:.2f}% (Max: +{win_max_mfe:.2f}%) | "
              f"Winner Avg MAE: {win_mae:.2f}% | Loser Avg MFE: +{loss_mfe:.2f}%")

conn.close()
