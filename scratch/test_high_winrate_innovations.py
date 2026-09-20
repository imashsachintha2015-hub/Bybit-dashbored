import urllib.request
import json
import ssl
import time
import math

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

SYMBOLS = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'XRPUSDT', 'DOGEUSDT']
MAKER_COST_BPS = 3.5  # 0.035% round trip with Limit Order entry + Limit/Market exit

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

def calc_bollinger(closes, period=20, mult=2.0):
    upper, lower, sma = [], [], []
    for i in range(len(closes)):
        if i < period - 1:
            upper.append(closes[i])
            lower.append(closes[i])
            sma.append(closes[i])
        else:
            slice_ = closes[i - period + 1 : i + 1]
            m = sum(slice_) / period
            variance = sum((x - m) ** 2 for x in slice_) / period
            std = math.sqrt(variance)
            sma.append(m)
            upper.append(m + mult * std)
            lower.append(m - mult * std)
    return sma, upper, lower

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

# ─────────────────────────────────────────────────────────────────────────────
# REALISTIC SIMULATOR WITH TIGHT BREAK-EVEN RATCHET & MAKER FEES
# ─────────────────────────────────────────────────────────────────────────────
def simulate_trade(candles, entry_idx, side, entry_price, initial_stop, tp1_price, tp2_price,
                   tp1_fraction=0.90, max_bars=10, early_be_pct=0.0020, cost_bps=MAKER_COST_BPS, notional=100.0):
    cost_rate = cost_bps / 10000.0
    current_stop = initial_stop
    tp1_filled = False
    stop_at_be = False
    banked_pnl = 0.0
    remaining_fraction = 1.0

    for k in range(entry_idx + 1, min(entry_idx + 1 + max_bars, len(candles))):
        c = candles[k]
        bars_held = k - entry_idx

        # Early BE trigger (e.g. +0.20% gain moves stop to Entry price)
        if not stop_at_be:
            if side == 'LONG' and c['high'] >= entry_price * (1.0 + early_be_pct):
                current_stop = entry_price
                stop_at_be = True
            elif side == 'SHORT' and c['low'] <= entry_price * (1.0 - early_be_pct):
                current_stop = entry_price
                stop_at_be = True

        # Check Stop Loss
        hit_stop = (c['low'] <= current_stop) if side == 'LONG' else (c['high'] >= current_stop)
        if hit_stop:
            exit_price = current_stop
            price_move = (exit_price - entry_price) / entry_price if side == 'LONG' else (entry_price - exit_price) / entry_price
            slice_pnl = remaining_fraction * notional * (price_move - cost_rate)
            total_pnl = banked_pnl + slice_pnl
            outcome = 'WIN' if total_pnl > 0.03 else ('BE' if total_pnl >= -0.05 else 'LOSS')
            return {'pnl': total_pnl, 'outcome': outcome, 'win': total_pnl > 0, 'bars': bars_held, 'reason': 'STOP'}

        # Check TP1
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

        # Check TP2
        if tp1_filled and tp2_price is not None:
            hit_tp2 = (c['high'] >= tp2_price) if side == 'LONG' else (c['low'] <= tp2_price)
            if hit_tp2:
                price_move = (tp2_price - entry_price) / entry_price if side == 'LONG' else (entry_price - tp2_price) / entry_price
                slice_pnl = remaining_fraction * notional * (price_move - cost_rate)
                total_pnl = banked_pnl + slice_pnl
                return {'pnl': total_pnl, 'outcome': 'WIN', 'win': True, 'bars': bars_held, 'reason': 'TP2_RUNNER'}

    # Max bars timeout
    exit_c = candles[min(entry_idx + max_bars, len(candles) - 1)]
    exit_price = exit_c['close']
    price_move = (exit_price - entry_price) / entry_price if side == 'LONG' else (entry_price - exit_price) / entry_price
    slice_pnl = remaining_fraction * notional * (price_move - cost_rate)
    total_pnl = banked_pnl + slice_pnl
    outcome = 'WIN' if total_pnl > 0.03 else ('BE' if total_pnl >= -0.05 else 'LOSS')
    return {'pnl': total_pnl, 'outcome': outcome, 'win': total_pnl > 0, 'bars': max_bars, 'reason': 'TIMEOUT'}

# ─────────────────────────────────────────────────────────────────────────────
# NEW ARCHITECTURE 1: 🎯 HIGH-CONVICTION S/R DEFENSE (KEY LEVEL REACTION)
# Identifies tested horizontal support/resistance levels on 15m.
# When price touches level with a rejection hammer/shooting star, enter with Limit order.
# TP1: +0.40% (bank 90%), Early BE at +0.18%. Stop: structural floor.
# ─────────────────────────────────────────────────────────────────────────────
def strat_sr_defense(candles_5m, candles_15m):
    closes_15m = [c['close'] for c in candles_15m]
    atr_5m = calc_atr(candles_5m, 14)
    trades = []
    cooldown = 0

    for i in range(30, len(candles_5m) - 12):
        if cooldown > 0:
            cooldown -= 1
            continue

        c = candles_5m[i]
        atr = atr_5m[i]

        # S/R levels from 15m pivots
        idx_15m = min(int(i / 3), len(candles_15m) - 1)
        if idx_15m < 20:
            continue
        recent_15m = candles_15m[idx_15m - 15 : idx_15m]
        res_level = max(x['high'] for x in recent_15m)
        sup_level = min(x['low'] for x in recent_15m)

        dist_to_sup = abs(c['low'] - sup_level) / sup_level
        dist_to_res = abs(c['high'] - res_level) / res_level

        rng = max(c['high'] - c['low'], atr * 0.2)
        lower_wick = min(c['open'], c['close']) - c['low']
        upper_wick = c['high'] - max(c['open'], c['close'])

        side = None
        # Defending support floor: dipped within 0.20% of support and printed a hammer rejection (wick >= 45%)
        if dist_to_sup <= 0.0025 and (lower_wick / rng >= 0.45) and c['close'] > sup_level:
            side = 'LONG'
        # Defending resistance ceiling: tested within 0.20% of resistance and printed shooting star (wick >= 45%)
        elif dist_to_res <= 0.0025 and (upper_wick / rng >= 0.45) and c['close'] < res_level:
            side = 'SHORT'

        if not side:
            continue

        entry = c['close']
        stop = sup_level - atr * 0.5 if side == 'LONG' else res_level + atr * 0.5
        tp1 = entry * 1.0040 if side == 'LONG' else entry * 0.9960
        tp2 = entry * 1.0100 if side == 'LONG' else entry * 0.9900

        res = simulate_trade(candles_5m, i, side, entry, stop, tp1, tp2, tp1_fraction=0.90, max_bars=8, early_be_pct=0.0018)
        if res:
            trades.append(res)
            cooldown = 6
    return trades

# ─────────────────────────────────────────────────────────────────────────────
# NEW ARCHITECTURE 2: ⚡ DUAL BOLLINGER MEAN REVERSION (2.0σ + RSI EXTREME)
# Extreme LTF stretch: 5m price pierces 2.0σ Bollinger Band + RSI < 28 or > 72.
# Enters on confirmation candle closing back inside the band.
# Target: +0.45% move toward 20 SMA. Early BE at +0.20%.
# ─────────────────────────────────────────────────────────────────────────────
def strat_bollinger_reversion(candles_5m, candles_15m):
    closes_5m = [c['close'] for c in candles_5m]
    sma20, upper, lower = calc_bollinger(closes_5m, period=20, mult=2.0)
    rsi_5m = calc_rsi(closes_5m, 14)
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
        rsi = rsi_5m[i]

        side = None
        # Oversold snap: previous candle pierced lower band and RSI < 28, current candle forms green reversal
        if p['low'] <= lower[i - 1] and rsi_5m[i - 1] <= 28 and c['close'] > p['close'] and c['close'] > lower[i]:
            side = 'LONG'
        # Overbought snap: previous candle pierced upper band and RSI > 72, current candle forms red reversal
        elif p['high'] >= upper[i - 1] and rsi_5m[i - 1] >= 72 and c['close'] < p['close'] and c['close'] < upper[i]:
            side = 'SHORT'

        if not side:
            continue

        entry = c['close']
        stop = min(c['low'], p['low']) - atr * 0.4 if side == 'LONG' else max(c['high'], p['high']) + atr * 0.4
        tp1 = entry * 1.0045 if side == 'LONG' else entry * 0.9955
        tp2 = sma20[i]

        res = simulate_trade(candles_5m, i, side, entry, stop, tp1, tp2, tp1_fraction=0.90, max_bars=8, early_be_pct=0.0020)
        if res:
            trades.append(res)
            cooldown = 5
    return trades

# ─────────────────────────────────────────────────────────────────────────────
# NEW ARCHITECTURE 3: 🛡️ PATIENT LIMIT RETEST PULLBACK (HTF 1H TREND + 50% FIB RETEST)
# Eliminates FOMO top buying. When a momentum candle fires in trend direction,
# places a LIMIT ORDER at the 50% retrace of that candle (buying the dip).
# Target: +0.50% from the limit fill. Early BE at +0.22%.
# ─────────────────────────────────────────────────────────────────────────────
def strat_patient_retest(candles_5m, candles_15m):
    closes_5m = [c['close'] for c in candles_5m]
    closes_15m = [c['close'] for c in candles_15m]
    ema9_15m = calc_ema(closes_15m, 9)
    ema21_15m = calc_ema(closes_15m, 21)
    atr_5m = calc_atr(candles_5m, 14)

    trades = []
    cooldown = 0

    for i in range(25, len(candles_5m) - 15):
        if cooldown > 0:
            cooldown -= 1
            continue

        c = candles_5m[i]
        atr = atr_5m[i]
        idx_15m = min(int(i / 3), len(candles_15m) - 1)
        htf_trend = 'BULLISH' if ema9_15m[idx_15m] > ema21_15m[idx_15m] else 'BEARISH'

        body = abs(c['close'] - c['open'])
        if body < atr * 0.75:  # Must be a strong momentum candle
            continue

        # Limit order placed at 50% midpoint of the impulse candle
        limit_entry = (c['open'] + c['close']) / 2.0
        side = 'LONG' if (htf_trend == 'BULLISH' and c['close'] > c['open']) else ('SHORT' if (htf_trend == 'BEARISH' and c['close'] < c['open']) else None)

        if not side:
            continue

        # Check if next 1-2 candles fill our limit order
        filled = False
        fill_bar = None
        for fill_k in range(i + 1, min(i + 3, len(candles_5m))):
            fc = candles_5m[fill_k]
            if side == 'LONG' and fc['low'] <= limit_entry:
                filled = True
                fill_bar = fill_k
                break
            elif side == 'SHORT' and fc['high'] >= limit_entry:
                filled = True
                fill_bar = fill_k
                break

        if not filled:
            continue

        stop = c['low'] - atr * 0.4 if side == 'LONG' else c['high'] + atr * 0.4
        tp1 = limit_entry * 1.0050 if side == 'LONG' else limit_entry * 0.9950
        tp2 = limit_entry * 1.0120 if side == 'LONG' else limit_entry * 0.9880

        res = simulate_trade(candles_5m, fill_bar, side, limit_entry, stop, tp1, tp2, tp1_fraction=0.90, max_bars=8, early_be_pct=0.0022)
        if res:
            trades.append(res)
            cooldown = 5
    return trades

# ─────────────────────────────────────────────────────────────────────────────
# NEW ARCHITECTURE 4: 🌊 LIQUIDITY SWEEP WITH STRICT HTF TREND FILTER
# Only takes liquidity sweeps IN THE DIRECTION of the 15m/1h macro trend.
# When in HTF uptrend: takes ONLY dips below 20-bar low that sweep resting stops and bounce.
# When in HTF downtrend: takes ONLY spikes above 20-bar high that sweep stops and dump.
# Target: +0.55% TP1 (85% bank), BE ratchet, +1.25% TP2 (15% runner).
# ─────────────────────────────────────────────────────────────────────────────
def strat_trend_liquidity_sweep(candles_5m, candles_15m):
    closes_5m = [c['close'] for c in candles_5m]
    closes_15m = [c['close'] for c in candles_15m]
    ema9_15m = calc_ema(closes_15m, 9)
    ema21_15m = calc_ema(closes_15m, 21)
    atr_5m = calc_atr(candles_5m, 14)

    trades = []
    cooldown = 0

    for i in range(25, len(candles_5m) - 15):
        if cooldown > 0:
            cooldown -= 1
            continue

        c = candles_5m[i]
        atr = atr_5m[i]
        idx_15m = min(int(i / 3), len(candles_15m) - 1)
        htf_trend = 'BULLISH' if ema9_15m[idx_15m] > ema21_15m[idx_15m] else 'BEARISH'

        prior_slice = candles_5m[i - 20 : i]
        prior_high = max(x['high'] for x in prior_slice)
        prior_low = min(x['low'] for x in prior_slice)

        rng = max(c['high'] - c['low'], atr * 0.2)
        lower_wick = min(c['open'], c['close']) - c['low']
        upper_wick = c['high'] - max(c['open'], c['close'])

        side = None
        # HTF Bullish: only buying stop-run sweeps of local lows!
        if htf_trend == 'BULLISH' and c['low'] < prior_low and (prior_low - c['low']) / prior_low >= 0.0008 and c['close'] > prior_low and (lower_wick / rng >= 0.35):
            side = 'LONG'
        # HTF Bearish: only shorting stop-run sweeps of local highs!
        elif htf_trend == 'BEARISH' and c['high'] > prior_high and (c['high'] - prior_high) / prior_high >= 0.0008 and c['close'] < prior_high and (upper_wick / rng >= 0.35):
            side = 'SHORT'

        if not side:
            continue

        entry = c['close']
        stop = c['low'] - atr * 0.3 if side == 'LONG' else c['high'] + atr * 0.3
        tp1 = entry * 1.0055 if side == 'LONG' else entry * 0.9945
        tp2 = entry * 1.0125 if side == 'LONG' else entry * 0.9875

        res = simulate_trade(candles_5m, i, side, entry, stop, tp1, tp2, tp1_fraction=0.85, max_bars=9, early_be_pct=0.0020)
        if res:
            trades.append(res)
            cooldown = 5
    return trades

# ─────────────────────────────────────────────────────────────────────────────
# NEW ARCHITECTURE 5: 💎 WIDE-ANCHOR SURESHOT WITH RAPID BE RATCHET
# Keeps target at +0.50% (+5% ROE @ 10x), but widens invalidation stop beyond HTF structure (1.2% away).
# To eliminate large losses: Ratchets stop to Entry at +0.18% move!
# If price doesn't hit +0.18% in 4 bars (20 min), exits with time-stop scratch.
# ─────────────────────────────────────────────────────────────────────────────
def strat_wide_anchor_sureshot(candles_5m, candles_15m):
    closes_5m = [c['close'] for c in candles_5m]
    closes_15m = [c['close'] for c in candles_15m]
    ema9_15m = calc_ema(closes_15m, 9)
    ema21_15m = calc_ema(closes_15m, 21)
    rsi_5m = calc_rsi(closes_5m, 14)
    atr_5m = calc_atr(candles_5m, 14)

    trades = []
    cooldown = 0

    for i in range(25, len(candles_5m) - 15):
        if cooldown > 0:
            cooldown -= 1
            continue

        c = candles_5m[i]
        p = candles_5m[i - 1]
        atr = atr_5m[i]
        rsi = rsi_5m[i]
        idx_15m = min(int(i / 3), len(candles_15m) - 1)
        htf_trend = 'BULLISH' if ema9_15m[idx_15m] > ema21_15m[idx_15m] else 'BEARISH'

        rng = max(c['high'] - c['low'], atr * 0.2)
        lower_wick = min(c['open'], c['close']) - c['low']
        upper_wick = c['high'] - max(c['open'], c['close'])

        side = None
        # High selectivity: HTF trend alignment + RSI pullback + clear wick
        if htf_trend == 'BULLISH' and (lower_wick / rng >= 0.38) and rsi <= 48 and c['close'] > c['open']:
            side = 'LONG'
        elif htf_trend == 'BEARISH' and (upper_wick / rng >= 0.38) and rsi >= 52 and c['close'] < c['open']:
            side = 'SHORT'

        if not side:
            continue

        entry = c['close']
        # Wide invalidation stop (1.2% away to avoid normal market noise stop-hunting)
        stop = entry * 0.9880 if side == 'LONG' else entry * 1.0120
        tp1 = entry * 1.0050 if side == 'LONG' else entry * 0.9950
        tp2 = entry * 1.0110 if side == 'LONG' else entry * 0.9890

        # Rapid BE ratchet at +0.18% move
        res = simulate_trade(candles_5m, i, side, entry, stop, tp1, tp2, tp1_fraction=0.90, max_bars=6, early_be_pct=0.0018)
        if res:
            trades.append(res)
            cooldown = 5
    return trades

def main():
    print("=" * 80)
    print("  TESTING 5 NEW HIGH WIN-RATE STRATEGY ARCHITECTURES (BYBIT LIVE DATA)")
    print("=" * 80)

    dataset = {}
    print("\nFetching latest kline batches...")
    for sym in SYMBOLS:
        c5m = fetch_klines(sym, '5', limit=1000)
        c15m = fetch_klines(sym, '15', limit=500)
        dataset[sym] = {'5m': c5m, '15m': c15m}
        time.sleep(0.15)
    print("Data download completed.\n")

    strategies = {
        'A. S/R Key Level Defense (Support Bounce)': strat_sr_defense,
        'B. Dual Bollinger 2.0-Sigma Snapback': strat_bollinger_reversion,
        'C. Patient Limit Retest (50% Pullback Dip)': strat_patient_retest,
        'D. Trend-Aligned Liquidity Sweep (Turtle Soup)': strat_trend_liquidity_sweep,
        'E. Wide-Anchor SureShot + Rapid BE (+0.18%)': strat_wide_anchor_sureshot
    }

    results = {}
    for name, strat_func in strategies.items():
        all_trades = []
        for sym in SYMBOLS:
            c5m = dataset[sym]['5m']
            c15m = dataset[sym]['15m']
            t = strat_func(c5m, c15m)
            all_trades.extend(t)

        n = len(all_trades)
        if n == 0:
            results[name] = {'trades': 0}
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

        equity = 0.0
        peak = 0.0
        max_dd = 0.0
        for x in all_trades:
            equity += x['pnl']
            if equity > peak:
                peak = equity
            dd = peak - equity
            if dd > max_dd:
                max_dd = dd

        avg_bars = sum(x['bars'] for x in all_trades) / n
        avg_hold_mins = avg_bars * 5.0
        pnl_per_trade = total_net_pnl / n
        proj_100_pnl = pnl_per_trade * 100.0

        results[name] = {
            'trades': n,
            'wins': wins,
            'be': be,
            'losses': losses,
            'win_rate': win_rate,
            'no_loss_rate': no_loss_rate,
            'profit_factor': pf,
            'total_net_pnl': total_net_pnl,
            'pnl_per_trade': pnl_per_trade,
            'proj_100_pnl': proj_100_pnl,
            'max_dd': max_dd,
            'avg_hold_mins': avg_hold_mins
        }

    header = f"{'Strategy Architecture':<46} | {'Trades':<6} | {'Win %':<6} | {'NoLoss%':<7} | {'PF':<5} | {'Total $':<8} | {'Per 100':<8} | {'Hold':<5}"
    print(header)
    print("-" * len(header))

    for name, r in results.items():
        if r['trades'] == 0:
            print(f"{name:<46} | 0 trades")
            continue
        print(f"{name:<46} | {r['trades']:<6} | {r['win_rate']:<5.1f}% | {r['no_loss_rate']:<6.1f}% | {r['profit_factor']:<5.2f} | ${r['total_net_pnl']:<7.2f} | ${r['proj_100_pnl']:<7.2f} | {r['avg_hold_mins']:<3.0f}m")

    with open('scratch/innovative_strategies_results.json', 'w', encoding='utf-8') as f:
        json.dump(results, f, indent=2)

if __name__ == '__main__':
    main()
