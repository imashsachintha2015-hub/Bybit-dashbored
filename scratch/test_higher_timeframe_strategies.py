import urllib.request
import json
import ssl
import time
import math

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

SYMBOLS = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'XRPUSDT', 'DOGEUSDT']
MAKER_COST_BPS = 3.5

def fetch_klines(symbol, interval, limit=1000):
    url = f"https://api.bybit.com/v5/market/kline?category=linear&symbol={symbol}&interval={interval}&limit={limit}"
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, context=ctx, timeout=15) as resp:
        data = json.loads(resp.read().decode())
        raw = data.get('result', {}).get('list', [])
        candles = []
        for r in reversed(raw):
            candles.append({
                'start': int(r[0]),
                'open': float(r[1]),
                'high': float(r[2]),
                'low': float(r[3]),
                'close': float(r[4]),
                'volume': float(r[5])
            })
        return candles

def calc_ema(values, period):
    if len(values) < period:
        return [values[-1]] * len(values) if values else []
    k = 2.0 / (period + 1)
    out = [values[0]]
    for i in range(1, len(values)):
        out.append(values[i] * k + out[-1] * (1 - k))
    return out

def calc_rsi(closes, period=14):
    if len(closes) < period + 1:
        return [50.0] * len(closes)
    gains, losses = [], []
    for i in range(1, period + 1):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0.0))
        losses.append(max(-diff, 0.0))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    out = [50.0] * period
    for i in range(period + 1, len(closes)):
        diff = closes[i] - closes[i - 1]
        g = max(diff, 0.0)
        l = max(-diff, 0.0)
        avg_gain = (avg_gain * (period - 1) + g) / period
        avg_loss = (avg_loss * (period - 1) + l) / period
        if avg_loss == 0:
            out.append(100.0)
        else:
            rs = avg_gain / avg_loss
            out.append(100.0 - (100.0 / (1.0 + rs)))
    while len(out) < len(closes):
        out.insert(0, 50.0)
    return out

def calc_atr(candles, period=14):
    trs = [candles[0]['high'] - candles[0]['low']]
    for i in range(1, len(candles)):
        c, p = candles[i], candles[i - 1]
        tr = max(c['high'] - c['low'], abs(c['high'] - p['close']), abs(c['low'] - p['close']))
        trs.append(tr)
    atr = [trs[0]]
    k = 1.0 / period
    for i in range(1, len(trs)):
        atr.append(atr[-1] * (1 - k) + trs[i] * k)
    return atr

def simulate_swing_trade(candles, entry_idx, side, entry_price, initial_stop, tp1_price, tp2_price,
                          tp1_fraction=0.60, max_bars=24, early_be_r=0.8, notional=100.0):
    cost_rate = MAKER_COST_BPS / 10000.0
    risk_dist = abs(entry_price - initial_stop)
    if risk_dist <= 0:
        return None

    current_stop = initial_stop
    tp1_filled = False
    stop_at_be = False
    banked_pnl = 0.0
    remaining_fraction = 1.0

    for k in range(entry_idx + 1, min(entry_idx + 1 + max_bars, len(candles))):
        c = candles[k]
        bars_held = k - entry_idx

        # Milestone BE when trade reaches early_be_r * risk_dist
        if not stop_at_be:
            if side == 'LONG' and c['high'] >= entry_price + risk_dist * early_be_r:
                current_stop = entry_price
                stop_at_be = True
            elif side == 'SHORT' and c['low'] <= entry_price - risk_dist * early_be_r:
                current_stop = entry_price
                stop_at_be = True

        # Stop check
        hit_stop = (c['low'] <= current_stop) if side == 'LONG' else (c['high'] >= current_stop)
        if hit_stop:
            exit_price = current_stop
            move = (exit_price - entry_price) / entry_price if side == 'LONG' else (entry_price - exit_price) / entry_price
            slice_pnl = remaining_fraction * notional * (move - cost_rate)
            total = banked_pnl + slice_pnl
            outcome = 'WIN' if total > 0.05 else ('BE' if total >= -0.05 else 'LOSS')
            return {'pnl': total, 'outcome': outcome, 'win': total > 0, 'bars': bars_held, 'r': total / (notional * (risk_dist / entry_price))}

        # TP1 check
        if not tp1_filled:
            hit_tp1 = (c['high'] >= tp1_price) if side == 'LONG' else (c['low'] <= tp1_price)
            if hit_tp1:
                tp1_filled = True
                move = (tp1_price - entry_price) / entry_price if side == 'LONG' else (entry_price - tp1_price) / entry_price
                slice_pnl = tp1_fraction * notional * (move - cost_rate)
                banked_pnl += slice_pnl
                remaining_fraction -= tp1_fraction
                current_stop = entry_price
                stop_at_be = True

        # TP2 check
        if tp1_filled and tp2_price is not None:
            hit_tp2 = (c['high'] >= tp2_price) if side == 'LONG' else (c['low'] <= tp2_price)
            if hit_tp2:
                move = (tp2_price - entry_price) / entry_price if side == 'LONG' else (entry_price - tp2_price) / entry_price
                slice_pnl = remaining_fraction * notional * (move - cost_rate)
                total = banked_pnl + slice_pnl
                return {'pnl': total, 'outcome': 'WIN', 'win': True, 'bars': bars_held, 'r': total / (notional * (risk_dist / entry_price))}

    # Timeout
    exit_c = candles[min(entry_idx + max_bars, len(candles) - 1)]
    exit_price = exit_c['close']
    move = (exit_price - entry_price) / entry_price if side == 'LONG' else (entry_price - exit_price) / entry_price
    slice_pnl = remaining_fraction * notional * (move - cost_rate)
    total = banked_pnl + slice_pnl
    outcome = 'WIN' if total > 0.05 else ('BE' if total >= -0.05 else 'LOSS')
    return {'pnl': total, 'outcome': outcome, 'win': total > 0, 'bars': max_bars, 'r': total / (notional * (risk_dist / entry_price))}

# ─────────────────────────────────────────────────────────────────────────────
# 15M STRATEGY A: 15M TREND PULLBACK (MASIS Core Playbook)
# EMA9 > EMA21 > EMA50, pulls back to 21 EMA, confirms bounce
# ─────────────────────────────────────────────────────────────────────────────
def strat_15m_trend_pullback(candles_15m, candles_60m):
    closes_15m = [c['close'] for c in candles_15m]
    closes_60m = [c['close'] for c in candles_60m]
    ema9 = calc_ema(closes_15m, 9)
    ema21 = calc_ema(closes_15m, 21)
    ema50 = calc_ema(closes_15m, 50)
    ema21_60m = calc_ema(closes_60m, 21)
    atr = calc_atr(candles_15m, 14)

    trades = []
    cooldown = 0

    for i in range(50, len(candles_15m) - 24):
        if cooldown > 0:
            cooldown -= 1
            continue

        c = candles_15m[i]
        p = candles_15m[i - 1]
        a = atr[i]
        idx_60m = min(int(i / 4), len(candles_60m) - 1)
        htf_bull = closes_60m[idx_60m] > ema21_60m[idx_60m]
        htf_bear = closes_60m[idx_60m] < ema21_60m[idx_60m]

        side = None
        if htf_bull and (ema9[i] > ema21[i] > ema50[i]) and (p['low'] <= ema21[i] * 1.003) and (c['close'] > ema9[i]):
            side = 'LONG'
        elif htf_bear and (ema9[i] < ema21[i] < ema50[i]) and (p['high'] >= ema21[i] * 0.997) and (c['close'] < ema9[i]):
            side = 'SHORT'

        if not side:
            continue

        entry = c['close']
        stop = ema50[i] - a * 0.5 if side == 'LONG' else ema50[i] + a * 0.5
        risk = abs(entry - stop)
        tp1 = entry + risk * 1.5 if side == 'LONG' else entry - risk * 1.5
        tp2 = entry + risk * 2.5 if side == 'LONG' else entry - risk * 2.5

        res = simulate_swing_trade(candles_15m, i, side, entry, stop, tp1, tp2, tp1_fraction=0.60, max_bars=24, early_be_r=0.8)
        if res:
            trades.append(res)
            cooldown = 6
    return trades

# ─────────────────────────────────────────────────────────────────────────────
# 15M STRATEGY B: 15M LIQUIDITY SWEEP & RECLAIM (MASIS Sweep Playbook)
# ─────────────────────────────────────────────────────────────────────────────
def strat_15m_sweep_reclaim(candles_15m, candles_60m):
    closes_15m = [c['close'] for c in candles_15m]
    atr = calc_atr(candles_15m, 14)
    trades = []
    cooldown = 0

    for i in range(30, len(candles_15m) - 24):
        if cooldown > 0:
            cooldown -= 1
            continue

        c = candles_15m[i]
        a = atr[i]
        prior = candles_15m[i - 20 : i]
        prior_hi = max(x['high'] for x in prior)
        prior_lo = min(x['low'] for x in prior)

        rng = max(c['high'] - c['low'], a * 0.2)
        lower_wick = min(c['open'], c['close']) - c['low']
        upper_wick = c['high'] - max(c['open'], c['close'])

        side = None
        if c['low'] < prior_lo and c['close'] > prior_lo and (lower_wick / rng >= 0.40):
            side = 'LONG'
        elif c['high'] > prior_hi and c['close'] < prior_hi and (upper_wick / rng >= 0.40):
            side = 'SHORT'

        if not side:
            continue

        entry = c['close']
        stop = c['low'] - a * 0.4 if side == 'LONG' else c['high'] + a * 0.4
        risk = abs(entry - stop)
        tp1 = entry + risk * 1.5 if side == 'LONG' else entry - risk * 1.5
        tp2 = entry + risk * 2.5 if side == 'LONG' else entry - risk * 2.5

        res = simulate_swing_trade(candles_15m, i, side, entry, stop, tp1, tp2, tp1_fraction=0.60, max_bars=24, early_be_r=0.8)
        if res:
            trades.append(res)
            cooldown = 6
    return trades

# ─────────────────────────────────────────────────────────────────────────────
# 15M STRATEGY C: 15M SQUEEZE BREAKOUT RETEST (MASIS Squeeze Playbook)
# ─────────────────────────────────────────────────────────────────────────────
def strat_15m_squeeze_retest(candles_15m, candles_60m):
    closes = [c['close'] for c in candles_15m]
    vols = [c['volume'] for c in candles_15m]
    atr = calc_atr(candles_15m, 14)
    trades = []
    cooldown = 0

    for i in range(25, len(candles_15m) - 24):
        if cooldown > 0:
            cooldown -= 1
            continue

        c = candles_15m[i]
        a = atr[i]

        # Squeeze in previous 6 bars (1.5 hours range < 0.60%)
        box = candles_15m[i - 6 : i]
        box_hi = max(x['high'] for x in box)
        box_lo = min(x['low'] for x in box)
        box_w = (box_hi - box_lo) / box_lo

        if box_w > 0.0060:
            continue

        avg_vol = sum(vols[i - 15 : i]) / 15.0
        vol_surge = c['volume'] >= 1.6 * avg_vol

        side = None
        if vol_surge and c['close'] > box_hi:
            side = 'LONG'
        elif vol_surge and c['close'] < box_lo:
            side = 'SHORT'

        if not side:
            continue

        entry = c['close']
        stop = box_lo - a * 0.3 if side == 'LONG' else box_hi + a * 0.3
        risk = abs(entry - stop)
        tp1 = entry + risk * 1.5 if side == 'LONG' else entry - risk * 1.5
        tp2 = entry + risk * 3.0 if side == 'LONG' else entry - risk * 3.0

        res = simulate_swing_trade(candles_15m, i, side, entry, stop, tp1, tp2, tp1_fraction=0.60, max_bars=24, early_be_r=0.8)
        if res:
            trades.append(res)
            cooldown = 6
    return trades

def main():
    print("=" * 90)
    print("  TESTING HIGHER TIMEFRAME SWING PLAYBOOKS (15M / 1H BYBIT TAPES)")
    print("  Model: 1.5R - 2.5R structural targets | Fee diluted to <4% of gain")
    print("=" * 90)

    dataset = {}
    print("\nFetching 15m and 1h klines across 5 liquid coins...")
    for sym in SYMBOLS:
        c15m = fetch_klines(sym, '15', limit=1000)
        c60m = fetch_klines(sym, '60', limit=500)
        dataset[sym] = {'15m': c15m, '60m': c60m}
        time.sleep(0.12)
    print("Data download completed.\n")

    strategies = {
        '1. 15m Trend Pullback (1.5R/2.5R Targets)': strat_15m_trend_pullback,
        '2. 15m Liquidity Sweep Reclaim (1.5R/2.5R)': strat_15m_sweep_reclaim,
        '3. 15m Squeeze Breakout Retest (1.5R/3.0R)': strat_15m_squeeze_retest
    }

    results = {}
    for name, strat_func in strategies.items():
        all_trades = []
        for sym in SYMBOLS:
            c15m = dataset[sym]['15m']
            c60m = dataset[sym]['60m']
            t = strat_func(c15m, c60m)
            all_trades.extend(t)

        n = len(all_trades)
        if n == 0:
            continue

        wins = sum(1 for x in all_trades if x['outcome'] == 'WIN')
        be = sum(1 for x in all_trades if x['outcome'] == 'BE')
        losses = sum(1 for x in all_trades if x['outcome'] == 'LOSS')
        win_rate = (wins / n) * 100.0
        no_loss_rate = ((wins + be) / n) * 100.0

        total_net_pnl = sum(x['pnl'] for x in all_trades)
        gross_profit = sum(x['pnl'] for x in all_trades if x['pnl'] > 0)
        gross_loss = abs(sum(x['pnl'] for x in all_trades if x['pnl'] < 0))
        pf = (gross_profit / gross_loss) if gross_loss > 0 else (99.9 if gross_profit > 0 else 0)

        total_r = sum(x.get('r', 0) for x in all_trades)
        pnl_per_trade = total_net_pnl / n
        proj_100_pnl = pnl_per_trade * 100.0
        avg_bars = sum(x['bars'] for x in all_trades) / n
        avg_hold_mins = avg_bars * 15.0

        results[name] = {
            'trades': n,
            'wins': wins,
            'be': be,
            'losses': losses,
            'win_rate': win_rate,
            'no_loss_rate': no_loss_rate,
            'profit_factor': pf,
            'total_net_pnl': total_net_pnl,
            'total_r': total_r,
            'proj_100_pnl': proj_100_pnl,
            'avg_hold_mins': avg_hold_mins
        }

    header = f"{'15m Swing Strategy':<44} | {'Trades':<6} | {'Win %':<6} | {'NoLoss%':<7} | {'PF':<5} | {'Total $':<8} | {'Total R':<8} | {'Hold':<6}"
    print(header)
    print("-" * len(header))

    for name, r in results.items():
        print(f"{name:<44} | {r['trades']:<6} | {r['win_rate']:<5.1f}% | {r['no_loss_rate']:<6.1f}% | {r['profit_factor']:<5.2f} | ${r['total_net_pnl']:<7.2f} | {r['total_r']:<+6.1f}R  | {r['avg_hold_mins']:<4.0f}m")

    with open('scratch/htf_swing_results.json', 'w', encoding='utf-8') as f:
        json.dump(results, f, indent=2)

if __name__ == '__main__':
    main()
