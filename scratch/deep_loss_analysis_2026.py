import os
import sys
import json
import time
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

def fetch_klines(symbol, interval, start_time, end_time):
    # start_time, end_time in ms
    url = f"https://api-demo.bybit.com/v5/market/kline?category=linear&symbol={symbol}&interval={interval}&start={start_time}&end={end_time}&limit=200"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "MASIS/3.0"})
        with urllib.request.urlopen(req, timeout=5) as r:
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
                    "vol": float(row[5])
                })
            return candles
    except Exception as e:
        return []

# 1. Wallet Balance
wb = client.get_wallet_balance()
coins = wb.get('result', {}).get('list', [{}])[0].get('coin', [])
usdt = next((c for c in coins if c.get('coin') == 'USDT'), {})
equity = float(usdt.get('equity', 0))
wallet_bal = float(usdt.get('walletBalance', 0))
print(f"=== CURRENT BYBIT ACCOUNT STATUS ===")
print(f"USDT Equity: ${equity:.4f} | Wallet Balance: ${wallet_bal:.4f}")

# 2. Active Positions
pos = client.get_positions()
act = [p for p in pos.get('result', {}).get('list', []) if float(p.get('size', 0)) > 0]
print(f"Active Positions: {len(act)}")
for p in act:
    print(f"  {p['symbol']} {p['side']} size={p['size']} entry={p['avgPrice']} mark={p['markPrice']} unpnl={p['unrealisedPnl']} sl={p.get('stopLoss')} tp={p.get('takeProfit')}")

# 3. Closed Trades Analysis
closed_res = client.get_closed_pnl(limit=100)
records = closed_res.get('result', {}).get('list', []) or []
print(f"\nTotal Closed Trade Records fetched: {len(records)}")

trades = []
for r in records:
    sym = r.get('symbol')
    side = r.get('side') # Closing side
    # If closing side is Sell, original position was BUY (LONG). If Buy, original was SELL (SHORT)
    pos_side = "LONG" if side.lower() == "sell" else "SHORT"
    qty = float(r.get('qty', 0))
    entry = float(r.get('avgEntryPrice', 0))
    exit_p = float(r.get('avgExitPrice', 0))
    pnl = float(r.get('closedPnl', 0))
    open_fee = float(r.get('openFee', 0))
    close_fee = float(r.get('closeFee', 0))
    tot_fee = open_fee + close_fee
    created_ts = int(r.get('createdTime', 0))
    updated_ts = int(r.get('updatedTime', 0))
    order_id = r.get('orderId')
    exec_type = r.get('execType')
    
    # Calculate price change
    pct_change = ((exit_p - entry) / entry) * 100.0 if entry > 0 else 0.0
    if pos_side == "SHORT":
        pct_change = -pct_change
        
    trades.append({
        "order_id": order_id,
        "symbol": sym,
        "pos_side": pos_side,
        "qty": qty,
        "notional": round(qty * entry, 2),
        "entry": entry,
        "exit": exit_p,
        "pct_change": round(pct_change, 2),
        "pnl": pnl,
        "fee": tot_fee,
        "gross_pnl": round(pnl + tot_fee, 4),
        "created_ts": created_ts,
        "updated_ts": updated_ts,
        "created_str": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(created_ts / 1000)) if created_ts else "--",
        "updated_str": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(updated_ts / 1000)) if updated_ts else "--",
        "exec_type": exec_type
    })

# Sort newest first
trades.sort(key=lambda x: x['updated_ts'], reverse=True)

# Separate wins, losses, breakevens
wins = [t for t in trades if t['pnl'] > 0]
losses = [t for t in trades if t['pnl'] < 0]
breakevens = [t for t in trades if t['pnl'] == 0]

print(f"\n=== OVERALL PERFORMANCE SUMMARY (Last {len(trades)} Trades) ===")
print(f"Wins: {len(wins)} | Losses: {len(losses)} | Breakeven: {len(breakevens)}")
if trades:
    win_rate = (len(wins) / len(trades)) * 100
    print(f"Win Rate: {win_rate:.1f}%")
total_pnl = sum(t['pnl'] for t in trades)
gross_wins = sum(t['pnl'] for t in wins)
gross_losses = sum(t['pnl'] for t in losses)
total_fees = sum(t['fee'] for t in trades)
print(f"Gross Profit: +${gross_wins:.4f}")
print(f"Gross Loss:   -${abs(gross_losses):.4f}")
print(f"Total Fees:    ${total_fees:.4f}")
print(f"Net Realized: ${total_pnl:+.4f}")

# Detail of the 20 most recent trades (Sept 18 - Sept 20)
print(f"\n=== MOST RECENT 20 TRADES DETAIL ===")
print(f"{'Exit Time':19} | {'Symbol':9} | {'Side':5} | {'Qty':7} | {'Notional':9} | {'Entry':8} -> {'Exit':8} | {'Move %':7} | {'Net PnL':9} | {'Fees':7}")
print("-" * 105)
for t in trades[:20]:
    print(f"{t['updated_str']} | {t['symbol']:9} | {t['pos_side']:5} | {t['qty']:<7} | ${t['notional']:<8.2f} | {t['entry']:<8.4f} -> {t['exit']:<8.4f} | {t['pct_change']:+6.2f}% | ${t['pnl']:+8.4f} | ${t['fee']:<6.4f}")

# Deep dive into the loss trades: analyze MFE (Maximum Favorable Excursion)
# Did the price turn BEFORE reaching TP?
print(f"\n=== DEEP DIVE: DID PRICE TURN BEFORE REACHING TP (MFE ANALYSIS)? ===")
turn_before_tp_count = 0
tight_sl_count = 0
immediate_adverse_count = 0

for t in losses[:15]: # Analyze last 15 losses
    # Fetch 1-min candles between created_ts and updated_ts
    start_t = max(0, t['created_ts'] - 60000)
    end_t = t['updated_ts'] + 60000
    duration_min = round((t['updated_ts'] - t['created_ts']) / 60000.0, 1)
    
    klines = fetch_klines(t['symbol'], '1', start_t, end_t)
    if klines:
        highs = [c['high'] for c in klines]
        lows = [c['low'] for c in klines]
        max_high = max(highs)
        min_low = min(lows)
        
        entry = t['entry']
        if t['pos_side'] == "LONG":
            max_fav_pct = ((max_high - entry) / entry) * 100.0
            max_adv_pct = ((min_low - entry) / entry) * 100.0
        else:
            max_fav_pct = ((entry - min_low) / entry) * 100.0
            max_adv_pct = ((entry - max_high) / entry) * 100.0
            
        print(f"[{t['updated_str']}] {t['symbol']} {t['pos_side']} (Dur: {duration_min}m) | Entry: {entry} -> Exit: {t['exit']} (PnL: ${t['pnl']:+.4f})")
        print(f"   Max Favorable (MFE): +{max_fav_pct:.2f}% | Max Adverse (MAE): {max_adv_pct:.2f}%")
        
        if max_fav_pct >= 0.30:
            turn_before_tp_count += 1
            print(f"   --> DIAGNOSIS: TURNED BEFORE TP! Trade reached +{max_fav_pct:.2f}% in green, but reversed into SL!")
        elif abs(max_adv_pct) <= 0.60 and max_fav_pct < 0.15:
            tight_sl_count += 1
            print(f"   --> DIAGNOSIS: TIGHT SL WHIPSAW / NO FOLLOW-THROUGH (Chopped immediately)")
        else:
            immediate_adverse_count += 1
            print(f"   --> DIAGNOSIS: WRONG DIRECTIONAL TIMING / MOMENTUM FAILURE")
    else:
        print(f"[{t['updated_str']}] {t['symbol']} {t['pos_side']} | Entry: {t['entry']} -> Exit: {t['exit']} | PnL: ${t['pnl']:+.4f} (No klines available)")

print(f"\nLoss Classification Summary (out of sampled {min(15, len(losses))} losses):")
print(f"  - Turned before reaching TP (went green +0.3% to +0.8%, then reversed to loss): {turn_before_tp_count}")
print(f"  - Chopped in tight noise / tight stop whipsaw: {tight_sl_count}")
print(f"  - Immediate adverse / wrong entry timing: {immediate_adverse_count}")
