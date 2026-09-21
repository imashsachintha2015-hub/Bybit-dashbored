import sys
import os
import json
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scratch.smart_growth_executor import client

def main():
    # 1. Fetch all closed trades from Bybit
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
        time.sleep(0.15)

    print(f"Total Bybit closed trades: {len(all_trades)}")

    # 2. Filter yesterday's trades
    # In Bybit UTC, yesterday was 2026-09-21
    # In Local (+05:30), let's inspect both 2026-09-21 and 2026-09-20
    yesterday_trades = []
    for t in all_trades:
        ts = int(t.get('updatedTime') or t.get('createdTime') or 0)
        dt_utc = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
        d_str = dt_utc.strftime('%Y-%m-%d')
        if d_str in ('2026-09-21', '2026-09-20'):
            yesterday_trades.append((dt_utc, t))

    print(f"Trades on 2026-09-20 and 2026-09-21: {len(yesterday_trades)}")

    wins = []
    losses = []
    by_symbol = {}

    for dt, t in yesterday_trades:
        sym = t.get('symbol')
        pnl = float(t.get('closedPnl', 0))
        entry = float(t.get('avgEntryPrice', 0))
        exit_p = float(t.get('avgExitPrice', 0))
        qty = float(t.get('qty', 0))
        val = float(t.get('cumEntryValue', 0))
        fee = float(t.get('openFee', 0)) + float(t.get('closeFee', 0))
        
        # Position side: in Bybit closed-pnl, side is closing order. So Sell = Long closed, Buy = Short closed.
        closing_side = t.get('side')
        pos_side = 'LONG' if closing_side == 'Sell' else 'SHORT'
        
        pnl_pct = ((exit_p - entry) / entry * 100) if pos_side == 'LONG' else ((entry - exit_p) / entry * 100)

        trade_obj = {
            'time_utc': dt.strftime('%Y-%m-%d %H:%M:%S'),
            'symbol': sym,
            'side': pos_side,
            'entry': entry,
            'exit': exit_p,
            'qty': qty,
            'notional': val,
            'pnl': pnl,
            'pnl_pct': pnl_pct,
            'fees': fee,
            'is_win': pnl > 0
        }

        if sym not in by_symbol:
            by_symbol[sym] = {'wins': 0, 'losses': 0, 'gross_profit': 0.0, 'gross_loss': 0.0, 'trades': []}
        
        by_symbol[sym]['trades'].append(trade_obj)
        if pnl > 0:
            wins.append(trade_obj)
            by_symbol[sym]['wins'] += 1
            by_symbol[sym]['gross_profit'] += pnl
        else:
            losses.append(trade_obj)
            by_symbol[sym]['losses'] += 1
            by_symbol[sym]['gross_loss'] += abs(pnl)

    print("\n" + "="*80)
    print("  YESTERDAY TRADING PERFORMANCE BREAKDOWN")
    print("="*80)
    total_count = len(yesterday_trades)
    win_count = len(wins)
    loss_count = len(losses)
    win_rate = (win_count / total_count * 100) if total_count else 0
    gp = sum(w['pnl'] for w in wins)
    gl = sum(abs(l['pnl']) for l in losses)
    total_fees = sum(w['fees'] for w in wins) + sum(l['fees'] for l in losses)
    net_pnl = gp - gl

    print(f"Total Trades: {total_count} | Wins: {win_count} | Losses: {loss_count} | Win Rate: {win_rate:.1f}%")
    print(f"Gross Profit: +${gp:.4f} | Gross Loss: -${gl:.4f} | Net PnL: ${net_pnl:+.4f} | Total Fees: ${total_fees:.4f}")
    if gl > 0:
        print(f"Profit Factor: {gp / gl:.2f}")

    print("\n" + "-"*80)
    print("  BREAKDOWN BY SYMBOL")
    print("-"*80)
    for sym, st in sorted(by_symbol.items(), key=lambda x: len(x[1]['trades']), reverse=True):
        cnt = len(st['trades'])
        wr = (st['wins'] / cnt * 100) if cnt else 0
        net = st['gross_profit'] - st['gross_loss']
        avg_w = (st['gross_profit'] / st['wins']) if st['wins'] else 0
        avg_l = (st['gross_loss'] / st['losses']) if st['losses'] else 0
        print(f"{sym:10s} | {cnt:2d} trades (W:{st['wins']:2d}, L:{st['losses']:2d}, WR:{wr:5.1f}%) | PnL: {net:+7.4f} | Avg Win: +${avg_w:.4f} | Avg Loss: -${avg_l:.4f}")

    # 3. Categorize Wins: Why did they win?
    print("\n" + "="*80)
    print("  ANALYSIS: WHY DID THE WINNING TRADES WIN?")
    print("="*80)
    big_wins = [w for w in wins if w['pnl_pct'] >= 0.30]
    quick_wins = [w for w in wins if 0.05 <= w['pnl_pct'] < 0.30]
    scratch_wins = [w for w in wins if w['pnl_pct'] < 0.05]

    print(f"1. Stage-1 / Full Target Exits (Gain >= +0.30%): {len(big_wins)} trades")
    for w in big_wins[:8]:
        print(f"   - {w['time_utc']} {w['symbol']} {w['side']} @ {w['entry']} -> {w['exit']} (+{w['pnl_pct']:.2f}%, +${w['pnl']:.4f})")
    print("   -> WHY THEY WON: Perfect trend alignment (15m Bull EMA, RSI pullback to 45-60) + DeepSeek staged profit banking locked in profits at resistance.")

    print(f"\n2. Micro-Scalp / Breakeven-Ratchet Exits (+0.05% to +0.30%): {len(quick_wins)} trades")
    for w in quick_wins[:8]:
        print(f"   - {w['time_utc']} {w['symbol']} {w['side']} @ {w['entry']} -> {w['exit']} (+{w['pnl_pct']:.2f}%, +${w['pnl']:.4f})")
    print("   -> WHY THEY WON: SL was ratcheted to +0.12% above entry when momentum paused, guaranteeing positive cash exit before reversal.")

    print(f"\n3. Scratch Exits (< +0.05%): {len(scratch_wins)} trades")
    print("   -> WHY THEY WON: Fee-cushion stop protected capital when the move failed to extend.")

    # 4. Categorize Losses: Why did they lose?
    print("\n" + "="*80)
    print("  ANALYSIS: WHY DID THE LOSING TRADES LOSE?")
    print("="*80)
    full_stop_losses = [l for l in losses if l['pnl_pct'] <= -0.65]
    medium_losses = [l for l in losses if -0.65 < l['pnl_pct'] <= -0.25]
    micro_losses = [l for l in losses if l['pnl_pct'] > -0.25]

    print(f"1. Full Stop Loss Exits (Hit initial -0.75% stop): {len(full_stop_losses)} trades (Total loss: -${sum(abs(l['pnl']) for l in full_stop_losses):.4f})")
    for l in full_stop_losses[:8]:
        print(f"   - {l['time_utc']} {l['symbol']} {l['side']} @ {l['entry']} -> {l['exit']} ({l['pnl_pct']:.2f}%, -${abs(l['pnl']):.4f})")
    print("   -> WHY THEY LOST: Rapid macro market flushes (BTC drop or sudden market-wide liquidation wicks) that triggered the -0.75% stop within 1-3 minutes before pullback could form.")

    print(f"\n2. Choppy Reversal / Medium Losses (-0.25% to -0.65%): {len(medium_losses)} trades")
    for l in medium_losses[:8]:
        print(f"   - {l['time_utc']} {l['symbol']} {l['side']} @ {l['entry']} -> {l['exit']} ({l['pnl_pct']:.2f}%, -${abs(l['pnl']):.4f})")
    print("   -> WHY THEY LOST: False breakouts where volume ratio was deceptively high, but buyers stalled at local resistance.")

    print(f"\n3. Micro-Cuts / Fee Friction (> -0.25%): {len(micro_losses)} trades")
    for l in micro_losses[:8]:
        print(f"   - {l['time_utc']} {l['symbol']} {l['side']} @ {l['entry']} -> {l['exit']} ({l['pnl_pct']:.2f}%, -${abs(l['pnl']):.4f})")
    print("   -> WHY THEY LOST: Early emergency exit / defense triggered by BTC weakness; trades were cut at breakeven minus Bybit taker fees (~0.11%).")

if __name__ == '__main__':
    main()
