import os
import sys
import json
from datetime import datetime

env = {}
with open('.env', 'r', encoding='utf-8') as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            k, v = line.split('=', 1)
            env[k.strip()] = v.strip().strip("'\"")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend_lib.bybit_client import BybitDemoClient
client = BybitDemoClient(env['BYBIT_API_KEY'], env['BYBIT_API_SECRET'], env['BYBIT_BASE_URL'])

pnl_res = client.get_closed_pnl(limit=100)
closed = pnl_res.get('result', {}).get('list', [])

print(f"Total Closed Bybit Trades: {len(closed)}")

# Also query Supabase trade_episodes
from backend_lib.supabase_client import supabase_get
episodes = supabase_get('trade_episodes', {'select': 'id,symbol,direction,entry_price,exit_price,pnl_net,pnl_pct,exit_reason,status,created_at', 'order': 'id.asc'})
print(f"Total Supabase Episodes: {len(episodes)}")

# Let's inspect the timeline of trades
print("\n=== TIMELINE ANALYSIS ===")
# Trades from earlier vs trades today
today_str = datetime.now().strftime("%Y-%m-%d")

today_trades = []
older_trades = []

for c in closed:
    ts = int(c.get('updatedTime') or c.get('createdTime') or 0) / 1000.0
    dt = datetime.fromtimestamp(ts)
    dt_str = dt.strftime("%Y-%m-%d %H:%M:%S")
    pnl = float(c.get('closedPnl', 0))
    c_info = {
        'symbol': c.get('symbol'),
        'side': c.get('side'),
        'qty': float(c.get('qty', 0)),
        'entry': float(c.get('avgEntryPrice', 0)),
        'exit': float(c.get('avgExitPrice', 0)),
        'pnl': pnl,
        'time': dt_str,
        'ts': ts
    }
    if dt.strftime("%Y-%m-%d") == today_str:
        today_trades.append(c_info)
    else:
        older_trades.append(c_info)

print(f"Older Trades (Before Analyze Mode): {len(older_trades)}")
if older_trades:
    w = [t for t in older_trades if t['pnl'] > 0]
    l = [t for t in older_trades if t['pnl'] < 0]
    gross_w = sum(t['pnl'] for t in w)
    gross_l = sum(abs(t['pnl']) for t in l)
    net_p = sum(t['pnl'] for t in older_trades)
    print(f"  Wins: {len(w)} | Losses: {len(l)} | Win Rate: {len(w)/len(older_trades)*100:.1f}%")
    print(f"  Gross Win: ${gross_w:.4f} | Gross Loss: -${gross_l:.4f} | Net: ${net_p:.4f}")
    if l and gross_l > 0:
        print(f"  Profit Factor: {gross_w / gross_l:.2f}")
    print(f"  Avg Loss: -${(gross_l/len(l)):.4f}" if l else "")
    print(f"  Avg Win: +${(gross_w/len(w)):.4f}" if w else "")

print(f"\nToday's Trades (After Analyze Mode Activated): {len(today_trades)}")
if today_trades:
    w = [t for t in today_trades if t['pnl'] > 0]
    l = [t for t in today_trades if t['pnl'] < 0]
    gross_w = sum(t['pnl'] for t in w)
    gross_l = sum(abs(t['pnl']) for t in l)
    net_p = sum(t['pnl'] for t in today_trades)
    print(f"  Wins: {len(w)} | Losses: {len(l)} | Win Rate: {len(w)/len(today_trades)*100:.1f}%")
    print(f"  Gross Win: ${gross_w:.4f} | Gross Loss: -${gross_l:.4f} | Net: ${net_p:.4f}")
    if l and gross_l > 0:
        print(f"  Profit Factor: {gross_w / gross_l:.2f}")
    print(f"  Avg Loss: -${(gross_l/len(l)):.4f}" if l else "")
    print(f"  Avg Win: +${(gross_w/len(w)):.4f}" if w else "")

print("\nRecent 10 Trades:")
for t in closed[:10]:
    ts = int(t.get('updatedTime') or 0) / 1000.0
    dt_str = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S") if ts > 0 else '--'
    print(f"  [{dt_str}] {t.get('symbol')} {t.get('side')} | Qty: {t.get('qty')} | PnL: ${float(t.get('closedPnl', 0)):+.4f}")
