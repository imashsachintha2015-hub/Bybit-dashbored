import os
import sys
import json
import time
import math
import urllib.request
from datetime import datetime

# Path setup
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backend_lib.bybit_client import BybitDemoClient
from backend_lib import auto_trade_state

# Load environment
with open('.env') as f:
    env = {}
    for line in f:
        line = line.strip()
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1)
            env[k.strip()] = v.strip().strip("'\"")

client = BybitDemoClient(env['BYBIT_API_KEY'], env['BYBIT_API_SECRET'], env['BYBIT_BASE_URL'])

SYMBOLS = ['SOLUSDT', 'NEARUSDT', 'AVAXUSDT', 'LINKUSDT', 'SUIUSDT', 'DOGEUSDT', 'ADAUSDT']

COIN_SPECS = {
    'SOLUSDT': {'qtyStep': 0.1, 'minQty': 0.1, 'precision': 1},
    'NEARUSDT': {'qtyStep': 0.1, 'minQty': 0.1, 'precision': 1},
    'AVAXUSDT': {'qtyStep': 0.1, 'minQty': 0.1, 'precision': 1},
    'LINKUSDT': {'qtyStep': 0.1, 'minQty': 0.1, 'precision': 1},
    'SUIUSDT': {'qtyStep': 1.0, 'minQty': 1.0, 'precision': 0},
    'DOGEUSDT': {'qtyStep': 1.0, 'minQty': 1.0, 'precision': 0},
    'ADAUSDT': {'qtyStep': 1.0, 'minQty': 1.0, 'precision': 0},
}

LOG_FILE = 'scratch/live_growth_session.log'

def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    safe_msg = msg.encode('ascii', errors='replace').decode('ascii')
    out = f"[{ts}] {safe_msg}"
    print(out, flush=True)
    with open(LOG_FILE, 'a', encoding='utf-8') as f:
        f.write(f"[{ts}] {msg}\n")

def fetch_klines(symbol, interval="15", limit=40):
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

def get_macro_trend():
    btc_k = fetch_klines('BTCUSDT', '60', 35)
    eth_k = fetch_klines('ETHUSDT', '60', 35)
    if not btc_k or not eth_k:
        return 'BULLISH' # default to bullish if API slow
    btc_c = [c['close'] for c in btc_k]
    eth_c = [c['close'] for c in eth_k]
    btc_ema = calc_ema(btc_c, 20)
    eth_ema = calc_ema(eth_c, 20)
    
    if btc_c[-1] >= btc_ema and eth_c[-1] >= eth_ema:
        return 'BULLISH'
    elif btc_c[-1] < btc_ema and eth_c[-1] < eth_ema:
        return 'BEARISH'
    return 'BULLISH_BIAS'

def find_best_opportunity():
    candidates = []
    for sym in SYMBOLS:
        k15 = fetch_klines(sym, '15', 35)
        k5  = fetch_klines(sym, '5', 25)
        if len(k15) < 20 or len(k5) < 15:
            continue
            
        c15 = [c['close'] for c in k15]
        c5  = [c['close'] for c in k5]
        
        ema9_15  = calc_ema(c15, 9)
        ema21_15 = calc_ema(c15, 21)
        rsi_15   = calc_rsi(c15, 14)
        rsi_5    = calc_rsi(c5, 14)
        
        cur_p = c15[-1]
        last_5 = k5[-1]
        
        # Bullish alignment: 15m trend is UP
        if ema9_15 > ema21_15:
            score = 60
            setup = "TREND_CONTINUATION"
            
            # Pullback towards 15m EMA9 (buying near value)
            dist_to_ema9 = (cur_p - ema9_15) / ema9_15
            if -0.006 <= dist_to_ema9 <= 0.008:
                score += 25
                setup = "EMA_PULLBACK_VALUE"
                
            # 5m oversold bounce or wick rejection
            c_range = last_5['high'] - last_5['low']
            lower_wick = min(last_5['open'], last_5['close']) - last_5['low']
            wick_ratio = lower_wick / c_range if c_range > 0 else 0
            if wick_ratio > 0.20:
                score += 10
                
            # RSI sweet spot: not over-extended (>85)
            if 40 <= rsi_5 <= 80:
                score += 10
                
            candidates.append({
                'symbol': sym,
                'score': score,
                'setup': setup,
                'price': cur_p,
                'rsi_5': rsi_5,
                'dist_ema9': dist_to_ema9
            })
            
    candidates.sort(key=lambda x: x['score'], reverse=True)
    return candidates[0] if candidates and candidates[0]['score'] >= 75 else None

def get_current_equity():
    try:
        wb = client.get_wallet_balance()
        coins = wb.get('result', {}).get('list', [{}])[0].get('coin', [])
        for c in coins:
            if c.get('coin') == 'USDT':
                return float(c.get('equity', 0))
    except Exception as e:
        log(f"[WARN] Error getting wallet balance: {e}")
    return None

def run_trading_system(target_gross_profit=5.0):
    log("================================================================================")
    log("     QUANTITATIVE RISK & CAPITAL GROWTH ENGINE: $10 -> $15 TARGET")
    log("================================================================================")
    
    start_equity = get_current_equity()
    if start_equity is None:
        start_equity = 10.0
    target_equity = start_equity + target_gross_profit
    
    log(f"Starting Wallet Equity: ${start_equity:.4f} USDT")
    log(f"Gross Profit Target   : +${target_gross_profit:.2f} USDT")
    log(f"Target Terminal Equity: ${target_equity:.4f} USDT")
    log(f"Risk Rules: Max 1 Position | $65 Notional | -0.85% SL | +0.40% Break-Even Ratchet | +1.25% TP")
    log("--------------------------------------------------------------------------------")
    
    # Track closed trades
    pnl_init = client.get_closed_pnl(limit=10)
    seen_trade_ids = set(r.get('orderId') for r in pnl_init.get('result', {}).get('list', []))
    
    accumulated_gross = 0.0
    trade_count = 0
    
    while accumulated_gross < target_gross_profit:
        # Check active positions on Bybit
        pos_res = client.get_positions()
        if pos_res.get('retCode') != 0:
            # Network or SSL timeout -- do not assume 0 positions!
            time.sleep(3)
            continue
        active = [p for p in pos_res.get('result', {}).get('list', []) if float(p.get('size', 0)) > 0]
        
        # Check for newly closed trades
        pnl_check = client.get_closed_pnl(limit=10)
        recent_closed = pnl_check.get('result', {}).get('list', [])
        for r in recent_closed:
            oid = r.get('orderId')
            if oid not in seen_trade_ids:
                seen_trade_ids.add(oid)
                pnl = float(r.get('closedPnl', 0))
                sym = r.get('symbol')
                trade_count += 1
                if pnl > 0:
                    accumulated_gross += pnl
                    log(f">>> [WIN #{trade_count}] {sym} CLOSED FOR +${pnl:.4f}! Cumulative Gross Profit: ${accumulated_gross:.4f} / ${target_gross_profit:.2f}")
                else:
                    log(f">>> [STOP/CUT #{trade_count}] {sym} closed for ${pnl:.4f}. Cumulative Gross Profit: ${accumulated_gross:.4f} / ${target_gross_profit:.2f}")
                
                # Check terminal target
                current_eq = get_current_equity()
                log(f"    Current Account Equity: ${current_eq:.4f} USDT | Remaining to target: ${max(0.0, target_gross_profit - accumulated_gross):.4f}")
                
        if accumulated_gross >= target_gross_profit:
            log(f"*** CONGRATULATIONS! Target Gross Profit of +${target_gross_profit:.2f} ACHIEVED! ***")
            current_eq = get_current_equity()
            log(f"*** Final Account Equity: ${current_eq:.4f} USDT. All trading halted safely. ***")
            break
            
        if len(active) == 0:
            # Look for highest probability entry
            macro = get_macro_trend()
            opp = find_best_opportunity()
            
            if opp:
                sym = opp['symbol']
                spec = COIN_SPECS.get(sym, {'qtyStep': 0.1, 'minQty': 0.1, 'precision': 1})
                cur_p = opp['price']
                
                # Size calculation: $65 notional ($6.50 margin @ 10x leverage)
                notional = 65.0
                step = spec['qtyStep']
                raw_q = notional / cur_p
                qty = max(spec['minQty'], round(raw_q / step) * step)
                qty = round(qty, spec['precision'])
                if spec['precision'] == 0:
                    qty = int(qty)
                    
                # TP (+1.25%) and SL (-0.85%)
                tp_price = round(cur_p * 1.0125, 4 if cur_p < 10 else 2)
                sl_price = round(cur_p * 0.9915, 4 if cur_p < 10 else 2)
                
                log(f"[SIGNAL IDENTIFIED] {sym} LONG (Score: {opp['score']}/100 | Setup: {opp['setup']})")
                log(f"   Executing: {qty} {sym} @ ~{cur_p} (Notional: ${qty*cur_p:.2f}) | TP: {tp_price} (+1.25%) | SL: {sl_price} (-0.85%)")
                
                client.set_leverage(sym, 10)
                res = client.place_order(
                    category="linear",
                    symbol=sym,
                    side="Buy",
                    order_type="Market",
                    qty=str(qty),
                    tp=str(tp_price),
                    sl=str(sl_price)
                )
                
                if res.get('retCode') == 0:
                    oid = res.get('result', {}).get('orderId')
                    log(f"[ORDER FILLED] Bybit Order Placed Successfully! OrderId: {oid}")
                    
                    # Sync thesis with state guardian
                    thesis_key = f"{sym}-Buy"
                    new_thesis = {
                        "symbol": sym,
                        "side": "Buy",
                        "entryPrice": cur_p,
                        "stopLoss": sl_price,
                        "takeProfit": tp_price,
                        "qty": qty,
                        "openedAt": int(time.time() * 1000)
                    }
                    auto_trade_state.save(theses={thesis_key: new_thesis})
                    time.sleep(2)
                    continue
                else:
                    log(f"[ORDER ERROR] Failed to place order: {res.get('retMsg')}")
            else:
                log(f"[SCANNING] Macro: {macro}. Waiting for high-probability pullback alignment on target watchlist...")
                
            time.sleep(6)
        else:
            # Active position monitoring
            p = active[0]
            sym = p['symbol']
            entry = float(p['avgPrice'])
            mark = float(p['markPrice'])
            unrealised = float(p['unrealisedPnl'])
            gain_pct = ((mark - entry) / entry) * 100.0 if entry > 0 else 0.0
            cur_sl = float(p.get('stopLoss') or 0.0)
            
            # Dynamic Break-Even Ratchet: at +0.40% gain, lock in break-even + 0.05%
            if gain_pct >= 0.40 and cur_sl < entry:
                be_price = round(entry * 1.0006, 4 if entry < 10 else 2)
                log(f"[BREAK-EVEN RATCHET TRIGGERED] {sym} reached +{gain_pct:.2f}% gain! Ratcheting Stop Loss to {be_price} (Risk-Free Trade)!")
                client.set_trading_stop(category="linear", symbol=sym, stop_loss=str(be_price))
                
            log(f"[LIVE MONITOR] {sym} | Entry: {entry} | Mark: {mark} ({gain_pct:+.2f}%) | UnPnl: ${unrealised:+.4f} | SL: {cur_sl} | TP target: +1.25%")
            time.sleep(3)
            
    log("[SYSTEM COMPLETE] Trading runner has finished its objective.")

if __name__ == '__main__':
    run_trading_system(target_gross_profit=5.0)
