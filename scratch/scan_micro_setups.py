import os
import sys
import json
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scratch.micro_scalper_1dollar import COIN_SPECS, fetch_ticker, fetch_klines

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

print(f"{'SYMBOL':10} | {'PRICE':8} | {'5m TREND':8} | {'RSI(5m)':8} | {'LAST CANDLE':12} | {'RECOMMENDATION'}")
print("-" * 75)

for sym in COIN_SPECS.keys():
    k5 = fetch_klines(sym, '5', 25)
    if not k5 or len(k5) < 15:
        continue
    c5 = [c['close'] for c in k5]
    cur_p = c5[-1]
    ema9 = calc_ema(c5, 9)
    ema21 = calc_ema(c5, 21)
    rsi5 = calc_rsi(c5, 14)
    
    trend = "BULL" if ema9 > ema21 else "BEAR"
    last_c = k5[-1]
    c_color = "GREEN" if last_c['close'] >= last_c['open'] else "RED"
    
    # Check setup quality
    rec = "WAIT"
    if trend == "BULL" and 40 <= rsi5 <= 65 and cur_p >= ema9 * 0.998:
        rec = "EXCELLENT BUY"
    elif trend == "BULL" and rsi5 < 40:
        rec = "DIP BUY"
    elif trend == "BEAR" and rsi5 > 65:
        rec = "FADE SHORT"
        
    print(f"{sym:10} | {cur_p:<8.4f} | {trend:8} | {rsi5:<8.1f} | {c_color:12} | {rec}")
