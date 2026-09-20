import os
import sys
import json
import time
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

# Load candidates from scanner
with open('scratch/live_candidates.json') as f:
    candidates = json.load(f)

# Bybit lot step specs
COIN_SPECS = {
    'BTCUSDT': {'qtyStep': 0.001, 'minQty': 0.001},
    'ETHUSDT': {'qtyStep': 0.01, 'minQty': 0.01},
    'SOLUSDT': {'qtyStep': 0.1, 'minQty': 0.1},
    'LINKUSDT': {'qtyStep': 0.1, 'minQty': 0.1},
    'DOGEUSDT': {'qtyStep': 1.0, 'minQty': 1.0},
    'XRPUSDT': {'qtyStep': 1.0, 'minQty': 1.0},
    'AVAXUSDT': {'qtyStep': 0.1, 'minQty': 0.1},
    'NEARUSDT': {'qtyStep': 0.1, 'minQty': 0.1},
    'ADAUSDT': {'qtyStep': 1.0, 'minQty': 1.0},
    'SUIUSDT': {'qtyStep': 1.0, 'minQty': 1.0},
}

# Fetch currently active positions to avoid duplicates
positions_res = client.get_positions()
existing_syms = set()
for p in positions_res.get('result', {}).get('list', []):
    if float(p.get('size', 0)) > 0:
        existing_syms.add(p.get('symbol'))

print(f"Existing active positions on Bybit: {list(existing_syms)}")
print(f"Targeting 10 High-Probability Trades (Calibrated for $10 equity: ~$100 notional, TP +0.60%, SL -0.80%)\n")

executed_trades = []

for c in candidates[:10]:
    sym = c['symbol']
    spec = COIN_SPECS.get(sym, {'qtyStep': 0.1, 'minQty': 0.1})
    step = spec['qtyStep']
    min_q = spec['minQty']
    
    # Calculate qty for $100 notional
    target_notional = 100.0
    entry_p = c['entry']
    raw_qty = target_notional / entry_p
    
    # Round to step
    import math
    qty = max(min_q, round(raw_qty / step) * step)
    qty = round(qty, 4 if step < 0.01 else (2 if step < 1.0 else 0))
    if step >= 1.0:
        qty = int(qty)
        
    actual_notional = qty * entry_p
    tp = c['tp1']
    sl = c['sl']
    
    print(f"--- Placing Trade for {sym} ---")
    print(f"  Setup: {c['setup']} (Score: {c['score']}/100)")
    print(f"  Side: Buy | Qty: {qty} (Notional: ${actual_notional:.2f})")
    print(f"  Entry: {entry_p} | TP (+0.60%): {tp} | SL (-0.80%): {sl}")
    
    # 1. Set leverage to 10x
    try:
        client.set_leverage(sym, 10)
    except Exception as e:
        print(f"  [Leverage notice]: {e}")
        
    # 2. Place market order with TP/SL attached broker-side
    order_res = client.place_order(
        category="linear",
        symbol=sym,
        side="Buy",
        order_type="Market",
        qty=str(qty),
        tp=str(tp),
        sl=str(sl)
    )
    
    if order_res.get('retCode') == 0:
        order_id = order_res.get('result', {}).get('orderId')
        print(f"  [SUCCESS] Live Order Executed! Order ID: {order_id}")
        executed_trades.append({
            'symbol': sym,
            'orderId': order_id,
            'side': 'Buy',
            'qty': qty,
            'entry': entry_p,
            'notional': round(actual_notional, 2),
            'tp': tp,
            'sl': sl,
            'expected_gross_win': round(actual_notional * 0.0060, 2),
            'status': 'FILLED_ACTIVE'
        })
    else:
        print(f"  [REJECTED]: {order_res.get('retMsg')}")
        
    time.sleep(0.3) # Rate limit safety

print(f"\n=== EXECUTION SUMMARY: {len(executed_trades)} TRADES EXECUTED ===")
total_potential_gross = sum(t['expected_gross_win'] for t in executed_trades)
print(f"Total Combined Expected Gross Win: +${total_potential_gross:.2f}")

with open('scratch/executed_10_trades.json', 'w') as f:
    json.dump(executed_trades, f, indent=2)
