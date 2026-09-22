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

    return {
        "symbol": symbol,
        "price": cur_p,
        "strategy_tier": "MICRO_SCALP",
        "trend_15m": trend_15m,
        "trend_5m": trend_5m,
        "rsi_5m": round(rsi5, 1),
        "rsi_15m": round(rsi15, 1),
        "vol_ratio": round(vol_ratio, 2),
        "lower_wick_pct": round(l_wick_pct * 100, 1),
        "upper_wick_pct": round(u_wick_pct * 100, 1),
        "score": score,
        "setup": setup,
        "direction": direction,
        "recommended": score >= 75
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

    # ── 1. Bullish Setup Evaluation (HTF_SWING_PULLBACK_LONG) ──
    bull_score = 0
    if trend_1h == "BULL":
        bull_score += 30  # 1h Macro alignment
        if 44 <= rsi_1h <= 68:
            bull_score += 15  # Solid trend momentum, not overbought
        if trend_15m == "BULL":
            bull_score += 15  # 15m trend alignment
        if 38 <= rsi_15 <= 60:
            bull_score += 15  # 15m Pullback into Value Zone
        if l_wick_pct_15 >= 0.18:
            bull_score += 10  # 15m Wick Absorption
        if vol_ratio_15 >= 1.10:
            bull_score += 10  # 15m Volume confirmation
        if cur_p >= ema9_5 and u_wick_pct_5 < 0.25:
            bull_score += 5   # 5m Trigger
        # Super-runner volume impulse bonus
        if vol_ratio_15 >= 1.70:
            bull_score += 5

    # ── 2. Bearish Setup Evaluation (HTF_SWING_BREAKDOWN_SHORT) ──
    bear_score = 0
    trend_1h_bear = (ema20_1h < ema50_1h or cur_p <= ema20_1h * 1.008)
    trend_15m_bear = (ema21_15 < ema50_15 or cur_p <= ema21_15 * 1.005)
    if trend_1h_bear:
        bear_score += 30  # 1h Macro alignment
        if 30 <= rsi_1h <= 58:
            bear_score += 15  # Bearish momentum, not oversold
        if trend_15m_bear:
            bear_score += 15  # 15m trend alignment
        if 40 <= rsi_15 <= 65:
            bear_score += 15  # 15m Retest into Value Resistance Zone
        if u_wick_pct_15 >= 0.18:
            bear_score += 10  # 15m Upper Wick Rejection
        if vol_ratio_15 >= 1.10:
            bear_score += 10  # 15m Volume confirmation on breakdown
        if cur_p <= ema9_5 and l_wick_pct_5 < 0.25:
            bear_score += 5   # 5m Trigger
        # Super-runner volume impulse bonus
        if vol_ratio_15 >= 1.70:
            bear_score += 5

    # ── Bitcoin Macro & Altcoin Sensitivity Guard ──
    if btc_macro:
        btc_dumping = btc_macro.get('is_dumping') or btc_macro.get('btc_chg_5m', 0) < -0.15
        btc_pumping = btc_macro.get('regime') == 'BTC_BULL_PUMPING' or btc_macro.get('btc_chg_1h', 0) > 0.30
        
        # Longs: Vetoed if BTC dumping; boosted if BTC pumping
        if btc_dumping:
            bull_score = 0
        elif btc_pumping:
            bull_score = min(100, bull_score + 5)
            
        # Shorts: Boosted if BTC dumping (tailward wind!); vetoed if BTC strongly pumping
        if btc_dumping:
            bear_score = min(100, bear_score + 10)
        elif btc_pumping:
            bear_score = 0

    # Select the highest conviction direction
    if bull_score >= bear_score and bull_score > 0:
        score = bull_score
        setup = "HTF_SWING_PULLBACK_LONG"
        direction = "BUY"
        targets = {
            "sl_pct": -1.50,
            "tp1_pct": 2.20,
            "tp2_pct": 4.00,
            "tp3_pct": 6.50
        }
    elif bear_score > bull_score and bear_score > 0:
        score = bear_score
        setup = "HTF_SWING_BREAKDOWN_SHORT"
        direction = "SELL"
        targets = {
            "sl_pct": 1.35,      # Invalidation pivot stop above resistance
            "tp1_pct": -1.80,    # Risk-free partial bank
            "tp2_pct": -3.50,    # Major range sweep (e.g. XRP 1.5045)
            "tp3_pct": -5.00     # Deep macro waterfall (e.g. AVAX 10.6897)
        }
    else:
        score = 0
        setup = "NONE"
        direction = "NONE"
        targets = {}

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
        "lower_wick_pct": round(l_wick_pct_15 * 100, 1),
        "upper_wick_pct": round(u_wick_pct_15 * 100, 1),
        "score": score,
        "setup": setup,
        "direction": direction,
        "targets": targets,
        "recommended": score >= 78
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
                
            top_rec = f"{top['symbol']} {top['direction']} (Score {top['score']}/100 - {top['setup']})" if top else "None"
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
