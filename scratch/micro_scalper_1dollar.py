import os
import sys
import json
import time
import math
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

# Micro-scalp coin universe (liquid coins supporting ~$10 notional)
COIN_SPECS = {
    'SOLUSDT':  {'qty': 0.1,  'price_round': 2, 'qty_round': 1}, # ~$10.5 notional
    'AVAXUSDT': {'qty': 1.3,  'price_round': 3, 'qty_round': 1}, # ~$10.2 notional
    'NEARUSDT': {'qty': 2.9,  'price_round': 3, 'qty_round': 1}, # ~$10.0 notional
    'LINKUSDT': {'qty': 0.9,  'price_round': 3, 'qty_round': 1}, # ~$10.5 notional
    'DOGEUSDT': {'qty': 120,  'price_round': 5, 'qty_round': 0}, # ~$10.0 notional
    'SUIUSDT':  {'qty': 13,   'price_round': 4, 'qty_round': 0}, # ~$10.0 notional
    'ADAUSDT':  {'qty': 47,   'price_round': 4, 'qty_round': 0}, # ~$10.0 notional
}

LOG_FILE = 'scratch/micro_scalper.log'

def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    safe = msg.encode('ascii', errors='replace').decode('ascii')
    print(f"[{ts}] {safe}", flush=True)
    with open(LOG_FILE, 'a', encoding='utf-8') as f:
        f.write(f"[{ts}] {msg}\n")

def fetch_ticker(symbol):
    url = f"https://api-demo.bybit.com/v5/market/tickers?category=linear&symbol={symbol}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "MASIS/3.0"})
        with urllib.request.urlopen(req, timeout=5) as r:
            data = json.loads(r.read().decode())
            item = data.get("result", {}).get("list", [{}])[0]
            return {
                "lastPrice": float(item.get("lastPrice", 0)),
                "bid1": float(item.get("bid1Price", 0)),
                "ask1": float(item.get("ask1Price", 0)),
            }
    except Exception:
        return None

def fetch_klines(symbol, interval="5", limit=20):
    url = f"https://api-demo.bybit.com/v5/market/kline?category=linear&symbol={symbol}&interval={interval}&limit={limit}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "MASIS/3.0"})
        with urllib.request.urlopen(req, timeout=5) as r:
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

def get_active_positions():
    res = client.get_positions()
    if res.get('retCode') != 0:
        return None
    return [p for p in res.get('result', {}).get('list', []) if float(p.get('size', 0)) > 0]

def place_micro_scalp(symbol, side="Buy", take_profit_pct=0.0060, stop_loss_pct=0.0050):
    spec = COIN_SPECS[symbol]
    ticker = fetch_ticker(symbol)
    if not ticker or ticker['lastPrice'] <= 0:
        log(f"[ERROR] Could not fetch ticker for {symbol}")
        return False
        
    cur_p = ticker['lastPrice']
    qty = spec['qty']
    if spec['qty_round'] == 0:
        qty = int(qty)
    else:
        qty = round(qty, spec['qty_round'])
        
    p_dec = spec['price_round']
    
    if side == "Buy":
        tp = round(cur_p * (1 + take_profit_pct), p_dec)
        sl = round(cur_p * (1 - stop_loss_pct), p_dec)
    else:
        tp = round(cur_p * (1 - take_profit_pct), p_dec)
        sl = round(cur_p * (1 + stop_loss_pct), p_dec)
        
    notional = qty * cur_p
    margin = notional / 10.0
    
    log(f"[ENTRY] Firing $1-Margin Micro-Scalp on {symbol} {side.upper()} | Qty: {qty} (Notional: ${notional:.2f}, Margin: ${margin:.2f})")
    log(f"        Entry: {cur_p} | TP (+0.60%): {tp} (Target +$0.06) | SL (-0.50%): {sl} (Max Risk -$0.05)")
    
    client.set_leverage(symbol, 10)
    res = client.place_order(
        category="linear",
        symbol=symbol,
        side=side,
        order_type="Market",
        qty=str(qty),
        tp=str(tp),
        sl=str(sl)
    )
    
    if res.get('retCode') == 0:
        oid = res.get('result', {}).get('orderId')
        log(f"[SUCCESS] Order Filled on Bybit! Order ID: {oid}")
        return True
    else:
        log(f"[REJECTED] Bybit rejected order: {res.get('retMsg')}")
        return False

def check_ratchet_and_pnl(active_positions):
    for p in active_positions:
        sym = p['symbol']
        side = p['side']
        entry = float(p['avgPrice'])
        mark = float(p['markPrice'])
        sl = float(p.get('stopLoss') or 0)
        unpnl = float(p.get('unrealisedPnl', 0))
        spec = COIN_SPECS.get(sym, {'price_round': 2})
        p_dec = spec['price_round']
        
        gain_pct = ((mark - entry) / entry) * 100.0 if side == 'Buy' else ((entry - mark) / entry) * 100.0
        
        # Break-Even Ratchet: at +0.30% gain (halfway to +0.60% TP), lock in Break-Even + 0.04%
        if gain_pct >= 0.30 and ((side == 'Buy' and sl < entry) or (side == 'Sell' and sl > entry)):
            be_price = round(entry * 1.0004 if side == 'Buy' else entry * 0.9996, p_dec)
            log(f"[RATCHET] {sym} reached +{gain_pct:.2f}% gain (+$0.03)! Ratcheting SL to {be_price} (RISK-FREE)")
            client.set_trading_stop(category='linear', symbol=sym, stop_loss=str(be_price))

if __name__ == '__main__':
    print("Micro-Scalper Module Loaded Successfully.")
