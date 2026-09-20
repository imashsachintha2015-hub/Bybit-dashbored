import os
import sys
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

print("=== 1. CLOSING ALL OPEN POSITIONS & CANCELLING RESTING ORDERS ===")

# Cancel all open orders across category linear
orders_res = client.signed_request("GET", "/v5/order/realtime", {"category": "linear"})
open_orders = orders_res.get("result", {}).get("list", [])
print(f"Found {len(open_orders)} open resting orders to cancel.")
for o in open_orders:
    sym = o.get("symbol")
    oid = o.get("orderId")
    c_res = client.cancel_order("linear", sym, oid)
    print(f"  Cancelled order {oid} on {sym}: {c_res.get('retMsg')}")

# Get all open positions
pos_res = client.get_positions()
positions = [p for p in pos_res.get("result", {}).get("list", []) if float(p.get("size", 0)) > 0]
print(f"\nFound {len(positions)} active positions to close.")

for p in positions:
    sym = p.get("symbol")
    side = p.get("side") # 'Buy' or 'Sell'
    size = p.get("size")
    print(f"  Closing {sym} ({side} size={size})...")
    close_res = client.close_position("linear", sym, side, size)
    print(f"  Result: {close_res.get('retMsg')} (code: {close_res.get('retCode')})")
    time.sleep(0.2)

time.sleep(1.0)

# Check positions again
pos_check = client.get_positions()
remaining = [p for p in pos_check.get("result", {}).get("list", []) if float(p.get("size", 0)) > 0]
print(f"\nRemaining open positions after closing: {len(remaining)}")

print("\n=== 2. FETCHING CURRENT WALLET BALANCE ===")
wallet = client.get_wallet_balance()
coins = wallet.get("result", {}).get("list", [{}])[0].get("coin", [])
usdt_balance = 0.0
for c in coins:
    if c.get("coin") == "USDT":
        usdt_balance = float(c.get("walletBalance", c.get("equity", 0)))
        print(f"Current USDT Balance: ${usdt_balance:.4f} (Equity: ${float(c.get('equity', 0)):.4f})")

print("\n=== 3. REDUCING EQUITY TO EXACTLY $10.00 USDT ===")
target_equity = 10.0
if usdt_balance > target_equity:
    diff = usdt_balance - target_equity
    # Round down to 2 decimals to ensure we don't reduce past 10
    amount_to_reduce = round(diff, 2)
    print(f"Reducing USDT balance by ${amount_to_reduce:.2f} to reach target $10.00...")
    reduce_res = client.apply_demo_funds("USDT", amount_to_reduce, reduce=True)
    print(f"Reduce API Result: {reduce_res}")
elif usdt_balance < target_equity:
    diff = target_equity - usdt_balance
    amount_to_add = round(diff, 2)
    print(f"Current balance is below $10. Adding ${amount_to_add:.2f} to reach $10.00...")
    add_res = client.apply_demo_funds("USDT", amount_to_add, reduce=False)
    print(f"Add API Result: {add_res}")
else:
    print("Balance is already exactly $10.00!")

time.sleep(1.0)

print("\n=== 4. VERIFYING FINAL WALLET BALANCE & ACTIVE POSITIONS ===")
wallet_after = client.get_wallet_balance()
coins_after = wallet_after.get("result", {}).get("list", [{}])[0].get("coin", [])
for c in coins_after:
    if c.get("coin") == "USDT":
        print(f"FINAL USDT Balance: ${float(c.get('walletBalance', 0)):.4f} | Equity: ${float(c.get('equity', 0)):.4f} | Available: {c.get('availableToWithdraw')}")

final_pos = client.get_positions()
final_active = [p for p in final_pos.get("result", {}).get("list", []) if float(p.get("size", 0)) > 0]
print(f"FINAL ACTIVE POSITIONS COUNT: {len(final_active)}")
