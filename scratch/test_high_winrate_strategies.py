import urllib.request
import json
import ssl
import time
import math
from collections import defaultdict

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

SYMBOLS = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'XRPUSDT', 'DOGEUSDT']
COST_BPS = 8.0  # 0.08% round trip (taker entry + maker/taker exit + spread)

def fetch_klines(symbol, interval, limit=1000):
    url = f"https://api.bybit.com/v5/market/kline?category=linear&symbol={symbol}&interval={interval}&limit={limit}"
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, context=ctx, timeout=15) as resp:
        data = json.loads(resp.read().decode())
        raw = data.get('result', {}).get('list', [])
        # raw is newest first: [start, open, high, low, close, volume, turnover]
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

def calc_bollinger(closes, period=20, mult=2.5):
    upper = []
    lower = []
    sma = []
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
# SIMULATE TRADE WITH REALISTIC EXECUTION & PARTIAL SCALE-OUT
# ─────────────────────────────────────────────────────────────────────────────
def simulate_trade(candles, entry_idx, side, entry_price, initial_stop, tp1_price, tp2_price,
                   tp1_fraction=0.90, max_bars=12, be_at_tp1=True, early_be_pct=0.0025, notional=100.0):
    """
    Simulates a trade bar-by-bar:
    - Checks for stop loss hit (worst-case intrabar order)
    - Checks for TP1 (+0.50% move): banks tp1_fraction (e.g. 90%), ratchets stop to entry
    - Checks for TP2 with remaining runner fraction (e.g. 10%)
    - Handles timeout after max_bars
    - Returns dollar PnL, R-multiple, outcome (WIN, LOSS, BE), hold bars
    """
    cost_rate = COST_BPS / 10000.0
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

        # Early BE check if price reached early_be_pct
        if not stop_at_be:
            if side == 'LONG' and c['high'] >= entry_price * (1.0 + early_be_pct):
                current_stop = entry_price
                stop_at_be = True
            elif side == 'SHORT' and c['low'] <= entry_price * (1.0 - early_be_pct):
                current_stop = entry_price
                stop_at_be = True

        # Check Stop Loss (pessimistic check)
        hit_stop = (c['low'] <= current_stop) if side == 'LONG' else (c['high'] >= current_stop)
        if hit_stop:
            exit_price = current_stop
            price_move = (exit_price - entry_price) / entry_price if side == 'LONG' else (entry_price - exit_price) / entry_price
            slice_pnl = remaining_fraction * notional * (price_move - cost_rate)
            total_pnl = banked_pnl + slice_pnl
            outcome = 'WIN' if total_pnl > 0.05 else ('BE' if total_pnl >= -0.05 else 'LOSS')
            return {
                'pnl': total_pnl,
                'outcome': outcome,
                'win': total_pnl > 0,
                'bars': bars_held,
                'tp1_filled': tp1_filled,
                'exit_reason': 'STOP_HIT'
            }

        # Check TP1
        if not tp1_filled:
            hit_tp1 = (c['high'] >= tp1_price) if side == 'LONG' else (c['low'] <= tp1_price)
            if hit_tp1:
                tp1_filled = True
                price_move = (tp1_price - entry_price) / entry_price if side == 'LONG' else (entry_price - tp1_price) / entry_price
                slice_pnl = tp1_fraction * notional * (price_move - cost_rate)
                banked_pnl += slice_pnl
                remaining_fraction -= tp1_fraction
                if be_at_tp1:
                    current_stop = entry_price
                    stop_at_be = True
                # If tp1_fraction == 1.0, trade is finished!
                if remaining_fraction <= 0.001:
                    return {
                        'pnl': banked_pnl,
                        'outcome': 'WIN',
                        'win': True,
                        'bars': bars_held,
                        'tp1_filled': True,
                        'exit_reason': 'TP1_FULL_EXIT'
                    }

        # Check TP2 for runner
        if tp1_filled and tp2_price is not None:
            hit_tp2 = (c['high'] >= tp2_price) if side == 'LONG' else (c['low'] <= tp2_price)
            if hit_tp2:
                price_move = (tp2_price - entry_price) / entry_price if side == 'LONG' else (entry_price - tp2_price) / entry_price
                slice_pnl = remaining_fraction * notional * (price_move - cost_rate)
                total_pnl = banked_pnl + slice_pnl
                return {
                    'pnl': total_pnl,
                    'outcome': 'WIN',
                    'win': True,
                    'bars': bars_held,
                    'tp1_filled': True,
                    'exit_reason': 'TP2_RUNNER_EXIT'
                }

    # Time Stop exit on bar close
    exit_c = candles[min(entry_idx + max_bars, len(candles) - 1)]
    exit_price = exit_c['close']
    price_move = (exit_price - entry_price) / entry_price if side == 'LONG' else (entry_price - exit_price) / entry_price
    slice_pnl = remaining_fraction * notional * (price_move - cost_rate)
    total_pnl = banked_pnl + slice_pnl
    outcome = 'WIN' if total_pnl > 0.05 else ('BE' if total_pnl >= -0.05 else 'LOSS')
    return {
        'pnl': total_pnl,
        'outcome': outcome,
        'win': total_pnl > 0,
        'bars': max_bars,
        'tp1_filled': tp1_filled,
        'exit_reason': 'TIME_STOP'
    }

# ─────────────────────────────────────────────────────────────────────────────
# STRATEGY 1: 🎯 SURESHOT MICRO-SCALP
# HTF Trend (EMA9 vs EMA21) + 5m Value Pullback + 5m Absorption Wick
# Target: +0.50% TP1 (90% bank), BE ratchet, +1.20% TP2 (10% runner)
# ─────────────────────────────────────────────────────────────────────────────
def run_sureshot_strategy(candles_5m, candles_15m):
    closes_5m = [c['close'] for c in candles_5m]
    closes_15m = [c['close'] for c in candles_15m]
    ema9_15m = calc_ema(closes_15m, 9)
    ema21_15m = calc_ema(closes_15m, 21)
    rsi_5m = calc_rsi(closes_5m, 14)
    atr_5m = calc_atr(candles_5m, 14)

    trades = []
    cooldown = 0

    for i in range(30, len(candles_5m) - 15):
        if cooldown > 0:
            cooldown -= 1
            continue

        c = candles_5m[i]
        p = candles_5m[i - 1]
        atr = atr_5m[i]
        rsi = rsi_5m[i]

        # Find corresponding 15m candle index
        idx_15m = min(int(i / 3), len(candles_15m) - 1)
        htf_trend = 'BULLISH' if ema9_15m[idx_15m] > ema21_15m[idx_15m] else 'BEARISH'

        rng = max(c['high'] - c['low'], atr * 0.2)
        lower_wick = min(c['open'], c['close']) - c['low']
        upper_wick = c['high'] - max(c['open'], c['close'])
        lower_wick_pct = lower_wick / rng
        upper_wick_pct = upper_wick / rng

        side = None
        if htf_trend == 'BULLISH' and (lower_wick_pct >= 0.32 or rsi < 46) and c['close'] > p['low']:
            side = 'LONG'
        elif htf_trend == 'BEARISH' and (upper_wick_pct >= 0.32 or rsi > 54) and c['close'] < p['high']:
            side = 'SHORT'

        if not side:
            continue

        entry = c['close']
        buffer = atr * 0.4
        stop = min(c['low'], p['low']) - buffer if side == 'LONG' else max(c['high'], p['high']) + buffer
        tp1 = entry * 1.0050 if side == 'LONG' else entry * 0.9950
        tp2 = entry * 1.0120 if side == 'LONG' else entry * 0.9880

        res = simulate_trade(candles_5m, i, side, entry, stop, tp1, tp2, tp1_fraction=0.90, max_bars=8)
        if res:
            trades.append(res)
            cooldown = 4  # 20-minute cooldown
    return trades

# ─────────────────────────────────────────────────────────────────────────────
# STRATEGY 2: 🌊 LIQUIDITY SWEEP & WICK RECLAIM (TURTLE SOUP)
# Sweeps past 20-bar extreme into resting stops, closes back inside range with rejection wick
# Target: +0.60% TP1 (85% bank), BE ratchet, +1.40% TP2 (15% runner)
# ─────────────────────────────────────────────────────────────────────────────
def run_liquidity_sweep_strategy(candles_5m):
    closes = [c['close'] for c in candles_5m]
    atr_5m = calc_atr(candles_5m, 14)
    trades = []
    cooldown = 0

    for i in range(25, len(candles_5m) - 15):
        if cooldown > 0:
            cooldown -= 1
            continue

        c = candles_5m[i]
        atr = atr_5m[i]

        # Prior 20-bar high/low (excluding current candle)
        prior_slice = candles_5m[i - 20 : i]
        prior_high = max(x['high'] for x in prior_slice)
        prior_low = min(x['low'] for x in prior_slice)

        rng = max(c['high'] - c['low'], atr * 0.2)
        lower_wick = min(c['open'], c['close']) - c['low']
        upper_wick = c['high'] - max(c['open'], c['close'])

        side = None
        # Bullish sweep: dipped below prior_low by at least 0.10%, but closed BACK ABOVE prior_low
        if c['low'] < prior_low and (prior_low - c['low']) / prior_low >= 0.0010 and c['close'] > prior_low and (lower_wick / rng >= 0.35):
            side = 'LONG'
        # Bearish sweep: spiked above prior_high by at least 0.10%, but closed BACK BELOW prior_high
        elif c['high'] > prior_high and (c['high'] - prior_high) / prior_high >= 0.0010 and c['close'] < prior_high and (upper_wick / rng >= 0.35):
            side = 'SHORT'

        if not side:
            continue

        entry = c['close']
        stop = c['low'] - atr * 0.25 if side == 'LONG' else c['high'] + atr * 0.25
        tp1 = entry * 1.0060 if side == 'LONG' else entry * 0.9940
        tp2 = entry * 1.0140 if side == 'LONG' else entry * 0.9860

        res = simulate_trade(candles_5m, i, side, entry, stop, tp1, tp2, tp1_fraction=0.85, max_bars=10)
        if res:
            trades.append(res)
            cooldown = 5
    return trades

# ─────────────────────────────────────────────────────────────────────────────
# STRATEGY 3: ⚡ BOLLINGER BAND SQUEEZE EXHAUSTION (2.5σ CAPITULATION FADE)
# Pierces 2.5σ Bollinger Band + RSI exhaustion (<25 / >75) + Volume spike
# Target: +0.50% TP1 (90% bank), BE ratchet, +1.00% TP2
# ─────────────────────────────────────────────────────────────────────────────
def run_bollinger_exhaustion_strategy(candles_5m):
    closes = [c['close'] for c in candles_5m]
    vols = [c['volume'] for c in candles_5m]
    sma20, upper, lower = calc_bollinger(closes, period=20, mult=2.5)
    rsi_5m = calc_rsi(closes, 14)
    atr_5m = calc_atr(candles_5m, 14)

    trades = []
    cooldown = 0

    for i in range(25, len(candles_5m) - 15):
        if cooldown > 0:
            cooldown -= 1
            continue

        c = candles_5m[i]
        p = candles_5m[i - 1]
        rsi = rsi_5m[i]
        atr = atr_5m[i]
        avg_vol = sum(vols[i - 15 : i]) / 15.0

        vol_spike = c['volume'] >= 1.6 * avg_vol

        side = None
        # Oversold capitulation: pierced lower 2.5 sigma, RSI < 25, volume spike, closed green or formed hammer
        if c['low'] <= lower[i] and rsi <= 26 and vol_spike and c['close'] > c['open']:
            side = 'LONG'
        # Overbought euphoria: pierced upper 2.5 sigma, RSI > 75, volume spike, closed red or formed shooting star
        elif c['high'] >= upper[i] and rsi >= 74 and vol_spike and c['close'] < c['open']:
            side = 'SHORT'

        if not side:
            continue

        entry = c['close']
        stop = c['low'] - atr * 0.4 if side == 'LONG' else c['high'] + atr * 0.4
        tp1 = entry * 1.0050 if side == 'LONG' else entry * 0.9950
        tp2 = sma20[i]  # Reversion to 20 SMA

        res = simulate_trade(candles_5m, i, side, entry, stop, tp1, tp2, tp1_fraction=0.90, max_bars=8)
        if res:
            trades.append(res)
            cooldown = 5
    return trades

# ─────────────────────────────────────────────────────────────────────────────
# STRATEGY 4: 🚀 MULTI-CONFLUENCE SUPERTREND PULLBACK (TREND CONTINUATION)
# 15m Trend + 5m EMA9 Pullback + Momentum Re-acceleration
# Target: +0.65% TP1 (80% bank), BE ratchet, +1.50% TP2
# ─────────────────────────────────────────────────────────────────────────────
def run_trend_pullback_strategy(candles_5m, candles_15m):
    closes_5m = [c['close'] for c in candles_5m]
    closes_15m = [c['close'] for c in candles_15m]
    ema9_5m = calc_ema(closes_5m, 9)
    ema21_5m = calc_ema(closes_5m, 21)
    ema50_5m = calc_ema(closes_5m, 50)
    ema21_15m = calc_ema(closes_15m, 21)
    atr_5m = calc_atr(candles_5m, 14)

    trades = []
    cooldown = 0

    for i in range(50, len(candles_5m) - 15):
        if cooldown > 0:
            cooldown -= 1
            continue

        c = candles_5m[i]
        p = candles_5m[i - 1]
        atr = atr_5m[i]
        idx_15m = min(int(i / 3), len(candles_15m) - 1)

        # 15m trend filter
        htf_bull = closes_15m[idx_15m] > ema21_15m[idx_15m]
        htf_bear = closes_15m[idx_15m] < ema21_15m[idx_15m]

        side = None
        # Strong uptrend: 5m EMA9 > EMA21 > EMA50, dipped near EMA21, now bounced with bullish close
        if htf_bull and (ema9_5m[i] > ema21_5m[i] > ema50_5m[i]) and (p['low'] <= ema21_5m[i] * 1.002) and (c['close'] > ema9_5m[i]):
            side = 'LONG'
        # Strong downtrend: 5m EMA9 < EMA21 < EMA50, rallied near EMA21, now rejected with bearish close
        elif htf_bear and (ema9_5m[i] < ema21_5m[i] < ema50_5m[i]) and (p['high'] >= ema21_5m[i] * 0.998) and (c['close'] < ema9_5m[i]):
            side = 'SHORT'

        if not side:
            continue

        entry = c['close']
        stop = ema50_5m[i] - atr * 0.3 if side == 'LONG' else ema50_5m[i] + atr * 0.3
        tp1 = entry * 1.0065 if side == 'LONG' else entry * 0.9935
        tp2 = entry * 1.0150 if side == 'LONG' else entry * 0.9850

        res = simulate_trade(candles_5m, i, side, entry, stop, tp1, tp2, tp1_fraction=0.80, max_bars=10)
        if res:
            trades.append(res)
            cooldown = 6
    return trades

# ─────────────────────────────────────────────────────────────────────────────
# STRATEGY 5: 🧱 VOLUME ABSORPTION CONSOLIDATION BREAK (DELTA ACCUMULATION)
# Tight consolidation box (range < 0.60%) broken on 2.0x volume surge with trend alignment
# Target: +0.50% TP1 (90% bank), BE ratchet, +1.20% TP2
# ─────────────────────────────────────────────────────────────────────────────
def run_absorption_breakout_strategy(candles_5m, candles_15m):
    closes_5m = [c['close'] for c in candles_5m]
    vols_5m = [c['volume'] for c in candles_5m]
    atr_5m = calc_atr(candles_5m, 14)
    closes_15m = [c['close'] for c in candles_15m]
    ema21_15m = calc_ema(closes_15m, 21)

    trades = []
    cooldown = 0

    for i in range(25, len(candles_5m) - 15):
        if cooldown > 0:
            cooldown -= 1
            continue

        c = candles_5m[i]
        atr = atr_5m[i]
        idx_15m = min(int(i / 3), len(candles_15m) - 1)
        htf_trend = 'BULLISH' if closes_15m[idx_15m] > ema21_15m[idx_15m] else 'BEARISH'

        # Check last 6 candles consolidation range
        box = candles_5m[i - 6 : i]
        box_high = max(x['high'] for x in box)
        box_low = min(x['low'] for x in box)
        box_width = (box_high - box_low) / box_low

        if box_width > 0.0075: # Must be a tight consolidation squeeze (<0.75%)
            continue

        avg_vol = sum(vols_5m[i - 12 : i]) / 12.0
        vol_surge = c['volume'] >= 1.8 * avg_vol

        side = None
        if htf_trend == 'BULLISH' and vol_surge and c['close'] > box_high:
            side = 'LONG'
        elif htf_trend == 'BEARISH' and vol_surge and c['close'] < box_low:
            side = 'SHORT'

        if not side:
            continue

        entry = c['close']
        stop = box_low - atr * 0.2 if side == 'LONG' else box_high + atr * 0.2
        tp1 = entry * 1.0050 if side == 'LONG' else entry * 0.9950
        tp2 = entry * 1.0120 if side == 'LONG' else entry * 0.9880

        res = simulate_trade(candles_5m, i, side, entry, stop, tp1, tp2, tp1_fraction=0.90, max_bars=8)
        if res:
            trades.append(res)
            cooldown = 5
    return trades

# ─────────────────────────────────────────────────────────────────────────────
# MAIN TESTING HARNESS
# ─────────────────────────────────────────────────────────────────────────────
def main():
    print("=" * 80)
    print("  EMPIRICAL STRATEGY EVALUATION FOR HIGH WIN-RATE SCALPING")
    print(f"  Cost model: {COST_BPS} bps per round trip | Position sizing: $10 margin @ 10x ($100 notional)")
    print("=" * 80)

    dataset = {}
    print("\n[1/3] Downloading continuous klines across top liquid coins...")
    for sym in SYMBOLS:
        print(f"  Fetching {sym} (5m & 15m klines)...")
        c5m = fetch_klines(sym, '5', limit=1000)
        c15m = fetch_klines(sym, '15', limit=500)
        dataset[sym] = {'5m': c5m, '15m': c15m}
        time.sleep(0.2)
    print("  Klines download complete across all 5 symbols.")

    strategies = {
        '1. SureShot Micro-Scalp (0.50% TP1, 90% Bank, BE)': run_sureshot_strategy,
        '2. Liquidity Sweep & Wick Trap (Turtle Soup)': run_liquidity_sweep_strategy,
        '3. Bollinger 2.5-Sigma Capitulation Fade': run_bollinger_exhaustion_strategy,
        '4. EMA Supertrend Pullback': run_trend_pullback_strategy,
        '5. Volume Absorption Breakout': run_absorption_breakout_strategy
    }

    results = {}

    print("\n[2/3] Simulating strategies bar-by-bar...")
    for name, strat_func in strategies.items():
        all_trades = []
        for sym in SYMBOLS:
            c5m = dataset[sym]['5m']
            c15m = dataset[sym]['15m']
            if name in ['1. SureShot Micro-Scalp (0.50% TP1, 90% Bank, BE)', '4. EMA Supertrend Pullback', '5. Volume Absorption Breakout']:
                t = strat_func(c5m, c15m)
            else:
                t = strat_func(c5m)
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

        # Drawdown calculation
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

    print("\n[3/3] Backtest Simulation Completed! Summary Table:\n")
    header = f"{'Strategy Name':<45} | {'Trades':<6} | {'Win %':<6} | {'NoLoss%':<7} | {'PF':<5} | {'Total $':<8} | {'Per 100':<8} | {'Hold':<6}"
    print(header)
    print("-" * len(header))

    for name, r in results.items():
        if r['trades'] == 0:
            print(f"{name:<45} | 0 trades")
            continue
        print(f"{name:<45} | {r['trades']:<6} | {r['win_rate']:<5.1f}% | {r['no_loss_rate']:<6.1f}% | {r['profit_factor']:<5.2f} | ${r['total_net_pnl']:<7.2f} | ${r['proj_100_pnl']:<7.2f} | {r['avg_hold_mins']:<4.0f}m")

    # Save results to json
    with open('scratch/high_winrate_results.json', 'w', encoding='utf-8') as f:
        json.dump(results, f, indent=2)
    print("\nDetailed results saved to scratch/high_winrate_results.json")

if __name__ == '__main__':
    main()
