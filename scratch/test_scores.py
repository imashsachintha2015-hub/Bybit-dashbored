import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scratch.live_growth_runner import SYMBOLS, fetch_klines, calc_ema, calc_rsi

print(f"{'SYMBOL':10} | {'15m EMA9/21':12} | {'CurPrice':10} | {'DistEMA9':10} | {'RSI(5m)':8} | {'Score':6}")
print("-" * 65)

for sym in SYMBOLS:
    k15 = fetch_klines(sym, '15', 35)
    k5  = fetch_klines(sym, '5', 25)
    if len(k15) < 20 or len(k5) < 15:
        continue
    c15 = [c['close'] for c in k15]
    c5  = [c['close'] for c in k5]
    ema9_15 = calc_ema(c15, 9)
    ema21_15 = calc_ema(c15, 21)
    rsi_5 = calc_rsi(c5, 14)
    cur_p = c15[-1]
    
    bull = ema9_15 > ema21_15
    dist = (cur_p - ema9_15) / ema9_15
    
    score = 0
    if bull:
        score = 60
        if -0.006 <= dist <= 0.008:
            score += 25
        if 40 <= rsi_5 <= 80:
            score += 10
            
    print(f"{sym:10} | {'BULL' if bull else 'BEAR':12} | {cur_p:<10.4f} | {dist*100:+.2f}%     | {rsi_5:<8.1f} | {score:<6}")
