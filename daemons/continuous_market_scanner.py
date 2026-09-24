import os
import sys
import json
import time
import math
import urllib.request
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backend_lib.bybit_client import BybitDemoClient
from daemons.deepseek_profit_claimer import SmartProfitClaimer

_ENV_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env')
env = {}
if os.path.exists(_ENV_PATH):
    with open(_ENV_PATH, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if '=' in line and not line.startswith('#'):
                k, v = line.split('=', 1)
                env[k.strip()] = v.strip().strip("'\"")

bybit_key = os.environ.get('BYBIT_API_KEY') or env.get('BYBIT_API_KEY', '')
bybit_secret = os.environ.get('BYBIT_API_SECRET') or env.get('BYBIT_API_SECRET', '')
bybit_base = os.environ.get('BYBIT_BASE_URL') or env.get('BYBIT_BASE_URL', 'https://api-demo.bybit.com')

client = BybitDemoClient(bybit_key, bybit_secret, bybit_base)
claimer = SmartProfitClaimer(client)

# 24-coin universe — Large caps, Mid caps, Memecoins (~$11 notional per trade)
COIN_CONFIG = {
    # ── Large Caps ────────────────────────────────────────────────────────
    'BTCUSDT':  {'qty': 0.00013, 'p_dec': 1, 'q_dec': 5, 'min_step': 0.00001},  # ~$11 @ $85k
    'ETHUSDT':  {'qty': 0.004,   'p_dec': 2, 'q_dec': 3, 'min_step': 0.001},    # ~$11 @ $2700
    'BNBUSDT':  {'qty': 0.02,    'p_dec': 2, 'q_dec': 2, 'min_step': 0.01},     # ~$11 @ $570
    # ── Layer 1 / Layer 2 ────────────────────────────────────────────────
    'SOLUSDT':  {'qty': 0.1,    'p_dec': 2, 'q_dec': 1, 'min_step': 0.01},      # ~$11 @ $130
    'AVAXUSDT': {'qty': 1.3,    'p_dec': 3, 'q_dec': 1, 'min_step': 0.001},     # ~$11 @ $25
    'NEARUSDT': {'qty': 2.9,    'p_dec': 3, 'q_dec': 1, 'min_step': 0.001},     # ~$11 @ $4
    'SUIUSDT':  {'qty': 13,     'p_dec': 4, 'q_dec': 0, 'min_step': 0.0001},    # ~$11 @ $0.85
    'APTUSDT':  {'qty': 1.5,    'p_dec': 3, 'q_dec': 1, 'min_step': 0.001},     # ~$11 @ $7.5
    'TIAUSDT':  {'qty': 2.2,    'p_dec': 3, 'q_dec': 1, 'min_step': 0.001},     # ~$11 @ $5
    'SEIUSDT':  {'qty': 25,     'p_dec': 4, 'q_dec': 0, 'min_step': 0.0001},    # ~$11 @ $0.44
    # ── DeFi / Ecosystem ─────────────────────────────────────────────────
    'LINKUSDT': {'qty': 0.9,    'p_dec': 3, 'q_dec': 1, 'min_step': 0.001},     # ~$11 @ $12
    'INJUSDT':  {'qty': 0.5,    'p_dec': 3, 'q_dec': 1, 'min_step': 0.001},     # ~$11 @ $22
    'JUPUSDT':  {'qty': 16,     'p_dec': 4, 'q_dec': 0, 'min_step': 0.0001},    # ~$11 @ $0.70
    'STRKUSDT': {'qty': 20,     'p_dec': 4, 'q_dec': 0, 'min_step': 0.0001},    # ~$11 @ $0.55
    # ── L2 Ecosystem ─────────────────────────────────────────────────────
    'OPUSDT':   {'qty': 9,      'p_dec': 3, 'q_dec': 0, 'min_step': 0.0001},    # ~$11 @ $1.2
    'ARBUSDT':  {'qty': 14,     'p_dec': 4, 'q_dec': 0, 'min_step': 0.0001},    # ~$11 @ $0.78
    'MATICUSDT':{'qty': 17,     'p_dec': 4, 'q_dec': 0, 'min_step': 0.0001},    # ~$11 @ $0.65
    # ── Payments / Utility ───────────────────────────────────────────────
    'XRPUSDT':  {'qty': 15,     'p_dec': 4, 'q_dec': 0, 'min_step': 0.0001},    # ~$11 @ $0.73
    'ADAUSDT':  {'qty': 47,     'p_dec': 4, 'q_dec': 0, 'min_step': 0.0001},    # ~$11 @ $0.35
    'DOGEUSDT': {'qty': 120,    'p_dec': 5, 'q_dec': 0, 'min_step': 0.00001},   # ~$11 @ $0.13
    # ── Memecoins (High Volatility) ──────────────────────────────────────
    'WIFUSDT':  {'qty': 12,     'p_dec': 4, 'q_dec': 0, 'min_step': 0.0001},    # ~$11 @ $0.93
    'PEPEUSDT': {'qty': 2500000,'p_dec': 8, 'q_dec': 0, 'min_step': 0.0000001}, # ~$11 @ $0.0000044
    'BONKUSDT': {'qty': 1200000,'p_dec': 8, 'q_dec': 0, 'min_step': 0.0000001}, # ~$11 @ $0.0000093
    'FLOKIUSDT':{'qty': 450000, 'p_dec': 8, 'q_dec': 0, 'min_step': 0.0000001}, # ~$11 @ $0.000025
}


_ROOT     = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_FILE  = os.path.join(_ROOT, 'scratch', 'scanner_live.log')
STATE_FILE = os.path.join(_ROOT, 'scratch', 'live_market_state.json')

def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    safe = msg.encode('ascii', errors='replace').decode('ascii')
    out = f"[{ts}] {safe}"
    print(out, flush=True)
    with open(LOG_FILE, 'a', encoding='utf-8') as f:
        f.write(f"[{ts}] {msg}\n")

def fetch_klines(symbol, interval, limit=25):
    url = f"https://api-demo.bybit.com/v5/market/kline?category=linear&symbol={symbol}&interval={interval}&limit={limit}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "MASIS/3.0"})
        with urllib.request.urlopen(req, timeout=4) as r:
            data = json.loads(r.read().decode())
            raw = data.get("result", {}).get("list", [])
            candles = []
            for row in reversed(raw):
                candles.append({
                    "open": float(row[1]),
                    "high": float(row[2]),
                    "low": float(row[3]),
                    "close": float(row[4]),
                    "vol": float(row[5])
                })
            return candles
    except Exception:
        return []

def calc_ema(closes, period):
    if len(closes) < period:
        return closes[-1] if closes else 0
    k = 2 / (period + 1)
    ema = sum(closes[:period]) / period
    for c in closes[period:]:
        ema = c * k + ema * (1 - k)
    return ema

def calc_rsi(closes, period=14):
    if len(closes) < period + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i-1]
        gains.append(max(0, diff))
        losses.append(max(0, -diff))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))

def calc_displacement_efficiency(open_p, high_p, low_p, close_p, direction):
    """
    Directional Displacement Efficiency (D):
    D = (Directional Displacement) / (Total Candle Range).
    For BUY:  (close - open) / (high - low)
    For SELL: (open - close) / (high - low)
    Values: -1.0 to +1.0.
    High value (> 0.40): Strong directional continuation.
    Low value (< 0.20) with high volume: Absorption trap (heavy struggle, zero progress).
    """
    c_range = max(1e-5, high_p - low_p)
    if direction == "BUY":
        return (close_p - open_p) / c_range
    elif direction == "SELL":
        return (open_p - close_p) / c_range
    return 0.0

def calc_volume_persistence(klines):
    """
    Calculates Volume Persistence across recent 3 candles (V0 = latest, V1 = prev, V2 = two back):
    persistence = min(V0 / V1, V1 / V2)
    Distinguishes genuine volume momentum from one-off spike anomalies.
    """
    if len(klines) < 3:
        return 1.0
    v0 = max(1e-5, klines[-1]['vol'])
    v1 = max(1e-5, klines[-2]['vol'])
    v2 = max(1e-5, klines[-3]['vol'])
    return round(min(v0 / v1, v1 / v2), 2)

def check_5m_confirmation(k5, direction):
    """
    5-Minute Post-Signal Confirmation Gate:
    Requires the market to actively confirm continuation after the setup candle printed.
    
    SHORT Confirmation:
    - Close < previous close OR close in lower half of 5m candle range
    - 5m candle body is bearish (close <= open * 1.0005)
    - No large lower-wick rejection (l_wick_pct_5 < 0.25)
    
    LONG Confirmation:
    - Close > previous close OR close in upper half of 5m candle range
    - 5m candle body is bullish (close >= open * 0.9995)
    - No large upper-wick rejection (u_wick_pct_5 < 0.25)
    """
    if not k5 or len(k5) < 3:
        return True, "INSUFFICIENT_5M_DATA"

    last_5 = k5[-1]
    prev_5 = k5[-2]
    c_range = max(1e-5, last_5['high'] - last_5['low'])
    lower_wick = min(last_5['open'], last_5['close']) - last_5['low']
    upper_wick = last_5['high'] - max(last_5['open'], last_5['close'])
    l_wick_pct = lower_wick / c_range
    u_wick_pct = upper_wick / c_range

    if direction == "SELL":
        close_in_lower = (last_5['high'] - last_5['close']) / c_range >= 0.50
        close_below_prev = last_5['close'] <= prev_5['close']
        bearish_body = last_5['close'] <= last_5['open'] * 1.0005
        clean_bottom = l_wick_pct < 0.25

        if (close_below_prev or close_in_lower) and bearish_body and clean_bottom:
            return True, "5M_BEARISH_CONFIRMED"
        else:
            reasons = []
            if not (close_below_prev or close_in_lower):
                reasons.append("close_not_in_lower_half")
            if not bearish_body:
                reasons.append("candle_body_is_bullish_green")
            if not clean_bottom:
                reasons.append(f"lower_wick_absorption_{l_wick_pct*100:.0f}%")
            return False, "SHORT_WAITING_5M_CONFIRMATION: " + ", ".join(reasons)

    elif direction == "BUY":
        close_in_upper = (last_5['close'] - last_5['low']) / c_range >= 0.50
        close_above_prev = last_5['close'] >= prev_5['close']
        bullish_body = last_5['close'] >= last_5['open'] * 0.9995
        clean_top = u_wick_pct < 0.25

        if (close_above_prev or close_in_upper) and bullish_body and clean_top:
            return True, "5M_BULLISH_CONFIRMED"
        else:
            reasons = []
            if not (close_above_prev or close_in_upper):
                reasons.append("close_not_in_upper_half")
            if not bullish_body:
                reasons.append("candle_body_is_bearish_red")
            if not clean_top:
                reasons.append(f"upper_wick_resistance_{u_wick_pct*100:.0f}%")
            return False, "LONG_WAITING_5M_CONFIRMATION: " + ", ".join(reasons)

    return False, "UNKNOWN_DIRECTION"

def scan_symbol(symbol):
    k15 = fetch_klines(symbol, '15', 25)
    k5  = fetch_klines(symbol, '5', 25)
    if not k15 or not k5 or len(k15) < 15 or len(k5) < 15:
        return None
        
    c15 = [c['close'] for c in k15]
    c5  = [c['close'] for c in k5]
    
    cur_p = c5[-1]
    ema9_15  = calc_ema(c15, 9)
    ema21_15 = calc_ema(c15, 21)
    ema9_5   = calc_ema(c5, 9)
    ema21_5  = calc_ema(c5, 21)
    rsi5     = calc_rsi(c5, 14)
    rsi15    = calc_rsi(c15, 14)
    
    last_5 = k5[-1]
    c_range = max(0.0001, last_5['high'] - last_5['low'])
    lower_wick = min(last_5['open'], last_5['close']) - last_5['low']
    upper_wick = last_5['high'] - max(last_5['open'], last_5['close'])
    l_wick_pct = lower_wick / c_range
    u_wick_pct = upper_wick / c_range
    
    avg_vol = sum(c['vol'] for c in k5[-8:-1]) / 7.0 if len(k5) >= 8 else 1.0
    vol_ratio = last_5['vol'] / avg_vol if avg_vol > 0 else 1.0
    
    trend_15m = "BULL" if ema9_15 > ema21_15 else "BEAR"
    trend_5m  = "BULL" if ema9_5 > ema21_5 else "BEAR"
    
    # ── Bidirectional Tactical Micro-Scalp Scanner ──
    bull_scalp_score = 0
    if trend_15m == "BULL":
        bull_scalp_score += 35
        if trend_5m == "BULL":
            bull_scalp_score += 20
        if 40 <= rsi5 <= 65:
            bull_scalp_score += 20  # Pullback value zone
        if l_wick_pct >= 0.20:
            bull_scalp_score += 15  # Buyer absorption
        if vol_ratio >= 1.15:
            bull_scalp_score += 10  # Momentum volume

    bear_scalp_score = 0
    if trend_15m == "BEAR":
        bear_scalp_score += 35
        if trend_5m == "BEAR":
            bear_scalp_score += 20
        if 35 <= rsi5 <= 62:
            bear_scalp_score += 20  # Resistance retest zone
        if u_wick_pct >= 0.20:
            bear_scalp_score += 15  # Seller rejection
        if vol_ratio >= 1.15:
            bear_scalp_score += 10  # Breakdown volume

    if bull_scalp_score >= bear_scalp_score and bull_scalp_score >= 50:
        score = bull_scalp_score
        setup = "MICRO_SCALP_LONG"
        direction = "BUY"
    elif bear_scalp_score > bull_scalp_score and bear_scalp_score >= 50:
        score = bear_scalp_score
        setup = "MICRO_SCALP_SHORT"
        direction = "SELL"
    else:
        score = max(bull_scalp_score, bear_scalp_score)
        setup = "NONE"
        direction = "NONE"

    disp_5m = calc_displacement_efficiency(last_5['open'], last_5['high'], last_5['low'], last_5['close'], direction) if direction != "NONE" else 0.0
    vol_persist = calc_volume_persistence(k5)
    
    # Absorption Trap Detection: High Volume + Low Displacement Efficiency
    is_absorption_trap = (vol_ratio >= 1.60 and disp_5m < 0.20 and direction != "NONE")
    trap_warning = ""
    if is_absorption_trap:
        score = max(0, score - 35)
        trap_warning = f"ABSORPTION_TRAP: Volume {vol_ratio:.1f}x with zero displacement ({disp_5m:.2f})"

    is_confirmed, conf_note = check_5m_confirmation(k5, direction) if direction != "NONE" else (False, "NO_SETUP")
    if direction != "NONE" and not is_confirmed:
        score = min(score, 70)

    recommended = (score >= 75 and is_confirmed and not is_absorption_trap)

    return {
        "symbol": symbol,
        "price": cur_p,
        "strategy_tier": "MICRO_SCALP",
        "trend_15m": trend_15m,
        "trend_5m": trend_5m,
        "rsi_5m": round(rsi5, 1),
        "rsi_15m": round(rsi15, 1),
        "vol_ratio": round(vol_ratio, 2),
        "vol_persistence": vol_persist,
        "disp_5m": round(disp_5m, 2),
        "lower_wick_pct": round(l_wick_pct * 100, 1),
        "upper_wick_pct": round(u_wick_pct * 100, 1),
        "score": score,
        "is_confirmed": is_confirmed,
        "confirmation_note": conf_note,
        "is_absorption_trap": is_absorption_trap,
        "trap_warning": trap_warning,
        "setup": setup,
        "direction": direction,
        "recommended": recommended
    }

def scan_symbol_htf_swing(symbol, btc_macro=None):
    """
    High-Timeframe (HTF) Swing Runner Scanner:
    Analyzes 1h (Macro Trend), 15m (Value Pullback Zone), and 5m (Entry Timing).
    Targets $1.00+ profit per trade across both Longs and Shorts.
    Loosened from rigid 92 gate to 78+ for flexible multi-setup execution.
    """
    k1h  = fetch_klines(symbol, '60', 30)
    k15  = fetch_klines(symbol, '15', 30)
    k5   = fetch_klines(symbol, '5', 20)
    if not k1h or not k15 or not k5 or len(k1h) < 15 or len(k15) < 15 or len(k5) < 10:
        return None

    c1h = [c['close'] for c in k1h]
    c15 = [c['close'] for c in k15]
    c5  = [c['close'] for c in k5]

    cur_p = c5[-1]

    # 1. 1-Hour Macro Trend & Momentum
    ema20_1h = calc_ema(c1h, 20)
    ema50_1h = calc_ema(c1h, 50)
    rsi_1h   = calc_rsi(c1h, 14)
    trend_1h = "BULL" if (ema20_1h > ema50_1h and cur_p >= ema20_1h * 0.995) else "BEAR"

    # 2. 15-Minute Structural Trend & Pullback
    ema21_15 = calc_ema(c15, 21)
    ema50_15 = calc_ema(c15, 50)
    rsi_15   = calc_rsi(c15, 14)
    trend_15m = "BULL" if ema21_15 > ema50_15 else "BEAR"

    last_15 = k15[-1]
    range_15 = max(0.0001, last_15['high'] - last_15['low'])
    lower_wick_15 = min(last_15['open'], last_15['close']) - last_15['low']
    upper_wick_15 = last_15['high'] - max(last_15['open'], last_15['close'])
    l_wick_pct_15 = lower_wick_15 / range_15
    u_wick_pct_15 = upper_wick_15 / range_15

    avg_vol_15 = sum(c['vol'] for c in k15[-9:-1]) / 8.0 if len(k15) >= 9 else 1.0
    vol_ratio_15 = last_15['vol'] / avg_vol_15 if avg_vol_15 > 0 else 1.0

    # 3. 5-Minute Entry Trigger & Clean Rejection
    ema9_5 = calc_ema(c5, 9)
    last_5 = k5[-1]
    range_5 = max(0.0001, last_5['high'] - last_5['low'])
    upper_wick_5 = last_5['high'] - max(last_5['open'], last_5['close'])
    lower_wick_5 = min(last_5['open'], last_5['close']) - last_5['low']
    u_wick_pct_5 = upper_wick_5 / range_5
    l_wick_pct_5 = lower_wick_5 / range_5

    score = 0
    setup = "NONE"
    direction = "NONE"
    targets = {}

    # ── 1. Bullish Trend Setup (HTF_SWING_PULLBACK_LONG) ──
    pullback_long_score = 0
    if trend_1h == "BULL":
        pullback_long_score += 30  # 1h Macro alignment
        if 44 <= rsi_1h <= 68:
            pullback_long_score += 15  # Solid trend momentum, not overbought
        if trend_15m == "BULL":
            pullback_long_score += 15  # 15m trend alignment
        if 38 <= rsi_15 <= 60:
            pullback_long_score += 15  # 15m Pullback into Value Zone
        if l_wick_pct_15 >= 0.18:
            pullback_long_score += 10  # 15m Wick Absorption
        if vol_ratio_15 >= 1.10:
            pullback_long_score += 10  # 15m Volume confirmation
        if cur_p >= ema9_5 and u_wick_pct_5 < 0.25:
            pullback_long_score += 5   # 5m Trigger
        if vol_ratio_15 >= 1.70:
            pullback_long_score += 5

    # ── 2. Bullish Reversal Setup (HTF_SWING_OVERSOLD_BOUNCE_LONG) ──
    # Identifies deep bottoms/dips where price is oversold and buyers absorb liquidity for a +3.5% to +5.0% swing bounce
    reversal_long_score = 0
    if rsi_15 <= 36 or rsi_1h <= 38:
        reversal_long_score += 30  # Deep oversold condition
        if l_wick_pct_15 >= 0.20:
            reversal_long_score += 20  # Strong buyer wick defense
        if l_wick_pct_5 >= 0.25 or cur_p >= ema9_5:
            reversal_long_score += 15  # 5m bottom stabilization
        if vol_ratio_15 >= 1.15:
            reversal_long_score += 15  # Volume absorption at support
        if rsi_15 < 28:
            reversal_long_score += 10  # Extreme exhaustion bonus
        if u_wick_pct_15 < 0.25:
            reversal_long_score += 10  # Clean floor with minimal upper resistance

    # ── 3. Bearish Trend Setup (HTF_SWING_BREAKDOWN_SHORT) ──
    breakdown_short_score = 0
    trend_1h_bear = (ema20_1h < ema50_1h or cur_p <= ema20_1h * 1.008)
    trend_15m_bear = (ema21_15 < ema50_15 or cur_p <= ema21_15 * 1.005)
    if trend_1h_bear:
        breakdown_short_score += 30  # 1h Macro alignment
        if 30 <= rsi_1h <= 58:
            breakdown_short_score += 15  # Bearish momentum, not oversold
        if trend_15m_bear:
            breakdown_short_score += 15  # 15m trend alignment
        if 40 <= rsi_15 <= 65:
            breakdown_short_score += 15  # 15m Retest into Value Resistance Zone
        if u_wick_pct_15 >= 0.18:
            breakdown_short_score += 10  # 15m Upper Wick Rejection
        if vol_ratio_15 >= 1.10:
            breakdown_short_score += 10  # 15m Volume confirmation on breakdown
        if cur_p <= ema9_5 and l_wick_pct_5 < 0.25:
            breakdown_short_score += 5   # 5m Trigger
        if vol_ratio_15 >= 1.70:
            breakdown_short_score += 5

    # ── 4. Bearish Exhaustion Setup (HTF_SWING_OVERBOUGHT_REJECTION_SHORT) ──
    reversal_short_score = 0
    if rsi_15 >= 66 or rsi_1h >= 64:
        reversal_short_score += 30  # Overbought exhaustion
        if u_wick_pct_15 >= 0.20:
            reversal_short_score += 20  # Seller overhang rejection wick
        if u_wick_pct_5 >= 0.25 or cur_p <= ema9_5:
            reversal_short_score += 15  # 5m top rollover
        if vol_ratio_15 >= 1.15:
            reversal_short_score += 15  # Selling volume expansion
        if rsi_15 > 72:
            reversal_short_score += 10  # Blow-off top bonus
        if l_wick_pct_15 < 0.25:
            reversal_short_score += 10  # Minimal buyer absorption

    # ── Bitcoin Macro & Altcoin Sensitivity Guard ──
    # Modulates confidence based on BTC tailwinds/headwinds without blanket wiping independent setups
    if btc_macro:
        btc_severe_flush = btc_macro.get('is_severe_flush') or btc_macro.get('btc_chg_5m', 0) < -0.35
        btc_soft_dip = btc_macro.get('btc_chg_5m', 0) < -0.15
        btc_pumping = btc_macro.get('regime') == 'BTC_BULL_PUMPING' or btc_macro.get('btc_chg_1h', 0) > 0.30

        if btc_severe_flush:
            pullback_long_score = max(0, pullback_long_score - 30)
            reversal_long_score = max(0, reversal_long_score - 20)
            breakdown_short_score = min(100, breakdown_short_score + 15)
        elif btc_soft_dip:
            pullback_long_score = max(0, pullback_long_score - 10)
            breakdown_short_score = min(100, breakdown_short_score + 5)

        if btc_pumping:
            pullback_long_score = min(100, pullback_long_score + 10)
            reversal_long_score = min(100, reversal_long_score + 10)
            breakdown_short_score = max(0, breakdown_short_score - 20)
            reversal_short_score = max(0, reversal_short_score - 15)

    # ── Select the highest-probability candidate based on market data ──
    candidates_scored = [
        (pullback_long_score, "HTF_SWING_PULLBACK_LONG", "BUY", {"sl_pct": -1.50, "tp1_pct": 2.20, "tp2_pct": 4.00, "tp3_pct": 6.50}),
        (reversal_long_score, "HTF_SWING_OVERSOLD_BOUNCE_LONG", "BUY", {"sl_pct": -1.35, "tp1_pct": 2.00, "tp2_pct": 3.85, "tp3_pct": 5.50}),
        (breakdown_short_score, "HTF_SWING_BREAKDOWN_SHORT", "SELL", {"sl_pct": 1.35, "tp1_pct": -1.80, "tp2_pct": -3.50, "tp3_pct": -5.00}),
        (reversal_short_score, "HTF_SWING_OVERBOUGHT_REJECTION_SHORT", "SELL", {"sl_pct": 1.35, "tp1_pct": -1.80, "tp2_pct": -3.85, "tp3_pct": -5.50}),
    ]

    best_cand = max(candidates_scored, key=lambda c: c[0])
    if best_cand[0] > 0:
        score = best_cand[0]
        setup = best_cand[1]
        direction = best_cand[2]
        targets = best_cand[3]
    else:
        score = 0
        setup = "NONE"
        direction = "NONE"
        targets = {}

    disp_15m = calc_displacement_efficiency(last_15['open'], last_15['high'], last_15['low'], last_15['close'], direction) if direction != "NONE" else 0.0
    disp_5m = calc_displacement_efficiency(last_5['open'], last_5['high'], last_5['low'], last_5['close'], direction) if direction != "NONE" else 0.0
    vol_persist_15m = calc_volume_persistence(k15)

    # 1. Absorption Trap Detection (Attacks LINK/XRP false breakout failures)
    # High volume (>= 1.60x) with near-zero displacement (< 0.20) = absorption/exhaustion trap
    is_absorption_trap = (vol_ratio_15 >= 1.60 and disp_15m < 0.20 and direction != "NONE")
    trap_warning = ""
    if is_absorption_trap:
        score = max(0, score - 35)
        trap_warning = f"ABSORPTION_TRAP: Volume {vol_ratio_15:.1f}x with zero displacement ({disp_15m:.2f})"

    # 2. 5-Minute Post-Signal Confirmation Gate
    is_confirmed, conf_note = check_5m_confirmation(k5, direction) if direction != "NONE" else (False, "NO_SETUP")
    if direction != "NONE" and not is_confirmed:
        # Candidate has setup but has NOT yet confirmed continuation on 5m bar
        score = min(score, 74)  # Hard cap below recommendation threshold (78) until 5m confirms!

    # 3. Trade Readiness Score (Separates Setup Score from Execution Readiness)
    readiness_score = score
    if is_confirmed and not is_absorption_trap and vol_persist_15m >= 0.70 and disp_15m >= 0.30:
        readiness_score = min(100, score + 10)
    elif not is_confirmed or is_absorption_trap:
        readiness_score = min(readiness_score, 60)

    recommended = (score >= 78 and is_confirmed and not is_absorption_trap)

    return {
        "symbol": symbol,
        "price": cur_p,
        "strategy_tier": "HTF_SWING",
        "timeframe": "1h+15m+5m",
        "trend_1h": trend_1h,
        "trend_15m": trend_15m,
        "rsi_1h": round(rsi_1h, 1),
        "rsi_15m": round(rsi_15, 1),
        "vol_ratio": round(vol_ratio_15, 2),
        "vol_persistence": vol_persist_15m,
        "disp_15m": round(disp_15m, 2),
        "disp_5m": round(disp_5m, 2),
        "lower_wick_pct": round(l_wick_pct_15 * 100, 1),
        "upper_wick_pct": round(u_wick_pct_15 * 100, 1),
        "score": score,
        "readiness_score": readiness_score,
        "is_confirmed": is_confirmed,
        "confirmation_note": conf_note,
        "is_absorption_trap": is_absorption_trap,
        "trap_warning": trap_warning,
        "setup": setup,
        "direction": direction,
        "targets": targets,
        "recommended": recommended
    }

def get_account_status():
    wb = client.get_wallet_balance()
    coins = wb.get('result', {}).get('list', [{}])[0].get('coin', [])
    usdt = next((c for c in coins if c.get('coin') == 'USDT'), {})
    eq = float(usdt.get('equity', 0))
    
    pos_res = client.get_positions()
    raw_pos = pos_res.get('result', {}).get('list', []) if pos_res.get('retCode') == 0 else []
    active = []
    for p in raw_pos:
        if float(p.get('size', 0)) > 0:
            active.append({
                "symbol": p.get('symbol'),
                "side": p.get('side'),
                "size": float(p.get('size')),
                "entry": float(p.get('avgPrice')),
                "mark": float(p.get('markPrice')),
                "unpnl": float(p.get('unrealisedPnl')),
                "sl": float(p.get('stopLoss') or 0),
                "tp": float(p.get('takeProfit') or 0)
            })
    return eq, active

def run_scanner_loop():
    log("========================================================================")
    log("  LAUNCHING PERSISTENT 24/7 LIVE MARKET SCANNER & TELEMETRY ENGINE")
    log("  Universe: 24 Coins — BTC, ETH, BNB, SOL, AVAX, SUI, LINK, XRP, ADA,")
    log("            OP, ARB, APT, NEAR, INJ, TIA, SEI, JUP, STRK, MATIC,")
    log("            DOGE, WIF, PEPE, BONK, FLOKI  ($11 notional | HTF Swing Mode)")
    log("========================================================================")

    
    from daemons.btc_macro_monitor import fetch_btc_macro
    from backend_lib.market_knowledge import kb

    scan_count = 0
    
    while True:
        scan_count += 1
        try:
            eq, active = get_account_status()
            target_state = kb.get_target_state(current_equity=eq)
            strategy_mode = target_state.get('strategy_mode', 'SWING_RUNNER')
            
            # 1. Real-time Bitcoin Macro & Altcoin Sensitivity Guard
            btc_macro = fetch_btc_macro()
            btc_dumping = btc_macro and not btc_macro.get('alt_long_allowed', True)
            btc_desc = f"BTC: ${btc_macro.get('btc_price', 0):,.1f} ({btc_macro.get('btc_chg_5m', 0):+.2f}% 5m, {btc_macro.get('regime', 'UNKNOWN')})" if btc_macro else "BTC: N/A"

            # 2. Scan all symbols with appropriate strategy mode
            results = []
            for sym in COIN_CONFIG.keys():
                if strategy_mode == "SWING_RUNNER":
                    info = scan_symbol_htf_swing(sym, btc_macro)
                else:
                    info = scan_symbol(sym)

                if info:
                    if btc_dumping and info.get('direction') == 'BUY':
                        info['score'] = max(0, info['score'] - 40)
                        info['recommended'] = False
                        info['veto_reason'] = f"BLOCKED_BY_BTC_DUMP: BTC is flushing ({btc_macro.get('btc_chg_5m')}%)"
                    results.append(info)
                    
            results.sort(key=lambda x: x['score'], reverse=True)
            
            # Print brief scan summary every tick
            top = results[0] if results else None
            if btc_dumping and top and top.get('direction') == 'BUY':
                top['recommended'] = False
                top['veto_reason'] = f"BLOCKED_BY_BTC_DUMP: BTC is flushing ({btc_macro.get('btc_chg_5m')}%)"
                
            conf_tag = "✓ 5M CONFIRMED" if (top and top.get('is_confirmed')) else f"⏳ WAIT 5M"
            trap_tag = " ⚠️ ABSORPTION TRAP" if (top and top.get('is_absorption_trap')) else ""
            disp_str = f" [D:{top.get('disp_15m', 0):+.2f}, V_Persist:{top.get('vol_persistence', 1.0)}]" if top else ""
            top_rec = f"{top['symbol']} {top['direction']} (Score {top['score']}/100 [{conf_tag}]{trap_tag}{disp_str} - {top['setup']})" if top else "None"
            if btc_dumping and top:
                if top.get('direction') == 'BUY':
                    top_rec += f" [VETOED: BTC DUMPING {btc_macro.get('btc_chg_5m')}%]"
                elif top.get('direction') == 'SELL':
                    top_rec += f" [TAILWIND: BTC DUMPING {btc_macro.get('btc_chg_5m')}%]"
            
            pos_desc = f"{len(active)} active: " + ", ".join(f"{p['symbol']} {p['side']} (${p['unpnl']:+.3f})" for p in active) if active else "0 active"
            
            mode_tag = f"[{strategy_mode}]"
            btc_short_alert = ""
            if btc_macro and btc_macro.get('is_btc_short_opportunity'):
                btc_short_alert = f" ⚡[BTC SHORT OPP! -{btc_macro.get('btc_chg_5m', 0):.2f}% 5m VOLUME SPIKE]"
            log(f"[SCAN #{scan_count}] {mode_tag} {btc_desc} | Equity: ${eq:.4f} | {pos_desc} | Top Setup: {top_rec}{btc_short_alert}")
            
            # Save complete snapshot to live_market_state.json
            payload = {
                "timestamp": int(time.time()),
                "scan_count": scan_count,
                "strategy_mode": strategy_mode,
                "equity": eq,
                "btc_macro": btc_macro,
                "active_positions": active,
                "leaderboard": results,
                "top_recommendation": top
            }
            with open(STATE_FILE, 'w', encoding='utf-8') as f:
                json.dump(payload, f, indent=2)

            # Snapshot saved locally to scratch/live_market_state.json for server & executor (0 egress)

            # Only post new unique signals (score >= 78 in swing, >= 75 in scalp)
            min_score = 78 if strategy_mode == "SWING_RUNNER" else 75
            if top and top.get("score", 0) >= min_score and top.get("recommended"):
                sig_key = f"{top['symbol']}_{top['setup']}"
                if 'last_posted_signal' not in locals() or last_posted_signal != sig_key:
                    try:
                        from backend_lib.supabase_client import supabase_post
                        supabase_post("live_market_signals", {
                            "symbol": top["symbol"],
                            "direction": top["direction"],
                            "score": top["score"],
                            "setup_name": top["setup"],
                            "rsi_5m": top.get("rsi_5m", 50.0),
                            "vol_ratio": top.get("vol_ratio", 1.0),
                            "lower_wick_pct": top.get("lower_wick_pct", 0.0),
                            "upper_wick_pct": top.get("upper_wick_pct", 0.0),
                            "entry_price": top.get("price"),
                            "was_traded": False,
                            "status": "SUGGESTED",
                            "result_reason": f"Top AI setup identified: {top['setup']} with score {top['score']}/100"
                        }, prefer="return=minimal")
                        last_posted_signal = sig_key
                    except Exception:
                        pass
                    
        except Exception as e:
            log(f"[SCANNER NOTICE] {e}")
            
        time.sleep(8)

if __name__ == '__main__':
    run_scanner_loop()
