import os
import sys
import json
import urllib.request

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

def get_klines(symbol, interval, limit=30):
    url = f"https://api-demo.bybit.com/v5/market/kline?category=linear&symbol={symbol}&interval={interval}&limit={limit}"
    req = urllib.request.Request(url, headers={"User-Agent": "MASIS/3.0"})
    with urllib.request.urlopen(req, timeout=6) as r:
        data = json.loads(r.read().decode())
        raw = data.get("result", {}).get("list", [])
        candles = []
        for row in reversed(raw):
            candles.append({
                "start": int(row[0]),
                "open": float(row[1]),
                "high": float(row[2]),
                "low": float(row[3]),
                "close": float(row[4]),
                "volume": float(row[5])
            })
        return candles

# 1. Current Position
pos = client.get_positions(symbol='SOLUSDT')
active_sol = [p for p in pos.get('result', {}).get('list', []) if float(p.get('size', 0)) > 0]
print("=== CURRENT ACTIVE POSITION ===")
if active_sol:
    p = active_sol[0]
    entry = float(p['avgPrice'])
    mark = float(p['markPrice'])
    pnl = float(p['unrealisedPnl'])
    gain = ((mark - entry)/entry)*100
    print(f"SOLUSDT LONG: Size={p['size']} | Entry={entry} | Mark={mark} | PnL=${pnl:+.4f} ({gain:+.2f}%) | SL={p.get('stopLoss')} | TP={p.get('takeProfit')}")
else:
    print("No active SOL position.")

# 2. SOL Multi-timeframe analysis
k15 = get_klines('SOLUSDT', '15', 20)
k5 = get_klines('SOLUSDT', '5', 20)
k1 = get_klines('SOLUSDT', '1', 20)

print("\n=== SOLUSDT RECENT 5-MIN CANDLES ===")
for c in k5[-6:]:
    color = "GREEN" if c['close'] >= c['open'] else "RED  "
    body = abs(c['close'] - c['open'])
    c_range = c['high'] - c['low']
    upper_wick = c['high'] - max(c['open'], c['close'])
    lower_wick = min(c['open'], c['close']) - c['low']
    print(f"O: {c['open']:<7.2f} H: {c['high']:<7.2f} L: {c['low']:<7.2f} C: {c['close']:<7.2f} | {color} | Vol: {c['volume']:<8.1f} | L-Wick: {lower_wick:<5.2f} U-Wick: {upper_wick:<5.2f}")

# 3. BTC Context
btc5 = get_klines('BTCUSDT', '5', 5)
print(f"\nBTC Last Price: {btc5[-1]['close']} (5m change: {((btc5[-1]['close'] - btc5[-5]['open'])/btc5[-5]['open'])*100:+.2f}%)")
