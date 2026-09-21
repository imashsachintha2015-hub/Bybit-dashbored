import sys
import os
import json
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scratch.smart_growth_executor import client

def main():
    cursor = ''
    all_trades = []
    for p in range(15):
        params = {'category': 'linear', 'limit': '100'}
        if cursor:
            params['cursor'] = cursor
        res = client.signed_request('GET', '/v5/position/closed-pnl', params)
        data = res.get('result', {})
        items = data.get('list', [])
        if not items:
            break
        all_trades.extend(items)
        cursor = data.get('nextPageCursor')
        if not cursor:
            break
        time.sleep(0.2)

    print("=== TOTAL BYBIT CLOSED TRADES FETCHED ===")
    print("Count:", len(all_trades))

    if not all_trades:
        print("No trades found!")
        return

    timestamps = [int(x.get('updatedTime') or x.get('createdTime') or 0) for x in all_trades]
    min_ts, max_ts = min(timestamps), max(timestamps)
    print("Oldest:", datetime.fromtimestamp(min_ts/1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"))
    print("Newest:", datetime.fromtimestamp(max_ts/1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"))

    # Group by UTC day and Local day (Asia/Colombo +05:30)
    days_utc = {}
    days_local = {}
    
    for x in all_trades:
        ts = int(x.get('updatedTime') or x.get('createdTime') or 0)
        dt_utc = datetime.fromtimestamp(ts/1000, tz=timezone.utc)
        # user local is +05:30
        dt_local = datetime.fromtimestamp(ts/1000)
        
        d_utc = dt_utc.strftime('%Y-%m-%d')
        d_loc = dt_local.strftime('%Y-%m-%d')
        pnl = float(x.get('closedPnl', 0))

        for d_key, d_dict in [(d_utc, days_utc), (d_loc, days_local)]:
            if d_key not in d_dict:
                d_dict[d_key] = {'wins': 0, 'losses': 0, 'gross_profit': 0.0, 'gross_loss': 0.0, 'trades': []}
            if pnl > 0:
                d_dict[d_key]['wins'] += 1
                d_dict[d_key]['gross_profit'] += pnl
            elif pnl < 0:
                d_dict[d_key]['losses'] += 1
                d_dict[d_key]['gross_loss'] += abs(pnl)
            d_dict[d_key]['trades'].append(x)

    print("\n=== UTC DAY-WISE BREAKDOWN ===")
    for d in sorted(days_utc.keys()):
        st = days_utc[d]
        tot = st['wins'] + st['losses']
        wr = (st['wins'] / tot * 100) if tot else 0
        net = st['gross_profit'] - st['gross_loss']
        gp = st['gross_profit']
        gl = st['gross_loss']
        print(f"{d}: Trades={tot:3d} (W={st['wins']:2d}, L={st['losses']:2d}, WR={wr:5.1f}%) | Gross Profit=+${gp:6.2f} | Gross Loss=-${gl:6.2f} | Net PnL={net:+7.2f}")

    print("\n=== LOCAL DAY-WISE BREAKDOWN (User Local Time) ===")
    for d in sorted(days_local.keys()):
        st = days_local[d]
        tot = st['wins'] + st['losses']
        wr = (st['wins'] / tot * 100) if tot else 0
        net = st['gross_profit'] - st['gross_loss']
        gp = st['gross_profit']
        gl = st['gross_loss']
        print(f"{d}: Trades={tot:3d} (W={st['wins']:2d}, L={st['losses']:2d}, WR={wr:5.1f}%) | Gross Profit=+${gp:6.2f} | Gross Loss=-${gl:6.2f} | Net PnL={net:+7.2f}")

    # Total all-time
    total_w = sum(1 for x in all_trades if float(x.get('closedPnl', 0)) > 0)
    total_l = sum(1 for x in all_trades if float(x.get('closedPnl', 0)) < 0)
    total_gp = sum(float(x.get('closedPnl', 0)) for x in all_trades if float(x.get('closedPnl', 0)) > 0)
    total_gl = sum(abs(float(x.get('closedPnl', 0))) for x in all_trades if float(x.get('closedPnl', 0)) < 0)
    print(f"\n=== ALL-TIME OVERALL TOTAL ===")
    print(f"Total Trades: {len(all_trades)} (Wins: {total_w}, Losses: {total_l}, Win Rate: {total_w/(total_w+total_l)*100:.1f}%)")
    print(f"Gross Profit: ${total_gp:.2f}")
    print(f"Gross Loss:   ${total_gl:.2f}")
    print(f"Net PnL:      ${total_gp - total_gl:.2f}")

if __name__ == '__main__':
    main()
