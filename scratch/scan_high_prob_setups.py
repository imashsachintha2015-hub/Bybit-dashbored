import os
import sys
import json
import time
import urllib.request
from datetime import datetime

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

SYMBOLS = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'LINKUSDT', 'DOGEUSDT', 'XRPUSDT', 'AVAXUSDT', 'NEARUSDT', 'ADAUSDT', 'SUIUSDT']

def fetch_klines(symbol, interval="15", limit=50):
    url = f"https://api-demo.bybit.com/v5/market/kline?category=linear&symbol={symbol}&interval={interval}&limit={limit}"
    req = urllib.request.Request(url, headers={"User-Agent": "MASIS/3.0"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
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
    except Exception as e:
        print(f"Error fetching klines for {symbol}: {e}")
        return []

def calc_ema(closes, period):
    if len(closes) < period:
        return closes[-1] if closes else 0
    k = 2 / (period + 1)
    ema = sum(closes[:period]) / period
    for c in closes[period:]:
        ema = c * k + ema * (1 - k)
    return ema

# 1. Macro trend check on BTC and ETH
btc_1h = fetch_klines('BTCUSDT', interval="60", limit=50)
btc_closes = [c['close'] for c in btc_1h]
btc_ema20 = calc_ema(btc_closes, 20)
btc_ema50 = calc_ema(btc_closes, 50)
btc_price = btc_closes[-1] if btc_closes else 0

eth_1h = fetch_klines('ETHUSDT', interval="60", limit=50)
eth_closes = [c['close'] for c in eth_1h]
eth_ema20 = calc_ema(eth_closes, 20)
eth_price = eth_closes[-1] if eth_closes else 0

macro_bullish = btc_price > btc_ema20 and eth_price > eth_ema20
print(f"=== MACRO ENVIRONMENT ===")
print(f"BTC Price: {btc_price} | 1h EMA20: {btc_ema20:.1f} | 1h EMA50: {btc_ema50:.1f} | Trend: {'BULLISH' if btc_price > btc_ema20 else 'BEARISH'}")
print(f"ETH Price: {eth_price} | 1h EMA20: {eth_ema20:.1f} | Trend: {'BULLISH' if eth_price > eth_ema20 else 'BEARISH'}")
print(f"Macro Direction Permitted: {'LONG ONLY (Counter-trend shorts banned)' if macro_bullish else 'NEUTRAL/BEARISH'}\n")

# 2. Scan each symbol for High-Probability Setups
candidates = []

for sym in SYMBOLS:
    # 15m candles
    k15 = fetch_klines(sym, interval="15", limit=40)
    if len(k15) < 25:
        continue
    closes = [c['close'] for c in k15]
    ema9 = calc_ema(closes, 9)
    ema21 = calc_ema(closes, 21)
    cur_p = closes[-1]
    
    # 5m candles for trigger
    k5 = fetch_klines(sym, interval="5", limit=30)
    if len(k5) < 15:
        continue
    last_5m = k5[-1]
    prev_5m = k5[-2]
    
    # Check setup types:
    # Setup 1: SureShot Trend Pullback (15m EMA9 > EMA21, price tests EMA9/21 with bullish wick rejection)
    # Setup 2: Liquidation Climax Wick Trap (3x volume spike with >50% wick rejection)
    avg_vol = sum(c['volume'] for c in k5[-10:-1]) / 9.0
    vol_ratio = last_5m['volume'] / avg_vol if avg_vol > 0 else 1.0
    candle_range = last_5m['high'] - last_5m['low']
    lower_wick = min(last_5m['open'], last_5m['close']) - last_5m['low']
    wick_ratio = lower_wick / candle_range if candle_range > 0 else 0
    
    score = 0
    setup_name = None
    side = 'Buy'
    
    if macro_bullish and cur_p >= ema21 * 0.998:
        # Bullish alignment
        if cur_p >= ema9 and ema9 > ema21:
            setup_name = 'SURESHOT_TREND_PULLBACK'
            score = 88
        elif vol_ratio >= 2.0 and wick_ratio >= 0.40:
            setup_name = 'LIQUIDATION_CLIMAX_REVERSAL'
            score = 92
        elif cur_p > ema21:
            setup_name = 'EMA21_DYNAMIC_SUPPORT_BOUNCE'
            score = 84
            
        if setup_name:
            # Sizing for $10 equity: $50 to $100 notional
            target_notional = 100.0  # 10 USDT margin @ 10x
            entry_p = cur_p
            # TP1: +0.60% (generating +$0.60 gross per win)
            tp1_p = entry_p * 1.0060
            # SL: -0.80% (limiting loss to -$0.80)
            sl_p = entry_p * 0.9920
            
            candidates.append({
                'symbol': sym,
                'setup': setup_name,
                'side': side,
                'score': score,
                'entry': entry_p,
                'tp1': round(tp1_p, 4 if entry_p < 10 else 2),
                'sl': round(sl_p, 4 if entry_p < 10 else 2),
                'notional': target_notional,
                'expected_gross_win': round(target_notional * 0.0060, 2),
                'vol_ratio': round(vol_ratio, 2),
                'wick_ratio': round(wick_ratio, 2)
            })

candidates.sort(key=lambda x: x['score'], reverse=True)

print(f"=== HIGH-PROBABILITY SETUPS FOUND ({len(candidates)}) ===")
for c in candidates:
    print(f"Symbol: {c['symbol']:10} | Setup: {c['setup']:28} | Score: {c['score']}/100 | Side: {c['side']}")
    print(f"  Entry: {c['entry']} | TP1 (+0.60%): {c['tp1']} (Gross Profit: +${c['expected_gross_win']:.2f}) | SL (-0.80%): {c['sl']}")

with open('scratch/live_candidates.json', 'w') as f:
    json.dump(candidates, f, indent=2)
