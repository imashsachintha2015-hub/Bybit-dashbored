import os
import sys
import json
import time
import math
import urllib.request
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backend_lib.bybit_client import BybitDemoClient
from scratch.deepseek_profit_claimer import SmartProfitClaimer

with open('.env') as f:
    env = {}
    for line in f:
        line = line.strip()
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1)
            env[k.strip()] = v.strip().strip("'\"")

client = BybitDemoClient(env['BYBIT_API_KEY'], env['BYBIT_API_SECRET'], env['BYBIT_BASE_URL'])
claimer = SmartProfitClaimer(client)

# 7 core micro-scalp coins ($1 margin = ~$10 notional)
COIN_CONFIG = {
    'SOLUSDT':  {'qty': 0.1,  'p_dec': 2, 'q_dec': 1, 'min_step': 0.01},
    'AVAXUSDT': {'qty': 1.3,  'p_dec': 3, 'q_dec': 1, 'min_step': 0.001},
    'NEARUSDT': {'qty': 2.9,  'p_dec': 3, 'q_dec': 1, 'min_step': 0.001},
    'LINKUSDT': {'qty': 0.9,  'p_dec': 3, 'q_dec': 1, 'min_step': 0.001},
    'DOGEUSDT': {'qty': 120,  'p_dec': 5, 'q_dec': 0, 'min_step': 0.00001},
    'SUIUSDT':  {'qty': 13,   'p_dec': 4, 'q_dec': 0, 'min_step': 0.0001},
    'ADAUSDT':  {'qty': 47,   'p_dec': 4, 'q_dec': 0, 'min_step': 0.0001},
}

LOG_FILE = 'scratch/scanner_live.log'
STATE_FILE = 'scratch/live_market_state.json'

def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    safe = msg.encode('ascii', errors='replace').decode('ascii')
    out = f"[{ts}] {safe}"
    print(out, flush=True)
    with open(LOG_FILE, 'a', encoding='utf-8') as f:
        f.write(f"[{ts}] {msg}\n")

def fetch_klines(symbol, interval, limit=25):
    url = f"https://api-demo.bybit.com/v5/market/kline?category=linear&symbol={symbol}&interval={interval}&limit={limit}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "MASIS/3.0"})
        with urllib.request.urlopen(req, timeout=4) as r:
            data = json.loads(r.read().decode())
            raw = data.get("result", {}).get("list", [])
            candles = []
            for row in reversed(raw):
                candles.append({
                    "open": float(row[1]),
                    "high": float(row[2]),
                    "low": float(row[3]),
                    "close": float(row[4]),
                    "vol": float(row[5])
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

def scan_symbol(symbol):
    k15 = fetch_klines(symbol, '15', 25)
    k5  = fetch_klines(symbol, '5', 25)
    if not k15 or not k5 or len(k15) < 15 or len(k5) < 15:
        return None
        
    c15 = [c['close'] for c in k15]
    c5  = [c['close'] for c in k5]
    
    cur_p = c5[-1]
    ema9_15  = calc_ema(c15, 9)
    ema21_15 = calc_ema(c15, 21)
    ema9_5   = calc_ema(c5, 9)
    ema21_5  = calc_ema(c5, 21)
    rsi5     = calc_rsi(c5, 14)
    rsi15    = calc_rsi(c15, 14)
    
    last_5 = k5[-1]
    c_range = max(0.0001, last_5['high'] - last_5['low'])
    lower_wick = min(last_5['open'], last_5['close']) - last_5['low']
    upper_wick = last_5['high'] - max(last_5['open'], last_5['close'])
    l_wick_pct = lower_wick / c_range
    u_wick_pct = upper_wick / c_range
    
    avg_vol = sum(c['vol'] for c in k5[-8:-1]) / 7.0 if len(k5) >= 8 else 1.0
    vol_ratio = last_5['vol'] / avg_vol if avg_vol > 0 else 1.0
    
    trend_15m = "BULL" if ema9_15 > ema21_15 else "BEAR"
    trend_5m  = "BULL" if ema9_5 > ema21_5 else "BEAR"
    
    # Quantitative scoring (0 to 100)
    score = 0
    setup = "NONE"
    direction = "NONE"
    
    if trend_15m == "BULL":
        score += 40
        if trend_5m == "BULL":
            score += 20
        # Pullback value zone (RSI between 40 and 65)
        if 42 <= rsi5 <= 65:
            score += 20
        # Wick rejection (buyers stepping in)
        if l_wick_pct >= 0.25:
            score += 15
        if vol_ratio >= 1.2:
            score += 5
        setup = "TREND_PULLBACK_LONG"
        direction = "BUY"
    elif trend_15m == "BEAR" and rsi5 >= 68:
        score = 75
        setup = "OVERBOUGHT_BEAR_PULLBACK"
        direction = "SELL"
        
    return {
        "symbol": symbol,
        "price": cur_p,
        "trend_15m": trend_15m,
        "trend_5m": trend_5m,
        "rsi_5m": round(rsi5, 1),
        "rsi_15m": round(rsi15, 1),
        "vol_ratio": round(vol_ratio, 2),
        "lower_wick_pct": round(l_wick_pct * 100, 1),
        "upper_wick_pct": round(u_wick_pct * 100, 1),
        "score": score,
        "setup": setup,
        "direction": direction,
        "recommended": score >= 80
    }

def get_account_status():
    wb = client.get_wallet_balance()
    coins = wb.get('result', {}).get('list', [{}])[0].get('coin', [])
    usdt = next((c for c in coins if c.get('coin') == 'USDT'), {})
    eq = float(usdt.get('equity', 0))
    
    pos_res = client.get_positions()
    raw_pos = pos_res.get('result', {}).get('list', []) if pos_res.get('retCode') == 0 else []
    active = []
    for p in raw_pos:
        if float(p.get('size', 0)) > 0:
            active.append({
                "symbol": p.get('symbol'),
                "side": p.get('side'),
                "size": float(p.get('size')),
                "entry": float(p.get('avgPrice')),
                "mark": float(p.get('markPrice')),
                "unpnl": float(p.get('unrealisedPnl')),
                "sl": float(p.get('stopLoss') or 0),
                "tp": float(p.get('takeProfit') or 0)
            })
    return eq, active

def run_scanner_loop():
    log("========================================================================")
    log("  LAUNCHING PERSISTENT 24/7 LIVE MARKET SCANNER & TELEMETRY ENGINE")
    log("  Universe: SOL, AVAX, NEAR, LINK, DOGE, SUI, ADA ($1 Micro-Scalps)")
    log("========================================================================")
    
    scan_count = 0
    
    while True:
        scan_count += 1
        try:
            eq, active = get_account_status()
            
            # Scan all symbols
            results = []
            for sym in COIN_CONFIG.keys():
                info = scan_symbol(sym)
                if info:
                    results.append(info)
                    
            results.sort(key=lambda x: x['score'], reverse=True)
            
            # Print brief scan summary every tick
            top = results[0] if results else None
            top_rec = f"{top['symbol']} {top['direction']} (Score {top['score']}/100 - {top['setup']})" if top else "None"
            
            pos_desc = f"{len(active)} active: " + ", ".join(f"{p['symbol']} {p['side']} (${p['unpnl']:+.3f})" for p in active) if active else "0 active"
            
            log(f"[SCAN #{scan_count}] Equity: ${eq:.4f} | {pos_desc} | Top Setup: {top_rec}")
            
            # Save complete snapshot to live_market_state.json
            payload = {
                "timestamp": int(time.time()),
                "scan_count": scan_count,
                "equity": eq,
                "active_positions": active,
                "leaderboard": results,
                "top_recommendation": top
            }
            with open(STATE_FILE, 'w', encoding='utf-8') as f:
                json.dump(payload, f, indent=2)

            # Sync to Supabase Cloud Database every 3 scans
            if scan_count % 3 == 0:
                try:
                    from backend_lib.supabase_client import supabase_kv_set, supabase_post
                    supabase_kv_set("live_market_state", payload)
                    if top and top.get("score", 0) >= 75:
                        supabase_post("live_market_signals", {
                            "symbol": top["symbol"],
                            "direction": top["direction"],
                            "score": top["score"],
                            "setup_name": top["setup"],
                            "rsi_5m": top["rsi_5m"],
                            "vol_ratio": top["vol_ratio"],
                            "lower_wick_pct": top["lower_wick_pct"],
                            "upper_wick_pct": top["upper_wick_pct"],
                            "entry_price": top.get("price"),
                            "was_traded": False,
                            "status": "SUGGESTED",
                            "result_reason": f"Top AI setup identified: {top['setup']} with score {top['score']}/100"
                        }, prefer="return=minimal")
                except Exception as e:
                    pass
                
            # Manage active positions with DeepSeek Smart Staged Profit Claimer
            for p in active:
                claimer.evaluate_and_claim(p)
                    
        except Exception as e:
            log(f"[SCANNER NOTICE] {e}")
            
        time.sleep(8)

if __name__ == '__main__':
    run_scanner_loop()
