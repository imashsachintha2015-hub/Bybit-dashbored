import urllib.request
import json
import ssl
import time
import math

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

SYMBOLS = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'XRPUSDT', 'DOGEUSDT']
MAKER_COST_BPS = 3.5  # 0.035% round trip

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

def calc_er(closes, period=20):
    """Kaufman Efficiency Ratio (net travel / total travel)"""
    out = [0.0] * len(closes)
    for i in range(period, len(closes)):
        net = abs(closes[i] - closes[i - period])
        path = sum(abs(closes[k] - closes[k - 1]) for k in range(i - period + 1, i + 1))
        out[i] = (net / path) if path > 0 else 0.0
    return out

# ─────────────────────────────────────────────────────────────────────────────
# SIMULATOR: Realistic Trade Engine
# ─────────────────────────────────────────────────────────────────────────────
def simulate_trade(candles, entry_idx, side, entry_price, initial_stop, tp1_price, tp2_price,
                   tp1_fraction=0.90, max_bars=8, early_be_pct=0.0015, cost_bps=MAKER_COST_BPS, notional=100.0):
    cost_rate = cost_bps / 10000.0
    current_stop = initial_stop
    tp1_filled = False
    stop_at_be = False
    banked_pnl = 0.0
    remaining_fraction = 1.0

    for k in range(entry_idx + 1, min(entry_idx + 1 + max_bars, len(candles))):
        c = candles[k]
        bars_held = k - entry_idx

        # Early BE ratchet
        if not stop_at_be:
            if side == 'LONG' and c['high'] >= entry_price * (1.0 + early_be_pct):
                current_stop = entry_price
                stop_at_be = True
            elif side == 'SHORT' and c['low'] <= entry_price * (1.0 - early_be_pct):
                current_stop = entry_price
                stop_at_be = True

        # Stop check
        hit_stop = (c['low'] <= current_stop) if side == 'LONG' else (c['high'] >= current_stop)
        if hit_stop:
            exit_price = current_stop
            price_move = (exit_price - entry_price) / entry_price if side == 'LONG' else (entry_price - exit_price) / entry_price
            slice_pnl = remaining_fraction * notional * (price_move - cost_rate)
            total_pnl = banked_pnl + slice_pnl
            outcome = 'WIN' if total_pnl > 0.02 else ('BE' if total_pnl >= -0.05 else 'LOSS')
            return {'pnl': total_pnl, 'outcome': outcome, 'win': total_pnl > 0, 'bars': bars_held, 'reason': 'STOP'}

        # TP1 check
        if not tp1_filled:
            hit_tp1 = (c['high'] >= tp1_price) if side == 'LONG' else (c['low'] <= tp1_price)
            if hit_tp1:
                tp1_filled = True
                price_move = (tp1_price - entry_price) / entry_price if side == 'LONG' else (entry_price - tp1_price) / entry_price
                slice_pnl = tp1_fraction * notional * (price_move - cost_rate)
                banked_pnl += slice_pnl
                remaining_fraction -= tp1_fraction
                current_stop = entry_price
                stop_at_be = True

                if remaining_fraction <= 0.01:
                    return {'pnl': banked_pnl, 'outcome': 'WIN', 'win': True, 'bars': bars_held, 'reason': 'TP1_FULL'}

        # TP2 check
        if tp1_filled and tp2_price is not None:
            hit_tp2 = (c['high'] >= tp2_price) if side == 'LONG' else (c['low'] <= tp2_price)
            if hit_tp2:
                price_move = (tp2_price - entry_price) / entry_price if side == 'LONG' else (entry_price - tp2_price) / entry_price
                slice_pnl = remaining_fraction * notional * (price_move - cost_rate)
                total_pnl = banked_pnl + slice_pnl
                return {'pnl': total_pnl, 'outcome': 'WIN', 'win': True, 'bars': bars_held, 'reason': 'TP2_RUNNER'}

    # Timeout
    exit_c = candles[min(entry_idx + max_bars, len(candles) - 1)]
    exit_price = exit_c['close']
    price_move = (exit_price - entry_price) / entry_price if side == 'LONG' else (entry_price - exit_price) / entry_price
    slice_pnl = remaining_fraction * notional * (price_move - cost_rate)
    total_pnl = banked_pnl + slice_pnl
    outcome = 'WIN' if total_pnl > 0.02 else ('BE' if total_pnl >= -0.05 else 'LOSS')
    return {'pnl': total_pnl, 'outcome': outcome, 'win': total_pnl > 0, 'bars': max_bars, 'reason': 'TIMEOUT'}

# ─────────────────────────────────────────────────────────────────────────────
# MODEL 1: 🎯 MICRO-TARGET QUICK HARVEST (+0.35% TP1, EARLY BE AT +0.15%)
# Target: +0.35% (90% bank), early BE at +0.15%, TP2 at +0.80% (10% runner)
# ─────────────────────────────────────────────────────────────────────────────
def model_micro_harvest(candles_5m, candles_15m):
    closes_5m = [c['close'] for c in candles_5m]
    closes_15m = [c['close'] for c in candles_15m]
    ema9_15m = calc_ema(closes_15m, 9)
    ema21_15m = calc_ema(closes_15m, 21)
    rsi_5m = calc_rsi(closes_5m, 14)
    atr_5m = calc_atr(candles_5m, 14)

    trades = []
    cooldown = 0

    for i in range(25, len(candles_5m) - 12):
        if cooldown > 0:
            cooldown -= 1
            continue

        c = candles_5m[i]
        atr = atr_5m[i]
        rsi = rsi_5m[i]
        idx_15m = min(int(i / 3), len(candles_15m) - 1)
        htf_trend = 'BULLISH' if ema9_15m[idx_15m] > ema21_15m[idx_15m] else 'BEARISH'

        rng = max(c['high'] - c['low'], atr * 0.2)
        lower_wick = min(c['open'], c['close']) - c['low']
        upper_wick = c['high'] - max(c['open'], c['close'])

        side = None
        if htf_trend == 'BULLISH' and (lower_wick / rng >= 0.35) and rsi <= 48 and c['close'] > c['open']:
            side = 'LONG'
        elif htf_trend == 'BEARISH' and (upper_wick / rng >= 0.35) and rsi >= 52 and c['close'] < c['open']:
            side = 'SHORT'

        if not side:
            continue

        entry = c['close']
        stop = entry * 0.9930 if side == 'LONG' else entry * 1.0070  # 0.70% structural buffer
        tp1 = entry * 1.0035 if side == 'LONG' else entry * 0.9965  # +0.35% fast target!
        tp2 = entry * 1.0080 if side == 'LONG' else entry * 0.9920

        res = simulate_trade(candles_5m, i, side, entry, stop, tp1, tp2, tp1_fraction=0.90, max_bars=6, early_be_pct=0.0015)
        if res:
            trades.append(res)
            cooldown = 4
    return trades

# ─────────────────────────────────────────────────────────────────────────────
# MODEL 2: 🚀 EFFICIENCY-FILTERED TREND HARVEST (ER > 0.40 REGIME)
# Only trades when 15m trend is clean (Kaufman Efficiency Ratio > 0.40)
# Avoids all sideways noise and chop!
# ─────────────────────────────────────────────────────────────────────────────
def model_clean_trend_harvest(candles_5m, candles_15m):
    closes_5m = [c['close'] for c in candles_5m]
    closes_15m = [c['close'] for c in candles_15m]
    ema9_15m = calc_ema(closes_15m, 9)
    ema21_15m = calc_ema(closes_15m, 21)
    er_15m = calc_er(closes_15m, 20)
    atr_5m = calc_atr(candles_5m, 14)

    trades = []
    cooldown = 0

    for i in range(25, len(candles_5m) - 12):
        if cooldown > 0:
            cooldown -= 1
            continue

        c = candles_5m[i]
        p = candles_5m[i - 1]
        atr = atr_5m[i]
        idx_15m = min(int(i / 3), len(candles_15m) - 1)

        # Efficiency filter: must be in a clean trend, not chop!
        if er_15m[idx_15m] < 0.38:
            continue

        htf_trend = 'BULLISH' if ema9_15m[idx_15m] > ema21_15m[idx_15m] else 'BEARISH'

        side = None
        # Pullback candle in trend direction
        if htf_trend == 'BULLISH' and p['close'] < p['open'] and c['close'] > c['open'] and c['close'] > p['high']:
            side = 'LONG'
        elif htf_trend == 'BEARISH' and p['close'] > p['open'] and c['close'] < c['open'] and c['close'] < p['low']:
            side = 'SHORT'

        if not side:
            continue

        entry = c['close']
        stop = min(c['low'], p['low']) - atr * 0.4 if side == 'LONG' else max(c['high'], p['high']) + atr * 0.4
        tp1 = entry * 1.0040 if side == 'LONG' else entry * 0.9960
        tp2 = entry * 1.0100 if side == 'LONG' else entry * 0.9900

        res = simulate_trade(candles_5m, i, side, entry, stop, tp1, tp2, tp1_fraction=0.90, max_bars=6, early_be_pct=0.0016)
        if res:
            trades.append(res)
            cooldown = 5
    return trades

# ─────────────────────────────────────────────────────────────────────────────
# MODEL 3: 💎 HIGH-PROBABILITY DOUBLE-WICK REVERSAL (2 CONSECUTIVE DEFENSE WICKS)
# Two consecutive candles print absorption wicks at the same support/resistance floor.
# Extremely high bounce probability!
# ─────────────────────────────────────────────────────────────────────────────
def model_double_wick_defense(candles_5m, candles_15m):
    closes_5m = [c['close'] for c in candles_5m]
    atr_5m = calc_atr(candles_5m, 14)
    trades = []
    cooldown = 0

    for i in range(25, len(candles_5m) - 12):
        if cooldown > 0:
            cooldown -= 1
            continue

        c1 = candles_5m[i - 1]
        c2 = candles_5m[i]
        atr = atr_5m[i]

        rng1 = max(c1['high'] - c1['low'], atr * 0.2)
        rng2 = max(c2['high'] - c2['low'], atr * 0.2)
        lw1 = min(c1['open'], c1['close']) - c1['low']
        lw2 = min(c2['open'], c2['close']) - c2['low']
        uw1 = c1['high'] - max(c1['open'], c1['close'])
        uw2 = c2['high'] - max(c2['open'], c2['close'])

        side = None
        # Double lower wick defense: two consecutive candles defend the same low floor
        if (lw1 / rng1 >= 0.35) and (lw2 / rng2 >= 0.35) and abs(c1['low'] - c2['low']) / c2['low'] <= 0.0015 and c2['close'] > c2['open']:
            side = 'LONG'
        # Double upper wick defense: two consecutive candles reject the same high ceiling
        elif (uw1 / rng1 >= 0.35) and (uw2 / rng2 >= 0.35) and abs(c1['high'] - c2['high']) / c2['high'] <= 0.0015 and c2['close'] < c2['open']:
            side = 'SHORT'

        if not side:
            continue

        entry = c2['close']
        stop = min(c1['low'], c2['low']) - atr * 0.35 if side == 'LONG' else max(c1['high'], c2['high']) + atr * 0.35
        tp1 = entry * 1.0040 if side == 'LONG' else entry * 0.9960
        tp2 = entry * 1.0090 if side == 'LONG' else entry * 0.9910

        res = simulate_trade(candles_5m, i, side, entry, stop, tp1, tp2, tp1_fraction=0.90, max_bars=7, early_be_pct=0.0015)
        if res:
            trades.append(res)
            cooldown = 5
    return trades

def main():
    print("=" * 80)
    print("  TESTING REFINED HIGH WIN-RATE BREAKTHROUGH MODELS (BYBIT 5M/15M TAPES)")
    print("=" * 80)

    dataset = {}
    for sym in SYMBOLS:
        c5m = fetch_klines(sym, '5', limit=1000)
        c15m = fetch_klines(sym, '15', limit=500)
        dataset[sym] = {'5m': c5m, '15m': c15m}
        time.sleep(0.15)

    models = {
        '1. Micro-Harvest (+0.35% TP1, Early BE @ 0.15%)': model_micro_harvest,
        '2. Efficiency-Filtered Trend Harvest (ER>0.38)': model_clean_trend_harvest,
        '3. Double-Wick S/R Reversal (2x Wick Absorption)': model_double_wick_defense
    }

    results = {}
    for name, strat_func in models.items():
        all_trades = []
        for sym in SYMBOLS:
            c5m = dataset[sym]['5m']
            c15m = dataset[sym]['15m']
            t = strat_func(c5m, c15m)
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

        pnl_per_trade = total_net_pnl / n
        proj_100_pnl = pnl_per_trade * 100.0
        avg_bars = sum(x['bars'] for x in all_trades) / n
        avg_hold_mins = avg_bars * 5.0

        results[name] = {
            'trades': n,
            'wins': wins,
            'be': be,
            'losses': losses,
            'win_rate': win_rate,
            'no_loss_rate': no_loss_rate,
            'profit_factor': pf,
            'total_net_pnl': total_net_pnl,
            'proj_100_pnl': proj_100_pnl,
            'avg_hold_mins': avg_hold_mins
        }

    header = f"{'Refined Model Name':<48} | {'Trades':<6} | {'Win %':<6} | {'NoLoss%':<7} | {'PF':<5} | {'Total $':<8} | {'Per 100':<8} | {'Hold':<5}"
    print(header)
    print("-" * len(header))
    for name, r in results.items():
        print(f"{name:<48} | {r['trades']:<6} | {r['win_rate']:<5.1f}% | {r['no_loss_rate']:<6.1f}% | {r['profit_factor']:<5.2f} | ${r['total_net_pnl']:<7.2f} | ${r['proj_100_pnl']:<7.2f} | {r['avg_hold_mins']:<3.0f}m")

if __name__ == '__main__':
    main()
