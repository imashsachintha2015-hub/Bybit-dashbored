import os
import sys
import json
import time
from datetime import datetime
import urllib.request

def fetch_klines(symbol, interval="60", limit=48):
    url = f"https://api-demo.bybit.com/v5/market/kline?category=linear&symbol={symbol}&interval={interval}&limit={limit}"
    req = urllib.request.Request(url, headers={"User-Agent": "MASIS/3.0"})
    with urllib.request.urlopen(req, timeout=10) as r:
        data = json.loads(r.read().decode())
        return data.get("result", {}).get("list", [])

# Let's inspect BTCUSDT and LINKUSDT klines
print("=== BTCUSDT 1h KLINES (Last 24) ===")
btc_klines = fetch_klines("BTCUSDT", "60", 24)
for k in reversed(btc_klines[-12:]):
    ts = int(k[0]) / 1000.0
    dt_str = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
    o, h, l, c, v = float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5])
    change_pct = ((c - o) / o) * 100
    print(f"[{dt_str}] O:{o:8.1f} H:{h:8.1f} L:{l:8.1f} C:{c:8.1f} | Change: {change_pct:+.2f}% Vol:{v:.1f}")

print("\n=== LINKUSDT 1h KLINES (around the 17th) ===")
link_klines = fetch_klines("LINKUSDT", "60", 36)
for k in reversed(link_klines[-18:]):
    ts = int(k[0]) / 1000.0
    dt_str = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
    o, h, l, c, v = float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5])
    change_pct = ((c - o) / o) * 100
    print(f"[{dt_str}] O:{o:7.3f} H:{h:7.3f} L:{l:7.3f} C:{c:7.3f} | Change: {change_pct:+.2f}% Vol:{v:.1f}")
