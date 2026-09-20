import os
import sys
import json
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backend_lib.bybit_client import BybitDemoClient

def fetch_klines(symbol, interval="15", limit=50):
    url = f"https://api-demo.bybit.com/v5/market/kline?category=linear&symbol={symbol}&interval={interval}&limit={limit}"
    req = urllib.request.Request(url, headers={"User-Agent": "MASIS/3.0"})
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
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
        return []

def calc_ema(closes, period):
    if len(closes) < period:
        return closes[-1] if closes else 0
    k = 2 / (period + 1)
    ema = sum(closes[:period]) / period
    for c in closes[period:]:
        ema = c * k + ema * (1 - k)
    return ema

def calc_rsi(closes, period=14):
    if len(closes) < period + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i-1]
        gains.append(max(0, diff))
        losses.append(max(0, -diff))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))

symbols = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'NEARUSDT', 'AVAXUSDT', 'LINKUSDT', 'SUIUSDT', 'DOGEUSDT']

print("================================================================================")
print("             LIVE MARKET QUANTITATIVE TECHNICAL SCREENER")
print("================================================================================")
for sym in symbols:
    k60 = fetch_klines(sym, '60', 40)
    k15 = fetch_klines(sym, '15', 40)
    k5  = fetch_klines(sym, '5', 40)
    
    if not k60 or not k15 or not k5:
        continue
        
    c60 = [c['close'] for c in k60]
    c15 = [c['close'] for c in k15]
    c5  = [c['close'] for c in k5]
    
    ema20_60 = calc_ema(c60, 20)
    ema50_60 = calc_ema(c60, 50)
    ema9_15  = calc_ema(c15, 9)
    ema21_15 = calc_ema(c15, 21)
    rsi14_15 = calc_rsi(c15, 14)
    rsi14_5  = calc_rsi(c5, 14)
    
    price = c15[-1]
    h1_trend = "BULL" if (c60[-1] > ema20_60 and ema20_60 > ema50_60) else ("BEAR" if c60[-1] < ema20_60 else "NEUTRAL")
    m15_trend = "BULL" if ema9_15 > ema21_15 else "BEAR"
    
    last_candle = k5[-1]
    vol_avg = sum(c['volume'] for c in k5[-8:-1]) / 7.0
    vol_ratio = last_candle['volume'] / vol_avg if vol_avg > 0 else 1.0
    
    # Wick rejection
    c_range = last_candle['high'] - last_candle['low']
    lower_wick = min(last_candle['open'], last_candle['close']) - last_candle['low']
    wick_ratio = lower_wick / c_range if c_range > 0 else 0
    
    print(f"{sym:9} | Price: {price:<10.4f} | 1h: {h1_trend:7} | 15m: {m15_trend:4} | RSI(15m): {rsi14_15:4.1f} | RSI(5m): {rsi14_5:4.1f} | VolRatio: {vol_ratio:4.2f}x | Wick: {wick_ratio*100:4.1f}%")
