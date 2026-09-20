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

SYMBOLS = ['SOLUSDT', 'NEARUSDT', 'LINKUSDT', 'DOGEUSDT', 'AVAXUSDT', 'SUIUSDT', 'ADAUSDT', 'XRPUSDT', 'ETHUSDT', 'BTCUSDT']

COIN_SPECS = {
    'BTCUSDT': {'qtyStep': 0.001, 'minQty': 0.001, 'precision': 3},
    'ETHUSDT': {'qtyStep': 0.01, 'minQty': 0.01, 'precision': 2},
    'SOLUSDT': {'qtyStep': 0.1, 'minQty': 0.1, 'precision': 1},
    'LINKUSDT': {'qtyStep': 0.1, 'minQty': 0.1, 'precision': 1},
    'DOGEUSDT': {'qtyStep': 1.0, 'minQty': 1.0, 'precision': 0},
    'XRPUSDT': {'qtyStep': 1.0, 'minQty': 1.0, 'precision': 0},
    'AVAXUSDT': {'qtyStep': 0.1, 'minQty': 0.1, 'precision': 1},
    'NEARUSDT': {'qtyStep': 0.1, 'minQty': 0.1, 'precision': 1},
    'ADAUSDT': {'qtyStep': 1.0, 'minQty': 1.0, 'precision': 0},
    'SUIUSDT': {'qtyStep': 1.0, 'minQty': 1.0, 'precision': 0},
}

if sys.stdout.encoding != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    # Clean ASCII safe string for windows consoles
    safe_msg = msg.encode('ascii', errors='replace').decode('ascii')
    out = f"[{ts}] {safe_msg}"
    print(out)
    with open('scratch/trade_session.log', 'a', encoding='utf-8') as f:
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

def get_macro_trend():
    btc = fetch_klines('BTCUSDT', '60', 30)
    eth = fetch_klines('ETHUSDT', '60', 30)
    if not btc or not eth:
        return 'NEUTRAL'
    btc_c = [c['close'] for c in btc]
    eth_c = [c['close'] for c in eth]
    btc_ema20 = calc_ema(btc_c, 20)
    eth_ema20 = calc_ema(eth_c, 20)
    
    if btc_c[-1] > btc_ema20 and eth_c[-1] > eth_ema20:
        return 'BULLISH'
    elif btc_c[-1] < btc_ema20 and eth_c[-1] < eth_ema20:
        return 'BEARISH'
    return 'NEUTRAL'

def find_best_trade(macro):
    scored = []
    for sym in SYMBOLS:
        k15 = fetch_klines(sym, '15', 30)
        k5 = fetch_klines(sym, '5', 25)
        if len(k15) < 20 or len(k5) < 15:
            continue
            
        c15 = [c['close'] for c in k15]
        ema9_15 = calc_ema(c15, 9)
        ema21_15 = calc_ema(c15, 21)
        cur_p = c15[-1]
        
        last_5 = k5[-1]
        prev_5 = k5[-2]
        avg_vol = sum(c['volume'] for c in k5[-8:-1]) / 7.0
        vol_ratio = last_5['volume'] / avg_vol if avg_vol > 0 else 1.0
        
        c_range = last_5['high'] - last_5['low']
        lower_wick = min(last_5['open'], last_5['close']) - last_5['low']
        wick_pct = lower_wick / c_range if c_range > 0 else 0
        
        score = 0
        setup = None
        
        if macro == 'BULLISH':
            # LONG ONLY
            if ema9_15 > ema21_15 and cur_p >= ema21_15 * 0.998:
                score += 50
                # Pullback testing EMA9/21
                if cur_p <= ema9_15 * 1.004:
                    score += 25
                if wick_pct >= 0.35 or vol_ratio >= 1.5:
                    score += 20
                setup = "SURESHOT_TREND_PULLBACK"
            elif vol_ratio >= 2.2 and wick_pct >= 0.45:
                score = 85
                setup = "LIQUIDATION_CLIMAX_REVERSAL"
                
        if score >= 75:
            scored.append({
                'symbol': sym,
                'score': score,
                'setup': setup,
                'price': cur_p,
                'side': 'Buy'
            })
            
    scored.sort(key=lambda x: x['score'], reverse=True)
    return scored[0] if scored else None

def get_active_positions():
    pos_res = client.get_positions()
    return [p for p in pos_res.get('result', {}).get('list', []) if float(p.get('size', 0)) > 0]

def run_session(target_gross=5.0):
    log("==========================================================")
    log("  LAUNCHING INSTITUTIONAL $10 -> $15 CAPITAL GROWTH ENGINE")
    log("  Target: Gross $5.00 Profit | Max Concurrent: 1 Trade")
    log("  Position Sizing: $65 Notional ($6.50 margin @ 10x leverage)")
    log("  Risk Management: -0.80% SL | BE at +0.40% | TP at +1.10%")
    log("==========================================================")
    
    start_time = time.time()
    accumulated_gross = 0.0
    initial_pnl = client.get_closed_pnl(limit=10)
    
    # Track starting closed trades count
    closed_start_ids = set(r.get('orderId') for r in initial_pnl.get('result', {}).get('list', []))
    
    while accumulated_gross < target_gross:
        # Check active positions
        active = get_active_positions()
        
        # Check closed PnL for newly closed trades
        pnl_res = client.get_closed_pnl(limit=10)
        recent = pnl_res.get('result', {}).get('list', [])
        for r in recent:
            oid = r.get('orderId')
            if oid not in closed_start_ids:
                closed_start_ids.add(oid)
                cpnl = float(r.get('closedPnl', 0))
                sym = r.get('symbol')
                if cpnl > 0:
                    accumulated_gross += cpnl
                    log(f"[WIN] TRADE WON! {sym} closed for +${cpnl:.4f}! Cumulative Gross Profit: ${accumulated_gross:.4f} / ${target_gross:.2f}")
                else:
                    log(f"[STOP] TRADE STOPPED: {sym} closed for ${cpnl:.4f}. Cumulative Gross Profit: ${accumulated_gross:.4f} / ${target_gross:.2f}")
                    
        if accumulated_gross >= target_gross:
            log(f"[TARGET REACHED] Gross profit reached ${accumulated_gross:.2f}! Total equity is now ~$15.00! Session complete.")
            break
            
        if len(active) == 0:
            # Look for trade setup
            macro = get_macro_trend()
            log(f"[SCAN] Scanning market... Macro Trend: {macro} (Long Only)")
            best = find_best_trade(macro)
            
            if best:
                sym = best['symbol']
                spec = COIN_SPECS.get(sym, {'qtyStep': 0.1, 'minQty': 0.1, 'precision': 1})
                cur_p = best['price']
                
                # Sizing: $65 notional ($6.50 margin @ 10x leverage, leaving $3.50 cash buffer)
                notional = 65.0
                step = spec['qtyStep']
                raw_q = notional / cur_p
                qty = max(spec['minQty'], round(raw_q / step) * step)
                qty = round(qty, spec['precision'])
                if spec['precision'] == 0:
                    qty = int(qty)
                    
                # TP (+1.10%) and SL (-0.80%)
                tp = round(cur_p * 1.0110, 4 if cur_p < 10 else 2)
                sl = round(cur_p * 0.9920, 4 if cur_p < 10 else 2)
                
                log(f"[ENTRY] ENTERING TRADE: {sym} LONG qty={qty} @ ~{cur_p} (Notional: ${qty * cur_p:.2f})")
                log(f"   Setup: {best['setup']} (Score {best['score']}/100) | TP (+1.10%): {tp} | SL (-0.80%): {sl}")
                
                client.set_leverage(sym, 10)
                res = client.place_order(
                    category="linear",
                    symbol=sym,
                    side="Buy",
                    order_type="Market",
                    qty=str(qty),
                    tp=str(tp),
                    sl=str(sl)
                )
                if res.get('retCode') == 0:
                    log(f"[FILLED] Live Order FILLED on Bybit! Order ID: {res.get('result', {}).get('orderId')}")
                else:
                    log(f"[REJECTED] Order failed: {res.get('retMsg')}")
            else:
                log("[WAIT] No Grade-A+ setup currently at exact entry trigger. Waiting for next 15s candle tick...")
                
            time.sleep(15)
        else:
            # Position active: Monitor and apply Break-Even Ratchet
            p = active[0]
            sym = p['symbol']
            entry = float(p['avgPrice'])
            mark = float(p['markPrice'])
            unrealised = float(p['unrealisedPnl'])
            gain_pct = ((mark - entry) / entry) * 100
            
            # Check Break-Even trigger at +0.40%
            current_sl = float(p.get('stopLoss') or 0)
            if gain_pct >= 0.40 and current_sl < entry:
                be_stop = round(entry * 1.0005, 4 if entry < 10 else 2) # entry + tiny fee cover
                log(f"[RATCHET] BREAK-EVEN RATCHET: {sym} reached +{gain_pct:.2f}% gain! Moving Stop Loss to {be_stop} (Risk-Free Trade)!")
                client.set_trading_stop(category="linear", symbol=sym, stop_loss=str(be_stop))
                
            log(f"[MONITOR] {sym}: Entry={entry} | Mark={mark} ({gain_pct:+.2f}%) | Unrealised PnL: ${unrealised:+.4f} | Target TP: +1.10%")
            time.sleep(4)
            
    # Final check of wallet
    w = client.get_wallet_balance()
    coins = w.get("result", {}).get("list", [{}])[0].get("coin", [])
    for c in coins:
        if c.get("coin") == "USDT":
            log(f"FINAL ACCOUNT STATUS: Equity = ${float(c.get('equity', 0)):.4f} USDT")

if __name__ == '__main__':
    run_session(target_gross=5.0)
