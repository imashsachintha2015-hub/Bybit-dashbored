#!/usr/bin/env python3
"""
CME-X4 HIGHER-TIMEFRAME MARKET DESTINATION ENGINE (Live Snapshot Generator)
Computes real-time 1D/4H/1H Probabilistic Market Maps across monitored assets.
Strictly Research-Only / Non-Execution.
"""

import os
import sys
import json
import math
import time
import urllib.request
from datetime import datetime, timezone

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT_DIR)

SNAPSHOT_PATH = os.path.join(ROOT_DIR, "scratch", "cme_x4_destination_map.json")

WATCH_SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "LINKUSDT", "DOGEUSDT", "AVAXUSDT", "SUIUSDT"]

def http_get(path):
    url = f"https://api.bybit.com{path}"
    req = urllib.request.Request(url, headers={"User-Agent": "MASIS-HTF-Map/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=6) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return {}

def fetch_klines(sym, interval, limit=50):
    res = http_get(f"/v5/market/kline?category=linear&symbol={sym}&interval={interval}&limit={limit}")
    raw_list = res.get("result", {}).get("list", [])
    out = []
    for b in reversed(raw_list):
        out.append({
            "start": int(b[0]),
            "open": float(b[1]),
            "high": float(b[2]),
            "low": float(b[3]),
            "close": float(b[4]),
            "volume": float(b[5])
        })
    return out

def calc_ema(arr, period):
    if len(arr) < period:
        return arr[-1] if arr else 0.0
    k = 2.0 / (period + 1)
    ema = sum(arr[:period]) / period
    for val in arr[period:]:
        ema = val * k + ema * (1.0 - k)
    return float(ema)

def calc_atr(bars, period=14):
    if len(bars) < period + 1:
        return (bars[-1]["high"] - bars[-1]["low"]) if bars else 1.0
    trs = []
    for i in range(1, len(bars)):
        c_prev = bars[i-1]["close"]
        h = bars[i]["high"]
        l = bars[i]["low"]
        tr = max(h - l, abs(h - c_prev), abs(l - c_prev))
        trs.append(tr)
    return sum(trs[-period:]) / float(period)

def calc_rsi(closes, period=14):
    if len(closes) < period + 1:
        return 50.0
    deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains = [max(0.0, d) for d in deltas[-period:]]
    losses = [max(0.0, -d) for d in deltas[-period:]]
    avg_g = sum(gains) / period
    avg_l = sum(losses) / period
    if avg_l == 0:
        return 100.0
    rs = avg_g / avg_l
    return 100.0 - (100.0 / (1.0 + rs))

def generate_destination_map():
    maps = []
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    for sym in WATCH_SYMBOLS:
        k1d = fetch_klines(sym, "D", limit=30)
        k4h = fetch_klines(sym, "240", limit=40)
        k1h = fetch_klines(sym, "60", limit=40)

        if len(k1d) < 15 or len(k4h) < 20 or len(k1h) < 20:
            continue

        c1d = [b["close"] for b in k1d]
        c4h = [b["close"] for b in k4h]
        c1h = [b["close"] for b in k1h]
        cur_p = c4h[-1]

        # 1D Regime
        ema20_1d = calc_ema(c1d, 20)
        regime_1d = "BULLISH" if cur_p >= ema20_1d else "BEARISH"

        # 4H Structure & Momentum
        ema9_4h = calc_ema(c4h, 9)
        ema21_4h = calc_ema(c4h, 21)
        rsi_4h = calc_rsi(c4h, 14)
        
        if ema9_4h > ema21_4h:
            struct_4h = "BULLISH_CONTINUATION" if cur_p >= ema9_4h else "PULLBACK_IN_BULL_TREND"
        else:
            struct_4h = "BEARISH_EXPANSION" if cur_p <= ema9_4h else "COUNTER_BOUNCE"

        mom_4h = "STRENGTHENING" if rsi_4h > 55 else ("WEAKENING" if rsi_4h < 45 else "NEUTRAL")

        # 1H Range Position
        high_1h = max(b["high"] for b in k1h[-20:])
        low_1h = min(b["low"] for b in k1h[-20:])
        rng_1h = max(1e-6, high_1h - low_1h)
        pos_1h_pct = ((cur_p - low_1h) / rng_1h) * 100.0

        if pos_1h_pct >= 66:
            pos_desc = "UPPER_THIRD_OF_RANGE"
        elif pos_1h_pct >= 33:
            pos_desc = "MID_RANGE_VALUE_ZONE"
        else:
            pos_desc = "LOWER_THIRD_OF_RANGE"

        # Volatility sigma
        atr_4h = calc_atr(k4h, 14)
        sigma = atr_4h / cur_p if cur_p > 0 else 0.02

        # Directional Strength Score H_t
        s_1d = 1.0 if regime_1d == "BULLISH" else -1.0
        s_4h = 1.0 if "BULL" in struct_4h else -1.0
        m_val = (rsi_4h - 50.0) / 50.0
        h_t = (0.40 * s_1d) + (0.40 * s_4h) + (0.20 * m_val)

        # Probabilities based on empirical Program 11 research findings
        if h_t > 0.30:
            p_cont = 55
            p_range = 33
            p_rev = 12
            expected_path = "PULLBACK_THEN_EXPANSION (Dip to 15m EMA21 -> Rally to Upper Zone)"
        elif h_t < -0.30:
            p_cont = 48
            p_range = 35
            p_rev = 17
            expected_path = "PULLBACK_THEN_BREAKDOWN (Bounce to 15m EMA21 -> Decline to Lower Zone)"
        else:
            p_cont = 35
            p_range = 45
            p_rev = 20
            expected_path = "CONSOLIDATION_CHOP (Oscillating within 1H Range)"

        upper_1_2 = cur_p * (1.0 + 1.2 * sigma)
        upper_1_8 = cur_p * (1.0 + 1.8 * sigma)
        lower_0_6 = cur_p * (1.0 - 0.6 * sigma)
        lower_0_9 = cur_p * (1.0 - 0.9 * sigma)

        fmt = ".2f" if cur_p >= 1 else ".4f"

        map_entry = {
            "symbol": sym,
            "current_price": cur_p,
            "1D_macro_regime": regime_1d,
            "4H_intermediate_structure": struct_4h,
            "4H_momentum": mom_4h,
            "1H_range_position": pos_desc,
            "directional_score_H_t": round(h_t, 2),
            "volatility_sigma_4h": round(sigma * 100.0, 2),
            "next_12h_forecast": {
                "continuation_pct": p_cont,
                "range_pct": p_range,
                "reversal_pct": p_rev
            },
            "probable_upper_destination": f"+1.2σ to +1.8σ ({upper_1_2:{fmt}} - {upper_1_8:{fmt}})",
            "probable_lower_destination": f"-0.6σ to -0.9σ ({lower_0_9:{fmt}} - {lower_0_6:{fmt}})",
            "expected_path": expected_path
        }
        maps.append(map_entry)

    snapshot = {
        "engine": "CME-X4 Research Program 11: Market Destination Engine",
        "timestamp": now_str,
        "mode": "PROBABILISTIC_MARKET_MAP",
        "market_maps": maps
    }

    tmp = SNAPSHOT_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, indent=2)
    os.replace(tmp, SNAPSHOT_PATH)
    return snapshot

if __name__ == "__main__":
    snap = generate_destination_map()
    print(f"Generated destination map for {len(snap['market_maps'])} coins at {snap['timestamp']}:")
    for m in snap["market_maps"]:
        print(f"  {m['symbol']:<9} | 1D: {m['1D_macro_regime']:<7} | 4H: {m['4H_intermediate_structure']:<24} | H_t: {m['directional_score_H_t']:+0.2f} | Path: {m['expected_path'][:35]}...")
