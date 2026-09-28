"""
CME-X4 Research Data Loader:
Fetches, aligns, and caches multi-timeframe linear perp klines from Bybit V5.
Timeframes: 1m, 3m, 5m, 15m, 60m (1h), 240m (4h).
Universe: BTCUSDT, ETHUSDT, SOLUSDT, XRPUSDT, LINKUSDT.
"""

import os
import json
import time
import urllib.request
import numpy as np
import pandas as pd

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
os.makedirs(DATA_DIR, exist_ok=True)

DEFAULT_SYMBOLS = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'XRPUSDT', 'LINKUSDT']
DEFAULT_INTERVALS = ['1', '3', '5', '15', '60', '240']

def fetch_bybit_klines(symbol, interval, limit=1000):
    cache_file = os.path.join(DATA_DIR, f"{symbol}_{interval}.json")
    if os.path.exists(cache_file):
        try:
            with open(cache_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if len(data) >= limit * 0.8:
                    return data
        except Exception:
            pass

    print(f"  [Fetching] {symbol} {interval}m from Bybit V5 (target {limit} bars)...")
    out = []
    end = int(time.time() * 1000)
    
    while len(out) < limit:
        batch_limit = min(200, limit - len(out))
        url = f"https://api.bybit.com/v5/market/kline?category=linear&symbol={symbol}&interval={interval}&limit={batch_limit}&end={end}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "MASIS-Research/4.0"})
            with urllib.request.urlopen(req, timeout=8) as r:
                res = json.loads(r.read().decode())
                if res.get('retCode') != 0:
                    break
                raw = res.get('result', {}).get('list', [])
                if not raw:
                    break
                
                batch = []
                for row in raw:
                    batch.append({
                        "start": int(row[0]),
                        "open": float(row[1]),
                        "high": float(row[2]),
                        "low": float(row[3]),
                        "close": float(row[4]),
                        "volume": float(row[5]),
                        "turnover": float(row[6])
                    })
                batch.sort(key=lambda x: x['start'])
                out = batch + out
                end = batch[0]['start'] - 1
                time.sleep(0.08)
        except Exception as e:
            print(f"    Warning fetching {symbol} {interval}m: {e}")
            break

    # Deduplicate by start timestamp
    seen = set()
    clean_out = []
    for c in out:
        if c['start'] not in seen:
            seen.add(c['start'])
            clean_out.append(c)
    clean_out.sort(key=lambda x: x['start'])

    if clean_out:
        with open(cache_file, 'w', encoding='utf-8') as f:
            json.dump(clean_out, f)
            
    return clean_out

def load_symbol_multi_tf(symbol, limit=1000):
    """Loads all requested intervals for a single symbol as pandas DataFrames."""
    dfs = {}
    for inv in DEFAULT_INTERVALS:
        bars = fetch_bybit_klines(symbol, inv, limit=limit)
        if bars:
            df = pd.DataFrame(bars)
            df['timestamp'] = pd.to_datetime(df['start'], unit='ms')
            df.set_index('timestamp', inplace=True)
            dfs[inv] = df
    return dfs

def load_universe(symbols=None, limit=1000):
    """Loads all symbols and timeframes."""
    symbols = symbols or DEFAULT_SYMBOLS
    universe = {}
    for s in symbols:
        universe[s] = load_symbol_multi_tf(s, limit=limit)
    return universe

if __name__ == '__main__':
    print("Testing CME-X4 Data Loader...")
    uni = load_universe(['BTCUSDT'], limit=300)
    for tf, df in uni['BTCUSDT'].items():
        print(f"BTC {tf}m: {len(df)} bars from {df.index[0]} to {df.index[-1]}")
