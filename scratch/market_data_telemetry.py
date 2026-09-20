import os
import sys
import json
import urllib.request
import time

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

def fetch_klines(symbol, interval, limit=30):
    url = f"https://api-demo.bybit.com/v5/market/kline?category=linear&symbol={symbol}&interval={interval}&limit={limit}"
    req = urllib.request.Request(url, headers={"User-Agent": "MASIS/3.0"})
    try:
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
    except Exception:
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

def get_telemetry():
    # 1. Wallet
    wb = client.get_wallet_balance()
    coins = wb.get('result', {}).get('list', [{}])[0].get('coin', [])
    usdt_info = next((c for c in coins if c.get('coin') == 'USDT'), {})
    equity = float(usdt_info.get('equity', 0))
    avail = float(usdt_info.get('availableToWithdraw', 0)) if usdt_info.get('availableToWithdraw') else equity
    
    # 2. Active Positions
    pos_res = client.get_positions()
    raw_pos = pos_res.get('result', {}).get('list', []) if pos_res.get('retCode') == 0 else []
    positions = []
    for p in raw_pos:
        if float(p.get('size', 0)) > 0:
            positions.append({
                "symbol": p.get('symbol'),
                "side": p.get('side'),
                "size": float(p.get('size', 0)),
                "entryPrice": float(p.get('avgPrice', 0)),
                "markPrice": float(p.get('markPrice', 0)),
                "unrealisedPnl": float(p.get('unrealisedPnl', 0)),
                "pnlPct": round(((float(p.get('markPrice', 0)) - float(p.get('avgPrice', 0))) / float(p.get('avgPrice', 1))) * (100 if p.get('side') == 'Buy' else -100), 2),
                "stopLoss": float(p.get('stopLoss') or 0),
                "takeProfit": float(p.get('takeProfit') or 0),
                "leverage": p.get('leverage')
            })
            
    # 3. Market Scan
    candidates = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'AVAXUSDT', 'NEARUSDT', 'LINKUSDT', 'SUIUSDT']
    market_snapshot = {}
    
    for sym in candidates:
        k60 = fetch_klines(sym, '60', 30)
        k15 = fetch_klines(sym, '15', 30)
        k5  = fetch_klines(sym, '5', 25)
        
        if not k60 or not k15 or not k5:
            continue
            
        c60 = [c['close'] for c in k60]
        c15 = [c['close'] for c in k15]
        c5  = [c['close'] for c in k5]
        
        cur_p = c5[-1]
        ema20_60 = calc_ema(c60, 20)
        ema9_15  = calc_ema(c15, 9)
        ema21_15 = calc_ema(c15, 21)
        ema9_5   = calc_ema(c5, 9)
        rsi_15   = calc_rsi(c15, 14)
        rsi_5    = calc_rsi(c5, 14)
        
        # 5m candle stats
        last_5 = k5[-1]
        prev_5 = k5[-2]
        avg_vol = sum(c['volume'] for c in k5[-8:-1]) / 7.0 if len(k5) >= 8 else 1.0
        vol_ratio = last_5['volume'] / avg_vol if avg_vol > 0 else 1.0
        
        c_range = last_5['high'] - last_5['low']
        lower_wick = min(last_5['open'], last_5['close']) - last_5['low']
        upper_wick = last_5['high'] - max(last_5['open'], last_5['close'])
        wick_l_pct = lower_wick / c_range if c_range > 0 else 0
        wick_u_pct = upper_wick / c_range if c_range > 0 else 0
        
        # Support / Resistance from last 20 15m candles
        sup_15 = min(c['low'] for c in k15[-15:])
        res_15 = max(c['high'] for c in k15[-15:])
        
        trend_1h = "BULL" if c60[-1] > ema20_60 else "BEAR"
        trend_15m = "BULL" if ema9_15 > ema21_15 else "BEAR"
        trend_5m = "BULL" if ema9_5 > cur_p else "BEAR"
        
        market_snapshot[sym] = {
            "price": cur_p,
            "trend_1h": trend_1h,
            "trend_15m": trend_15m,
            "rsi_15m": round(rsi_15, 1),
            "rsi_5m": round(rsi_5, 1),
            "vol_ratio_5m": round(vol_ratio, 2),
            "lower_wick_pct": round(wick_l_pct * 100, 1),
            "upper_wick_pct": round(wick_u_pct * 100, 1),
            "support_15m": sup_15,
            "resistance_15m": res_15,
            "recent_low_5m": min(c['low'] for c in k5[-4:]),
            "recent_high_5m": max(c['high'] for c in k5[-4:])
        }
        
    return {
        "timestamp": int(time.time()),
        "equity": equity,
        "available": avail,
        "activePositions": positions,
        "market": market_snapshot
    }

if __name__ == '__main__':
    data = get_telemetry()
    print(json.dumps(data, indent=2))
