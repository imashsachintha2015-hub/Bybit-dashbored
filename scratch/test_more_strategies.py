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
MAKER_COST_BPS = 3.5  # 0.035% round trip (maker entry + maker/taker exit)
TAKER_COST_BPS = 8.0  # 0.080% round trip (taker entry + market exit)

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

def calc_sma(values, period):
    out = []
    for i in range(len(values)):
        if i < period - 1:
            out.append(values[i])
        else:
            out.append(sum(values[i - period + 1 : i + 1]) / period)
    return out

def calc_stdev(values, period):
    smas = calc_sma(values, period)
    out = []
    for i in range(len(values)):
        if i < period - 1:
            out.append(0.0)
        else:
            slice_ = values[i - period + 1 : i + 1]
            m = smas[i]
            var = sum((x - m) ** 2 for x in slice_) / period
            out.append(math.sqrt(var))
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

# ─────────────────────────────────────────────────────────────────────────────
# SIMULATION ENGINE
# ─────────────────────────────────────────────────────────────────────────────
def simulate_trade(candles, entry_idx, side, entry_price, initial_stop, tp1_price, tp2_price=None,
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

        # Early break-even trigger
        if not stop_at_be and early_be_pct > 0:
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

    # Timeout
    exit_c = candles[min(entry_idx + max_bars, len(candles) - 1)]
    exit_price = exit_c['close']
    price_move = (exit_price - entry_price) / entry_price if side == 'LONG' else (entry_price - exit_price) / entry_price
    slice_pnl = remaining_fraction * notional * (price_move - cost_rate)
    total_pnl = banked_pnl + slice_pnl
    outcome = 'WIN' if total_pnl > 0.03 else ('BE' if total_pnl >= -0.05 else 'LOSS')
    return {'pnl': total_pnl, 'outcome': outcome, 'win': total_pnl > 0, 'bars': max_bars, 'reason': 'TIMEOUT'}

# ─────────────────────────────────────────────────────────────────────────────
# 1. 🌊 INSTITUTIONAL LIQUIDITY SWEEP & RETEST ("Ghost Hunter")
# ─────────────────────────────────────────────────────────────────────────────
def strat_liquidity_sweep_retest(candles_5m, candles_15m):
    closes_5m = [c['close'] for c in candles_5m]
    closes_15m = [c['close'] for c in candles_15m]
    ema9_15m = calc_ema(closes_15m, 9)
    ema21_15m = calc_ema(closes_15m, 21)
    atr_5m = calc_atr(candles_5m, 14)

    trades = []
    cooldown = 0

    for i in range(30, len(candles_5m) - 15):
        if cooldown > 0:
            cooldown -= 1
            continue

        c = candles_5m[i]
        atr = atr_5m[i]
        idx_15m = min(int(i / 3), len(candles_15m) - 1)
        htf_trend = 'BULLISH' if ema9_15m[idx_15m] > ema21_15m[idx_15m] else 'BEARISH'

        prior_slice = candles_5m[i - 25 : i]
        prior_high = max(x['high'] for x in prior_slice)
        prior_low = min(x['low'] for x in prior_slice)

        rng = max(c['high'] - c['low'], atr * 0.2)
        lower_wick = min(c['open'], c['close']) - c['low']
        upper_wick = c['high'] - max(c['open'], c['close'])

        side = None
        # Long: HTF Bullish, swept prior low by >= 0.10%, closed back above with hammer wick >= 40%
        if htf_trend == 'BULLISH' and c['low'] < prior_low and (prior_low - c['low']) / prior_low >= 0.0010 and c['close'] > prior_low and (lower_wick / rng >= 0.40):
            side = 'LONG'
        # Short: HTF Bearish, swept prior high by >= 0.10%, closed back below with shooting star wick >= 40%
        elif htf_trend == 'BEARISH' and c['high'] > prior_high and (c['high'] - prior_high) / prior_high >= 0.0010 and c['close'] < prior_high and (upper_wick / rng >= 0.40):
            side = 'SHORT'

        if not side:
            continue

        entry = c['close']
        stop = c['low'] - atr * 0.3 if side == 'LONG' else c['high'] + atr * 0.3
        tp1 = entry * 1.0050 if side == 'LONG' else entry * 0.9950
        tp2 = entry * 1.0120 if side == 'LONG' else entry * 0.9880

        res = simulate_trade(candles_5m, i, side, entry, stop, tp1, tp2, tp1_fraction=0.85, max_bars=8, early_be_pct=0.0020)
        if res:
            trades.append(res)
            cooldown = 5
    return trades

# ─────────────────────────────────────────────────────────────────────────────
# 2. ⚡ 3-SIGMA STATISTICAL MEAN REVERSION ("Rubber Band Sniper")
# ─────────────────────────────────────────────────────────────────────────────
def strat_3sigma_reversion(candles_5m, candles_15m):
    closes = [c['close'] for c in candles_5m]
    vols = [c['volume'] for c in candles_5m]
    smas = calc_sma(closes, 20)
    stdevs = calc_stdev(closes, 20)
    rsi_5m = calc_rsi(closes, 14)
    atr_5m = calc_atr(candles_5m, 14)

    trades = []
    cooldown = 0

    for i in range(25, len(candles_5m) - 15):
        if cooldown > 0:
            cooldown -= 1
            continue

        c = candles_5m[i]
        atr = atr_5m[i]
        sd = stdevs[i]
        if sd <= 0:
            continue
        m = smas[i]
        upper3 = m + 2.8 * sd
        lower3 = m - 2.8 * sd
        rsi = rsi_5m[i]

        avg_vol = sum(vols[i - 15 : i]) / 15.0
        vol_surge = c['volume'] >= 1.5 * avg_vol

        side = None
        # Oversold 3-sigma plunge
        if c['low'] <= lower3 and rsi <= 25 and vol_surge and c['close'] > c['open']:
            side = 'LONG'
        # Overbought 3-sigma explosion
        elif c['high'] >= upper3 and rsi >= 75 and vol_surge and c['close'] < c['open']:
            side = 'SHORT'

        if not side:
            continue

        entry = c['close']
        stop = c['low'] - atr * 0.4 if side == 'LONG' else c['high'] + atr * 0.4
        tp1 = entry * 1.0050 if side == 'LONG' else entry * 0.9950
        tp2 = m  # 20 SMA center

        res = simulate_trade(candles_5m, i, side, entry, stop, tp1, tp2, tp1_fraction=0.90, max_bars=8, early_be_pct=0.0018)
        if res:
            trades.append(res)
            cooldown = 6
    return trades

# ─────────────────────────────────────────────────────────────────────────────
# 3. 🧱 ORDER BLOCK & FAIR VALUE GAP RETEST (Smart Money Concept)
# ─────────────────────────────────────────────────────────────────────────────
def strat_fvg_retest(candles_5m, candles_15m):
    closes_5m = [c['close'] for c in candles_5m]
    closes_15m = [c['close'] for c in candles_15m]
    ema21_15m = calc_ema(closes_15m, 21)
    atr_5m = calc_atr(candles_5m, 14)

    trades = []
    cooldown = 0

    for i in range(25, len(candles_5m) - 15):
        if cooldown > 0:
            cooldown -= 1
            continue

        # Check for FVG pattern across i-2, i-1, i:
        c1, c2, c3 = candles_5m[i - 2], candles_5m[i - 1], candles_5m[i]
        atr = atr_5m[i]
        idx_15m = min(int(i / 3), len(candles_15m) - 1)
        htf_bull = closes_15m[idx_15m] > ema21_15m[idx_15m]
        htf_bear = closes_15m[idx_15m] < ema21_15m[idx_15m]

        side = None
        # Bullish FVG: c2 is large displacement up, c3 low > c1 high
        if htf_bull and (c3['low'] > c1['high']) and (c2['close'] - c2['open'] > atr * 0.8):
            fvg_mid = (c3['low'] + c1['high']) / 2.0
            # Wait for subsequent candle to retest fvg_mid
            for k in range(i + 1, min(i + 4, len(candles_5m))):
                if candles_5m[k]['low'] <= fvg_mid <= candles_5m[k]['high']:
                    side = 'LONG'
                    entry_k = k
                    entry_px = fvg_mid
                    break
        elif htf_bear and (c3['high'] < c1['low']) and (c2['open'] - c2['close'] > atr * 0.8):
            fvg_mid = (c3['high'] + c1['low']) / 2.0
            for k in range(i + 1, min(i + 4, len(candles_5m))):
                if candles_5m[k]['low'] <= fvg_mid <= candles_5m[k]['high']:
                    side = 'SHORT'
                    entry_k = k
                    entry_px = fvg_mid
                    break

        if not side:
            continue

        stop = entry_px * 0.9940 if side == 'LONG' else entry_px * 1.0060
        tp1 = entry_px * 1.0050 if side == 'LONG' else entry_px * 0.9950
        tp2 = entry_px * 1.0120 if side == 'LONG' else entry_px * 0.9880

        res = simulate_trade(candles_5m, entry_k, side, entry_px, stop, tp1, tp2, tp1_fraction=0.85, max_bars=8, early_be_pct=0.0020)
        if res:
            trades.append(res)
            cooldown = 5
    return trades

# ─────────────────────────────────────────────────────────────────────────────
# 4. 🎯 CONSOLIDATION SQUEEZE EXPANSION (Volatility Breakout)
# ─────────────────────────────────────────────────────────────────────────────
def strat_squeeze_expansion(candles_5m, candles_15m):
    closes_5m = [c['close'] for c in candles_5m]
    vols_5m = [c['volume'] for c in candles_5m]
    atr_5m = calc_atr(candles_5m, 14)
    closes_15m = [c['close'] for c in candles_15m]
    ema9_15m = calc_ema(closes_15m, 9)
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
        htf_trend = 'BULLISH' if ema9_15m[idx_15m] > ema21_15m[idx_15m] else 'BEARISH'

        # Squeeze in previous 5 candles (range < 0.45%)
        box = candles_5m[i - 5 : i]
        box_hi = max(x['high'] for x in box)
        box_lo = min(x['low'] for x in box)
        box_rng = (box_hi - box_lo) / box_lo

        if box_rng > 0.0045:
            continue

        avg_vol = sum(vols_5m[i - 12 : i]) / 12.0
        vol_surge = c['volume'] >= 1.6 * avg_vol

        side = None
        if htf_trend == 'BULLISH' and vol_surge and c['close'] > box_hi:
            side = 'LONG'
        elif htf_trend == 'BEARISH' and vol_surge and c['close'] < box_lo:
            side = 'SHORT'

        if not side:
            continue

        entry = c['close']
        stop = box_lo - atr * 0.2 if side == 'LONG' else box_hi + atr * 0.2
        tp1 = entry * 1.0045 if side == 'LONG' else entry * 0.9955
        tp2 = entry * 1.0100 if side == 'LONG' else entry * 0.9900

        res = simulate_trade(candles_5m, i, side, entry, stop, tp1, tp2, tp1_fraction=0.90, max_bars=7, early_be_pct=0.0018)
        if res:
            trades.append(res)
            cooldown = 5
    return trades

# ─────────────────────────────────────────────────────────────────────────────
# 5. 🔄 SESSION RANGE LIQUIDITY ROTATION (Asian Sweep at London/NY)
# ─────────────────────────────────────────────────────────────────────────────
def strat_session_sweep_rotation(candles_5m, candles_15m):
    closes_5m = [c['close'] for c in candles_5m]
    atr_5m = calc_atr(candles_5m, 14)
    trades = []
    cooldown = 0

    for i in range(35, len(candles_5m) - 15):
        if cooldown > 0:
            cooldown -= 1
            continue

        c = candles_5m[i]
        atr = atr_5m[i]

        # 30-period session box (~2.5 hours)
        session = candles_5m[i - 30 : i]
        sess_hi = max(x['high'] for x in session)
        sess_lo = min(x['low'] for x in session)
        sess_mid = (sess_hi + sess_lo) / 2.0

        rng = max(c['high'] - c['low'], atr * 0.2)
        lower_wick = min(c['open'], c['close']) - c['low']
        upper_wick = c['high'] - max(c['open'], c['close'])

        side = None
        # Swept session low, closed back inside
        if c['low'] < sess_lo and c['close'] > sess_lo and (lower_wick / rng >= 0.40):
            side = 'LONG'
        # Swept session high, closed back inside
        elif c['high'] > sess_hi and c['close'] < sess_hi and (upper_wick / rng >= 0.40):
            side = 'SHORT'

        if not side:
            continue

        entry = c['close']
        stop = c['low'] - atr * 0.25 if side == 'LONG' else c['high'] + atr * 0.25
        tp1 = entry * 1.0045 if side == 'LONG' else entry * 0.9955
        tp2 = sess_mid

        res = simulate_trade(candles_5m, i, side, entry, stop, tp1, tp2, tp1_fraction=0.85, max_bars=8, early_be_pct=0.0018)
        if res:
            trades.append(res)
            cooldown = 6
    return trades

# ─────────────────────────────────────────────────────────────────────────────
# 6. 📊 RSI DIVERGENCE CLIMAX (Double Bottom / Top Divergence)
# ─────────────────────────────────────────────────────────────────────────────
def strat_rsi_divergence(candles_5m, candles_15m):
    closes = [c['close'] for c in candles_5m]
    rsi = calc_rsi(closes, 14)
    atr_5m = calc_atr(candles_5m, 14)
    trades = []
    cooldown = 0

    for i in range(25, len(candles_5m) - 15):
        if cooldown > 0:
            cooldown -= 1
            continue

        c = candles_5m[i]
        atr = atr_5m[i]

        # Prior local low/high ~8-15 bars ago
        slice_bars = candles_5m[i - 15 : i - 5]
        prior_min_idx = min(range(len(slice_bars)), key=lambda k: slice_bars[k]['low'])
        prior_min_bar = slice_bars[prior_min_idx]
        prior_min_rsi = rsi[i - 15 + prior_min_idx]

        prior_max_idx = max(range(len(slice_bars)), key=lambda k: slice_bars[k]['high'])
        prior_max_bar = slice_bars[prior_max_idx]
        prior_max_rsi = rsi[i - 15 + prior_max_idx]

        side = None
        # Bullish divergence: price made lower low, but RSI made higher low (and RSI < 40)
        if c['low'] < prior_min_bar['low'] and rsi[i] > prior_min_rsi + 3.0 and rsi[i] <= 42 and c['close'] > c['open']:
            side = 'LONG'
        # Bearish divergence: price made higher high, but RSI made lower high (and RSI > 60)
        elif c['high'] > prior_max_bar['high'] and rsi[i] < prior_max_rsi - 3.0 and rsi[i] >= 58 and c['close'] < c['open']:
            side = 'SHORT'

        if not side:
            continue

        entry = c['close']
        stop = c['low'] - atr * 0.35 if side == 'LONG' else c['high'] + atr * 0.35
        tp1 = entry * 1.0050 if side == 'LONG' else entry * 0.9950
        tp2 = entry * 1.0110 if side == 'LONG' else entry * 0.9890

        res = simulate_trade(candles_5m, i, side, entry, stop, tp1, tp2, tp1_fraction=0.90, max_bars=8, early_be_pct=0.0020)
        if res:
            trades.append(res)
            cooldown = 5
    return trades

# ─────────────────────────────────────────────────────────────────────────────
# 7. 🛡️ MOVING AVERAGE RIBBON SQUEEZE (EMA 9/21/50 Compression Break)
# ─────────────────────────────────────────────────────────────────────────────
def strat_ribbon_compression(candles_5m, candles_15m):
    closes = [c['close'] for c in candles_5m]
    vols = [c['volume'] for c in candles_5m]
    ema9 = calc_ema(closes, 9)
    ema21 = calc_ema(closes, 21)
    ema50 = calc_ema(closes, 50)
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

        # Check ribbon compression: max distance between 9, 21, 50 EMA is < 0.20%
        spread = (max(ema9[i], ema21[i], ema50[i]) - min(ema9[i], ema21[i], ema50[i])) / c['close']
        if spread > 0.0025:
            continue

        avg_vol = sum(vols[i - 12 : i]) / 12.0
        vol_surge = c['volume'] >= 1.5 * avg_vol

        side = None
        # Expansion up
        if vol_surge and c['close'] > max(ema9[i], ema21[i], ema50[i]) and c['close'] > p['high']:
            side = 'LONG'
        # Expansion down
        elif vol_surge and c['close'] < min(ema9[i], ema21[i], ema50[i]) and c['close'] < p['low']:
            side = 'SHORT'

        if not side:
            continue

        entry = c['close']
        stop = min(ema9[i], ema21[i], ema50[i]) - atr * 0.3 if side == 'LONG' else max(ema9[i], ema21[i], ema50[i]) + atr * 0.3
        tp1 = entry * 1.0045 if side == 'LONG' else entry * 0.9955
        tp2 = entry * 1.0100 if side == 'LONG' else entry * 0.9900

        res = simulate_trade(candles_5m, i, side, entry, stop, tp1, tp2, tp1_fraction=0.90, max_bars=7, early_be_pct=0.0018)
        if res:
            trades.append(res)
            cooldown = 5
    return trades

# ─────────────────────────────────────────────────────────────────────────────
# 8. 💎 VOLUME DELTA ABSORPTION FLOOR ("Whale Wall")
# ─────────────────────────────────────────────────────────────────────────────
def strat_whale_wall(candles_5m, candles_15m):
    closes = [c['close'] for c in candles_5m]
    vols = [c['volume'] for c in candles_5m]
    atr_5m = calc_atr(candles_5m, 14)
    trades = []
    cooldown = 0

    for i in range(25, len(candles_5m) - 15):
        if cooldown > 0:
            cooldown -= 1
            continue

        c1 = candles_5m[i - 1]
        c2 = candles_5m[i]
        atr = atr_5m[i]

        avg_vol = sum(vols[i - 12 : i]) / 12.0
        high_vol = (c1['volume'] >= 1.4 * avg_vol) and (c2['volume'] >= 1.4 * avg_vol)
        if not high_vol:
            continue

        rng1 = max(c1['high'] - c1['low'], atr * 0.2)
        rng2 = max(c2['high'] - c2['low'], atr * 0.2)
        lw1 = min(c1['open'], c1['close']) - c1['low']
        lw2 = min(c2['open'], c2['close']) - c2['low']
        uw1 = c1['high'] - max(c1['open'], c1['close'])
        uw2 = c2['high'] - max(c2['open'], c2['close'])

        side = None
        # Double absorption floor: heavy volume absorbed at bottom wicks
        if (lw1 / rng1 >= 0.35) and (lw2 / rng2 >= 0.35) and abs(c1['low'] - c2['low']) / c2['low'] <= 0.0015 and c2['close'] > c2['open']:
            side = 'LONG'
        # Double absorption ceiling: heavy volume absorbed at top wicks
        elif (uw1 / rng1 >= 0.35) and (uw2 / rng2 >= 0.35) and abs(c1['high'] - c2['high']) / c2['high'] <= 0.0015 and c2['close'] < c2['open']:
            side = 'SHORT'

        if not side:
            continue

        entry = c2['close']
        stop = min(c1['low'], c2['low']) - atr * 0.3 if side == 'LONG' else max(c1['high'], c2['high']) + atr * 0.3
        tp1 = entry * 1.0045 if side == 'LONG' else entry * 0.9955
        tp2 = entry * 1.0100 if side == 'LONG' else entry * 0.9900

        res = simulate_trade(candles_5m, i, side, entry, stop, tp1, tp2, tp1_fraction=0.90, max_bars=7, early_be_pct=0.0018)
        if res:
            trades.append(res)
            cooldown = 5
    return trades

def main():
    print("=" * 90)
    print("  TESTING 8 INVENTED STRATEGY ARCHETYPES ON BYBIT PERPETUALS (5M/15M TAPES)")
    print(f"  Cost model: {MAKER_COST_BPS} bps (Maker limit fill) | $10 margin @ 10x leverage ($100 notional)")
    print("=" * 90)

    dataset = {}
    print("\nFetching latest klines across 5 major liquid coins...")
    for sym in SYMBOLS:
        c5m = fetch_klines(sym, '5', limit=1000)
        c15m = fetch_klines(sym, '15', limit=500)
        dataset[sym] = {'5m': c5m, '15m': c15m}
        time.sleep(0.12)
    print("Data download completed.\n")

    strategies = {
        '1. Liquidity Sweep & Retest (Ghost Hunter)': strat_liquidity_sweep_retest,
        '2. 3-Sigma Mean Reversion (Rubber Band)': strat_3sigma_reversion,
        '3. Order Block & FVG Retest (Smart Money)': strat_fvg_retest,
        '4. Squeeze Volatility Breakout': strat_squeeze_expansion,
        '5. Session Range Liquidity Rotation': strat_session_sweep_rotation,
        '6. RSI Divergence Climax (Double Reversal)': strat_rsi_divergence,
        '7. MA Ribbon Compression Expansion': strat_ribbon_compression,
        '8. Volume Delta Absorption Wall (Whale Wall)': strat_whale_wall
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

    header = f"{'Strategy Archetype':<46} | {'Trades':<6} | {'Win %':<6} | {'NoLoss%':<7} | {'PF':<5} | {'Total $':<8} | {'Per 100':<8} | {'Hold':<5}"
    print(header)
    print("-" * len(header))

    for name, r in results.items():
        if r['trades'] == 0:
            print(f"{name:<46} | 0 trades")
            continue
        print(f"{name:<46} | {r['trades']:<6} | {r['win_rate']:<5.1f}% | {r['no_loss_rate']:<6.1f}% | {r['profit_factor']:<5.2f} | ${r['total_net_pnl']:<7.2f} | ${r['proj_100_pnl']:<7.2f} | {r['avg_hold_mins']:<3.0f}m")

    with open('scratch/more_strategies_results.json', 'w', encoding='utf-8') as f:
        json.dump(results, f, indent=2)

if __name__ == '__main__':
    main()
