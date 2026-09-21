import os
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

env = {}
env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env')
with open(env_path, 'r', encoding='utf-8') as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            k, v = line.split('=', 1)
            env[k.strip()] = v.strip().strip("'\"")
            os.environ[k.strip()] = v.strip().strip("'\"")

from backend_lib.bybit_client import BybitDemoClient
client = BybitDemoClient(env['BYBIT_API_KEY'], env['BYBIT_API_SECRET'], env['BYBIT_BASE_URL'])

wb = client.get_wallet_balance()
coins = wb.get('result', {}).get('list', [{}])[0].get('coin', [])
usdt = next((c for c in coins if c.get('coin') == 'USDT'), {})
equity = float(usdt.get('equity', 0))
wallet_bal = float(usdt.get('walletBalance', 0))

pos_res = client.get_positions()
positions = [p for p in pos_res.get('result', {}).get('list', []) if float(p.get('size', 0)) > 0]

print('=== ACCOUNT SUMMARY ===')
print(f'Total Equity: ${equity:.4f} USDT')
print(f'Wallet Balance: ${wallet_bal:.4f} USDT')
print(f'Open Positions Count: {len(positions)}')

for p in positions:
    sym = p.get('symbol')
    side = p.get('side')
    sz = float(p.get('size', 0))
    entry = float(p.get('avgPrice', 0))
    mark = float(p.get('markPrice', 0))
    unpnl = float(p.get('unrealisedPnl', 0))
    im = float(p.get('positionIM', 0))
    val = float(p.get('positionValue', 0))
    sl = float(p.get('stopLoss', 0) or 0)
    tp = float(p.get('takeProfit', 0) or 0)
    pnl_pct = (unpnl / im * 100) if im > 0 else 0
    print(f'--- {sym} {side.upper()} ---')
    print(f'  Size: {sz} units (~${val:.2f} Notional | Margin IM: ${im:.2f})')
    print(f'  Entry: {entry} | Mark: {mark}')
    print(f'  Unrealized PnL: ${unpnl:+.4f} ({pnl_pct:+.2f}%)')
    print(f'  Stop Loss: {sl} | Take Profit: {tp}')

from backend_lib.supabase_client import supabase_get
tm = supabase_get('target_mode_state', {'id': 'eq.1'})
print('\n=== TARGET / ANALYZE MODE STATE ===')
print(json.dumps(tm, indent=2))

state_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'live_market_state.json')
if os.path.exists(state_file):
    with open(state_file, 'r') as f:
        lms = json.load(f)
    print('\n=== CURRENT SCANNER TOP CANDIDATES ===')
    top = lms.get('top_recommendation')
    if top:
        print(f"Top Pick: {top.get('symbol')} {top.get('direction')} | Score: {top.get('score')}/100 | Setup: {top.get('setup')} | Price: {top.get('price')}")
    print('\nLeaderboard Top 5:')
    for item in lms.get('leaderboard', [])[:5]:
        print(f"  [{item['symbol']}] {item['direction']} | Score: {item['score']}/100 | Setup: {item['setup']} | Price: {item['price']} | RSI: {item.get('rsi_5m', '--')}")
