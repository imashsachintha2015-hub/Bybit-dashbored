import os
import sys
import json
import urllib.request

symbols = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'XRPUSDT', 'DOGEUSDT', 'ADAUSDT', 'AVAXUSDT', 'LINKUSDT', 'DOTUSDT', 'NEARUSDT']

print("=== CHECKING REAL-TIME BYBIT MARKET SPREAD, VOLATILITY & OPPORTUNITIES ===")

for sym in symbols:
    url = f"https://api-demo.bybit.com/v5/market/tickers?category=linear&symbol={sym}"
    req = urllib.request.Request(url, headers={"User-Agent": "MASIS/3.0"})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            data = json.loads(r.read().decode())
            res = data.get("result", {}).get("list", [{}])[0]
            last_p = float(res.get("lastPrice", 0))
            bid = float(res.get("bid1Price", 0))
            ask = float(res.get("ask1Price", 0))
            spread_bps = ((ask - bid) / last_p) * 10000 if last_p > 0 else 0
            change_24h = float(res.get("price24hPcnt", 0)) * 100
            turnover = float(res.get("turnover24h", 0))
            print(f"{sym:10} | Price: {last_p:<10} | Spread: {spread_bps:4.1f} bps | 24h Change: {change_24h:+6.2f}% | 24h Turnover: ${turnover/1e6:6.1f}M")
    except Exception as e:
        print(f"Error fetching {sym}: {e}")
