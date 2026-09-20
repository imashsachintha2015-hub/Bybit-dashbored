"""
Smart Micro-Scalp Execution Engine with DeepSeek Staged Profit Claimer
Target: Grow account from ~$9.50 to $15.00 gross USDT.
Sizing: $1.00 - $1.20 margin per trade ($10 - $12 notional @ 10x leverage).
Max Concurrent: 1-2 trades (maintains $7.50+ / 80% free cash reserve).
Profit Claimer: DeepSeek staged exits (TAKE_PARTIAL, TAKE_ALL, KEEP_RUNNER).
"""

import os
import sys
import json
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backend_lib.bybit_client import BybitDemoClient
from backend_lib.market_knowledge import kb
from scratch.deepseek_profit_claimer import SmartProfitClaimer, round_price, round_qty, COIN_SPECS, fetch_klines, extract_microstructure

# Load environment
ENV_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env')
env = {}
with open(ENV_PATH, 'r', encoding='utf-8') as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            k, v = line.split('=', 1)
            env[k.strip()] = v.strip().strip("'\"")

client = BybitDemoClient(env['BYBIT_API_KEY'], env['BYBIT_API_SECRET'], env['BYBIT_BASE_URL'])
claimer = SmartProfitClaimer(client)

STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'live_market_state.json')
EXECUTOR_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'smart_executor.log')

MAX_CONCURRENT_TRADES = 1  # Sequential execution for maximum safety
NOTIONAL_PER_TRADE = 11.0  # ~$1.10 margin @ 10x leverage

# Cache for tracking trade lifecycles and MFE/MAE
tracked_trades = {}  # symbol -> {entry, side, size, max_gain, min_gain, opened_at}

def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    safe = msg.encode('ascii', errors='replace').decode('ascii')
    out = f"[{ts}] {safe}"
    print(out, flush=True)
    with open(EXECUTOR_LOG, 'a', encoding='utf-8') as f:
        f.write(f"[{ts}] {msg}\n")

def get_account_state():
    wb = client.get_wallet_balance()
    coins = wb.get('result', {}).get('list', [{}])[0].get('coin', [])
    usdt = next((c for c in coins if c.get('coin') == 'USDT'), {})
    eq = float(usdt.get('equity', 0))
    
    pos_res = client.get_positions()
    raw_pos = pos_res.get('result', {}).get('list', []) if pos_res.get('retCode') == 0 else []
    active = []
    for p in raw_pos:
        sz = float(p.get('size', 0))
        if sz > 0:
            active.append({
                "symbol": p.get('symbol'),
                "side": p.get('side'),
                "size": sz,
                "entry": float(p.get('avgPrice')),
                "mark": float(p.get('markPrice')),
                "unpnl": float(p.get('unrealisedPnl')),
                "sl": float(p.get('stopLoss') or 0),
                "tp": float(p.get('takeProfit') or 0)
            })
    return eq, active

def calculate_trade_qty(symbol, price):
    raw_qty = NOTIONAL_PER_TRADE / price
    return round_qty(symbol, raw_qty)

def check_and_learn_closed_trades(current_active_symbols):
    """Detects when a tracked position has closed, records to SQLite, and triggers DeepSeek learning."""
    global tracked_trades
    closed_syms = [s for s in tracked_trades.keys() if s not in current_active_symbols]
    for sym in closed_syms:
        trade_info = tracked_trades.pop(sym, None)
        if not trade_info:
            continue
        try:
            # Query Bybit closed PnL for exact settled numbers
            closed_res = client.get_closed_pnl(limit=5)
            closed_list = closed_res.get('result', {}).get('list', [])
            matched = next((c for c in closed_list if c.get('symbol') == sym), None)
            
            pnl_net = float(matched.get('closedPnl', 0)) if matched else 0.0
            exit_p = float(matched.get('avgExitPrice', 0)) if matched else 0.0
            entry_p = float(matched.get('avgEntryPrice', trade_info.get('entry', 0))) if matched else trade_info.get('entry', 0)
            pnl_pct = ((exit_p - entry_p) / entry_p) * 100.0 if trade_info.get('side') == 'Buy' else ((entry_p - exit_p) / entry_p) * 100.0
            
            k5 = fetch_klines(sym, '5', 10)
            k1 = fetch_klines(sym, '1', 10)
            micro = extract_microstructure(k5, k1, trade_info.get('side', 'Buy'))
            
            record_payload = {
                "symbol": sym,
                "direction": trade_info.get('side', 'BUY'),
                "entry_price": entry_p,
                "exit_price": exit_p,
                "size": trade_info.get('size', 0),
                "pnl_net": pnl_net,
                "pnl_pct": pnl_pct,
                "mfe_pct": trade_info.get('max_gain', 0),
                "mae_pct": trade_info.get('min_gain', 0),
                "exit_reason": "TAKE_PROFIT_OR_CLAIM" if pnl_net > 0 else "STOP_LOSS"
            }
            log(f"🧠 [LEARNING TRIGGERED] Trade {sym} closed ({pnl_pct:+.2f}%, ${pnl_net:+.4f})! Generating DeepSeek reflection...")
            learn_res = kb.record_trade_close(record_payload, micro)
            log(f"   Stored Rule: {learn_res.get('reflection', {}).get('rule')}")
        except Exception as e:
            log(f"[LEARN NOTICE] Error processing closed trade {sym}: {e}")

def run_single_cycle():
    eq, active = get_account_state()
    
    # 1. Check Target Mode state from SQLite Knowledge Base
    target_state = kb.get_target_state(current_equity=eq)
    target_equity = target_state.get('target_equity', 15.0)
    is_armed = target_state.get('is_armed', True)
    status = target_state.get('status', 'ACTIVE')

    # Circuit Breaker: Target Hit
    if eq >= target_equity or status == 'TARGET_REACHED_PARKED':
        log(f"🎯 [TARGET REACHED & PARKED] Current Equity: ${eq:.4f} >= ${target_equity:.2f} USDT (Progress: {target_state.get('progress_pct')}%)! AI Engine Standing By.")
        return True

    # Check if paused
    if not is_armed:
        log(f"⏸️ [ANALYZE MODE STANDBY] Waiting for user arming in dashboard (Target: ${target_equity:.2f})...")
        return True
        
    active_syms = [p['symbol'] for p in active]
    check_and_learn_closed_trades(active_syms)
        
    # 2. Manage all active positions with DeepSeek Smart Profit Claimer
    if active:
        for pos in active:
            sym = pos['symbol']
            side = pos['side']
            entry = pos['entry']
            mark = pos['mark']
            unpnl = pos['unpnl']
            gain_pct = ((mark - entry) / entry) * 100.0 if side.upper() == 'BUY' else ((entry - mark) / entry) * 100.0
            
            # Update MFE / MAE tracking
            if sym not in tracked_trades:
                tracked_trades[sym] = {'entry': entry, 'side': side, 'size': pos['size'], 'max_gain': gain_pct, 'min_gain': gain_pct, 'opened_at': time.time()}
            else:
                tracked_trades[sym]['max_gain'] = max(tracked_trades[sym]['max_gain'], gain_pct)
                tracked_trades[sym]['min_gain'] = min(tracked_trades[sym]['min_gain'], gain_pct)
            
            log(f"📊 [MONITOR] {sym} {side} | Mark: {mark} (Entry: {entry}) | Gain: {gain_pct:+.2f}% (${unpnl:+.4f}) | Peak Gain: {tracked_trades[sym]['max_gain']:+.2f}%")
            
            # DeepSeek claimer triggers if gain >= +0.32%
            if gain_pct >= 0.32:
                claimer.evaluate_and_claim(pos)
                
        # If we have reached max concurrent positions, don't open new ones
        if len(active) >= MAX_CONCURRENT_TRADES:
            return True
            
    # 3. If under max concurrent trades, inspect market state for Top Setup
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, 'r', encoding='utf-8') as f:
                market = json.load(f)
        except Exception:
            market = {}
            
        top = market.get('top_recommendation')
        active_syms = [p['symbol'] for p in active]
        
        if top and top.get('recommended') and top.get('symbol') not in active_syms:
            sym = top['symbol']
            direction = top['direction']
            score = top['score']
            cur_price = top['price']
            
            if score >= 90 and direction == 'BUY':
                qty = calculate_trade_qty(sym, cur_price)
                if qty <= 0:
                    return True
                    
                # Calculate initial Stop Loss (-0.75%) and initial Take Profit (+0.90%)
                sl_price = round_price(sym, cur_price * 0.9925)
                tp_price = round_price(sym, cur_price * 1.0090)
                
                log(f"🚀 [ENTER TRADE] Top Setup Found: {sym} {direction} (Score: {score}/100 - {top['setup']})")
                log(f"   Size: {qty} units (~${qty * cur_price:.2f} notional @ 10x) | SL: {sl_price} (-0.75%) | TP: {tp_price} (+0.90%)")
                
                # Ensure 10x leverage
                client.set_leverage(sym, 10)
                
                # Place order with broker-side stops
                order_res = client.place_order(
                    category='linear',
                    symbol=sym,
                    side='Buy',
                    order_type='Market',
                    qty=qty,
                    sl=sl_price,
                    tp=tp_price
                )
                
                if order_res.get('retCode') == 0:
                    log(f"✅ Order Placed Successfully! OrderId: {order_res.get('result', {}).get('orderId')}")
                else:
                    log(f"❌ Order Failed: {order_res.get('retMsg')}")
                    
    return True

def main():
    target_info = kb.get_target_state()
    tgt = target_info.get('target_equity', 15.0)
    log("========================================================================")
    log("  SMART MICRO-SCALP GROWTH EXECUTOR (POWERED BY DEEPSEEK PROFIT CLAIMER)")
    log(f"  Dynamic Target: ${tgt:.2f} USDT | Notional/Trade: ${NOTIONAL_PER_TRADE:.2f}")
    log("========================================================================")
    
    while True:
        try:
            keep_running = run_single_cycle()
            if not keep_running:
                break
        except Exception as e:
            log(f"[EXECUTOR ERROR] {e}")
            
        time.sleep(5)

if __name__ == '__main__':
    main()
