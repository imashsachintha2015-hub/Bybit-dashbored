import os
import sys
import argparse

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

def main():
    parser = argparse.ArgumentParser(description="Expert Trader Execution Helper")
    parser.add_argument("--action", required=True, choices=["order", "stop", "close", "status"])
    parser.add_argument("--symbol", default="SOLUSDT")
    parser.add_argument("--side", choices=["Buy", "Sell"], default="Buy")
    parser.add_argument("--qty", type=float)
    parser.add_argument("--price", type=float)
    parser.add_argument("--sl", type=float)
    parser.add_argument("--tp", type=float)
    parser.add_argument("--leverage", type=int, default=10)

    args = parser.parse_args()

    if args.action == "order":
        if not args.qty:
            print("[ERROR] --qty is required for placing an order.")
            return
        client.set_leverage(args.symbol, args.leverage)
        res = client.place_order(
            category="linear",
            symbol=args.symbol,
            side=args.side,
            order_type="Market",
            qty=str(args.qty),
            tp=str(args.tp) if args.tp else None,
            sl=str(args.sl) if args.sl else None
        )
        print(f"[ORDER RESULT] {res}")

    elif args.action == "stop":
        res = client.set_trading_stop(
            category="linear",
            symbol=args.symbol,
            stop_loss=str(args.sl) if args.sl else None,
            take_profit=str(args.tp) if args.tp else None
        )
        print(f"[STOP ADJUST RESULT] {res}")

    elif args.action == "close":
        pos_res = client.get_positions(symbol=args.symbol)
        active = [p for p in pos_res.get("result", {}).get("list", []) if float(p.get("size", 0)) > 0]
        if not active:
            print(f"[INFO] No active position on {args.symbol} to close.")
            return
        p = active[0]
        size = p.get("size")
        side = p.get("side")
        res = client.close_position(category="linear", symbol=args.symbol, side=side, qty=size)
        print(f"[CLOSE RESULT] {res}")

    elif args.action == "status":
        pos_res = client.get_positions()
        active = [p for p in pos_res.get("result", {}).get("list", []) if float(p.get("size", 0)) > 0]
        print(f"Active positions count: {len(active)}")
        for p in active:
            print(f"  {p['symbol']} {p['side']} Qty:{p['size']} Entry:{p['avgPrice']} Mark:{p['markPrice']} UnPnl:${p['unrealisedPnl']} SL:{p.get('stopLoss')} TP:{p.get('takeProfit')}")

if __name__ == '__main__':
    main()
