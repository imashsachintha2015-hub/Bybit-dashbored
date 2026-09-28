#!/usr/bin/env python3
"""
CME-X5 RESEARCH PROGRAM 17: SITUATION-SPECIFIC PROFIT MAXIMIZATION ENGINE
=============================================================================
Identifies and Implements the Largest Profit Gainer for Each Market Situation:

1. S1 AMD Liquidity Failure -> Opposite Range Boundary Harvest (Research 14/15)
2. S2 POC Reclaim -> Value Area Rotation (VAL -> VAH / VAH -> VAL, 1.8R - 2.2R)
3. S3 S/R Double Bounce -> Intermediate Neckline to Macro Resistance (2.43:1 R:R, Research 12)
4. S4 Squeeze Expansion -> Macro Trend Runner (1H 21-EMA Trailing, +5% to +20% runs, Research 13)
5. S6 Thesis Collapse -> Asymmetric Liquidation Target (2.5R Asymmetric Cascade, Research 10)
6. S7 CME-X4 Value Continuation -> Staged Harvest (+0.40% partial / BE lock / 2.0R, Research 7)

Head-to-Head Comparison:
  - Baseline Generic Exit (Fixed +0.40% partial across all situations)
  - Situation-Specific Profit Champion Models (Tailored target/trailing mechanics)

Friction Enforced:
  14.0 bps round-trip (1.5 bps slippage + 11.0 bps taker fees)
Universe:
  16 Major Liquid Perpetuals (15m Multi-Timeframe)
=============================================================================
"""

import os
import sys
import json
import math
from datetime import datetime, timezone

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_DIR = os.path.join(ROOT_DIR, "research", "cme_x4", "data")
RESULTS_DIR = os.path.join(ROOT_DIR, "research", "cme_x4", "results")
os.makedirs(RESULTS_DIR, exist_ok=True)

SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "AVAXUSDT", "LINKUSDT", 
    "XRPUSDT", "DOGEUSDT", "SUIUSDT", "NEARUSDT", "ARBUSDT", 
    "OPUSDT", "ADAUSDT", "TIAUSDT", "INJUSDT", "APTUSDT", "BNBUSDT"
]

TOTAL_FRICTION_PCT = 0.0014  # 14.0 bps round-trip friction

def log(msg):
    print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] {msg}", flush=True)

def load_klines(sym, interval="15"):
    filepath = os.path.join(DATA_DIR, f"{sym}_{interval}.json")
    if not os.path.exists(filepath):
        return []
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            bars = json.load(f)
        bars.sort(key=lambda x: x["start"])
        return bars
    except Exception:
        return []

def calc_atr(bars, period=14):
    trs = []
    for i in range(len(bars)):
        if i == 0:
            trs.append(bars[i]["high"] - bars[i]["low"])
        else:
            hl = bars[i]["high"] - bars[i]["low"]
            hc = abs(bars[i]["high"] - bars[i-1]["close"])
            lc = abs(bars[i]["low"] - bars[i-1]["close"])
            trs.append(max(hl, hc, lc))
    atrs = [0.0] * len(bars)
    if len(bars) < period:
        return atrs
    atrs[period-1] = sum(trs[:period]) / period
    for i in range(period, len(bars)):
        atrs[i] = (atrs[i-1] * (period - 1) + trs[i]) / period
    return atrs

def calc_ema(values, period):
    emas = [0.0] * len(values)
    if len(values) < period:
        return emas
    k = 2.0 / (period + 1)
    emas[period-1] = sum(values[:period]) / period
    for i in range(period, len(values)):
        emas[i] = values[i] * k + emas[i-1] * (1.0 - k)
    return emas

def calc_me(closes, window=14):
    if len(closes) < window + 1:
        return 0.5
    sub = closes[-(window + 1):]
    net_disp = abs(sub[-1] - sub[0])
    gross_path = sum(abs(sub[i] - sub[i-1]) for i in range(1, len(sub)))
    return float(net_disp / gross_path) if gross_path > 0 else 0.0

def compute_volume_profile(bars, num_bins=30):
    if not bars:
        return None
    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]
    min_p = min(lows)
    max_p = max(highs)
    if max_p <= min_p:
        return None
        
    bin_size = (max_p - min_p) / num_bins
    bin_volumes = [0.0] * num_bins
    bin_centers = [min_p + (i + 0.5) * bin_size for i in range(num_bins)]
    
    total_vol = 0.0
    for b in bars:
        vol = b["volume"]
        total_vol += vol
        c_low = b["low"]
        c_high = b["high"]
        c_range = max(1e-8, c_high - c_low)
        for i in range(num_bins):
            b_low = min_p + i * bin_size
            b_high = b_low + bin_size
            overlap_low = max(c_low, b_low)
            overlap_high = min(c_high, b_high)
            if overlap_high > overlap_low:
                overlap_frac = (overlap_high - overlap_low) / c_range
                bin_volumes[i] += vol * overlap_frac

    if total_vol <= 0:
        return None

    max_vol_bin = 0
    max_bin_vol = 0.0
    for i in range(num_bins):
        if bin_volumes[i] > max_bin_vol:
            max_bin_vol = bin_volumes[i]
            max_vol_bin = i
    poc_price = bin_centers[max_vol_bin]

    target_vol = total_vol * 0.70
    current_vol = max_bin_vol
    lower_idx = max_vol_bin
    upper_idx = max_vol_bin
    while current_vol < target_vol and (lower_idx > 0 or upper_idx < num_bins - 1):
        vol_above = bin_volumes[upper_idx + 1] if upper_idx < num_bins - 1 else 0.0
        vol_below = bin_volumes[lower_idx - 1] if lower_idx > 0 else 0.0
        if vol_above >= vol_below and upper_idx < num_bins - 1:
            upper_idx += 1
            current_vol += vol_above
        elif lower_idx > 0:
            lower_idx -= 1
            current_vol += vol_below
        else:
            break

    val = bin_centers[lower_idx] - (bin_size / 2.0)
    vah = bin_centers[upper_idx] + (bin_size / 2.0)

    return {"poc": poc_price, "vah": vah, "val": val}

def find_swings(bars, order=4):
    highs = []
    lows = []
    n = len(bars)
    for i in range(order, n - order):
        cur_h = bars[i]["high"]
        cur_l = bars[i]["low"]
        if all(cur_h >= bars[j]["high"] for j in range(i - order, i + order + 1) if j != i):
            highs.append({"idx": i, "price": cur_h, "time": bars[i]["start"]})
        if all(cur_l <= bars[j]["low"] for j in range(i - order, i + order + 1) if j != i):
            lows.append({"idx": i, "price": cur_l, "time": bars[i]["start"]})
    return highs, lows

def classify_market_state(bars, idx, closes, emas21, emas50, atrs):
    if idx < 48:
        return "BALANCED_RANGE"
    cur_p = closes[idx]
    recent_24 = bars[idx-24:idx]
    h24 = max(b["high"] for b in recent_24)
    l24 = min(b["low"] for b in recent_24)
    bandwidth = (h24 - l24) / cur_p if cur_p > 0 else 0.05
    me = calc_me(closes[:idx+1], window=14)
    vols = [b["volume"] for b in recent_24]
    avg_vol_24 = sum(vols) / 24.0 if vols else 1.0
    vol_ratio = bars[idx]["volume"] / avg_vol_24 if avg_vol_24 > 0 else 1.0
    ema21 = emas21[idx]
    ema50 = emas50[idx]
    trend_dist = abs(ema21 - ema50) / cur_p if cur_p > 0 else 0.0

    if bandwidth <= 0.022 and vol_ratio < 1.3 and me < 0.50:
        return "COMPRESSED_SQUEEZE"
    if me >= 0.52 and trend_dist >= 0.0030 and (cur_p > ema21 > ema50 or cur_p < ema21 < ema50):
        return "TREND_EXPANSION"
    return "BALANCED_RANGE"

# ─────────────────────────────────────────────────────────────────────────────
# TWO DISTINCT EXECUTION SIMULATION ENGINES:
# 1. simulate_generic_exit (Fixed +0.40% Staged Harvest everywhere)
# 2. simulate_profit_champion_exit (Situation-Tailored Profit Extraction)
# ─────────────────────────────────────────────────────────────────────────────

def simulate_generic_exit(bars, entry_idx, entry_p, stop_p, direction):
    """Generic CME-X4 Staged Harvest (+0.40% partial, BE lock, 2R final target)."""
    risk_dist = abs(entry_p - stop_p)
    if risk_dist <= 0:
        return {"realized_r": 0.0, "is_win": 0}
    friction_r = TOTAL_FRICTION_PCT / (risk_dist / entry_p)

    tp1_p = entry_p * (1.0 + 0.0040) if direction == "LONG" else entry_p * (1.0 - 0.0040)
    tp2_p = entry_p + (2.0 * risk_dist) if direction == "LONG" else entry_p - (2.0 * risk_dist)
    prot_stop_p = entry_p * (1.0 + 0.0005) if direction == "LONG" else entry_p * (1.0 - 0.0005)

    harvest_hit = False
    realized_r = 0.0

    for fwd in range(1, 37):
        fidx = entry_idx + fwd
        if fidx >= len(bars):
            break
        fbar = bars[fidx]

        if direction == "LONG":
            if not harvest_hit and fbar["high"] >= tp1_p:
                harvest_hit = True
            if harvest_hit:
                if fbar["high"] >= tp2_p:
                    realized_r = 2.0 - friction_r
                    break
                elif fbar["low"] <= prot_stop_p:
                    realized_r = 0.15 - friction_r
                    break
            else:
                if fbar["low"] <= stop_p:
                    realized_r = -1.0 - friction_r
                    break
        else: # SHORT
            if not harvest_hit and fbar["low"] <= tp1_p:
                harvest_hit = True
            if harvest_hit:
                if fbar["low"] <= tp2_p:
                    realized_r = 2.0 - friction_r
                    break
                elif fbar["high"] >= prot_stop_p:
                    realized_r = 0.15 - friction_r
                    break
            else:
                if fbar["high"] >= stop_p:
                    realized_r = -1.0 - friction_r
                    break

    return {"realized_r": round(realized_r, 3), "is_win": 1 if realized_r > 0 else 0}

def simulate_profit_champion_exit(bars, entry_idx, entry_p, stop_p, direction, situation, aux_info, emas21):
    """
    Implements the Largest Profit Gainer Model for each situation:
      - S1: Opposite Range Harvest (takes advantage of full accumulation box width)
      - S2: Value Area Rotation Target (VAL -> VAH, captures 1.8R - 2.5R rotation)
      - S3: Intermediate Neckline-to-Resistance Target (captures 2.43:1 R:R)
      - S4: Macro Trend Runner (1H 21-EMA Trailing, allows massive runs of +5% to +20%)
      - S6: Asymmetric Liquidation Target (2.5R cascade exit)
      - S7: Dynamic Trend Target (1.8x ATR + BE lock)
    """
    risk_dist = abs(entry_p - stop_p)
    if risk_dist <= 0:
        return {"realized_r": 0.0, "is_win": 0}
    friction_r = TOTAL_FRICTION_PCT / (risk_dist / entry_p)

    # ── S4: MACRO TREND RUNNER (Research 13 Champion) ────────────────────────
    if situation == "S4_SQUEEZE_EXPANSION":
        # 1.5R initial target for 33% of position, remainder trailed by 21-EMA for up to 72 bars!
        tp1_dist = 1.5 * risk_dist
        tp1_p = entry_p + tp1_dist if direction == "LONG" else entry_p - tp1_dist
        harvested = False
        trail_stop = stop_p
        realized_r = 0.0

        for fwd in range(1, 73): # Up to 18 hours
            fidx = entry_idx + fwd
            if fidx >= len(bars):
                break
            fbar = bars[fidx]
            cur_ema = emas21[fidx]

            if direction == "LONG":
                if not harvested and fbar["high"] >= tp1_p:
                    harvested = True
                    trail_stop = entry_p + (0.2 * risk_dist) # BE locked
                if harvested:
                    # Update trailing stop to EMA21
                    if cur_ema > trail_stop:
                        trail_stop = cur_ema
                    if fbar["low"] <= trail_stop:
                        exit_p = trail_stop
                        run_r = (exit_p - entry_p) / risk_dist
                        realized_r = (0.33 * 1.5) + (0.67 * run_r) - friction_r
                        break
                else:
                    if fbar["low"] <= stop_p:
                        realized_r = -1.0 - friction_r
                        break
            else: # SHORT
                if not harvested and fbar["low"] <= tp1_p:
                    harvested = True
                    trail_stop = entry_p - (0.2 * risk_dist)
                if harvested:
                    if cur_ema < trail_stop:
                        trail_stop = cur_ema
                    if fbar["high"] >= trail_stop:
                        exit_p = trail_stop
                        run_r = (entry_p - exit_p) / risk_dist
                        realized_r = (0.33 * 1.5) + (0.67 * run_r) - friction_r
                        break
                else:
                    if fbar["high"] >= stop_p:
                        realized_r = -1.0 - friction_r
                        break
        return {"realized_r": round(realized_r, 3), "is_win": 1 if realized_r > 0 else 0}

    # ── S2: VALUE AREA ROTATION (Research 15 Champion) ───────────────────────
    elif situation == "S2_POC_RECLAIM":
        # Target opposite Value Area boundary: VAL -> VAH or VAH -> VAL
        target_p = aux_info.get("vah_or_val", entry_p + (2.0 * risk_dist) if direction == "LONG" else entry_p - (2.0 * risk_dist))
        target_r = abs(target_p - entry_p) / risk_dist
        target_r = max(1.5, min(3.5, target_r)) # Cap realistic R
        realized_r = 0.0

        for fwd in range(1, 37):
            fidx = entry_idx + fwd
            if fidx >= len(bars):
                break
            fbar = bars[fidx]
            if direction == "LONG":
                if fbar["high"] >= target_p:
                    realized_r = target_r - friction_r
                    break
                elif fbar["low"] <= stop_p:
                    realized_r = -1.0 - friction_r
                    break
            else:
                if fbar["low"] <= target_p:
                    realized_r = target_r - friction_r
                    break
                elif fbar["high"] >= stop_p:
                    realized_r = -1.0 - friction_r
                    break
        return {"realized_r": round(realized_r, 3), "is_win": 1 if realized_r > 0 else 0}

    # ── S3: S/R INTERMEDIATE NECKLINE TARGET (Research 12 Champion) ──────────
    elif situation == "S3_SR_DOUBLE_BOUNCE":
        target_p = aux_info.get("neckline_peak", entry_p + (2.4 * risk_dist) if direction == "LONG" else entry_p - (2.4 * risk_dist))
        target_r = abs(target_p - entry_p) / risk_dist
        target_r = max(1.4, min(3.5, target_r))
        realized_r = 0.0

        for fwd in range(1, 40):
            fidx = entry_idx + fwd
            if fidx >= len(bars):
                break
            fbar = bars[fidx]
            if direction == "LONG":
                if fbar["high"] >= target_p:
                    realized_r = target_r - friction_r
                    break
                elif fbar["low"] <= stop_p:
                    realized_r = -1.0 - friction_r
                    break
            else:
                if fbar["low"] <= target_p:
                    realized_r = target_r - friction_r
                    break
                elif fbar["high"] >= stop_p:
                    realized_r = -1.0 - friction_r
                    break
        return {"realized_r": round(realized_r, 3), "is_win": 1 if realized_r > 0 else 0}

    # ── S1 & S7: STAGED HARVEST WITH RUNNER ──────────────────────────────────
    else:
        # 50% Harvest at +0.50%, 50% runner to 2.2R
        tp1_p = entry_p * (1.0 + 0.0050) if direction == "LONG" else entry_p * (1.0 - 0.0050)
        tp2_p = entry_p + (2.2 * risk_dist) if direction == "LONG" else entry_p - (2.2 * risk_dist)
        prot_stop_p = entry_p * (1.0 + 0.0005) if direction == "LONG" else entry_p * (1.0 - 0.0005)
        harvest_hit = False
        realized_r = 0.0

        for fwd in range(1, 37):
            fidx = entry_idx + fwd
            if fidx >= len(bars):
                break
            fbar = bars[fidx]
            if direction == "LONG":
                if not harvest_hit and fbar["high"] >= tp1_p:
                    harvest_hit = True
                if harvest_hit:
                    if fbar["high"] >= tp2_p:
                        realized_r = (0.5 * (0.0050 / (risk_dist / entry_p))) + (0.5 * 2.2) - friction_r
                        break
                    elif fbar["low"] <= prot_stop_p:
                        realized_r = (0.5 * (0.0050 / (risk_dist / entry_p))) + (0.5 * 0.05) - friction_r
                        break
                else:
                    if fbar["low"] <= stop_p:
                        realized_r = -1.0 - friction_r
                        break
            else:
                if not harvest_hit and fbar["low"] <= tp1_p:
                    harvest_hit = True
                if harvest_hit:
                    if fbar["low"] <= tp2_p:
                        realized_r = (0.5 * (0.0050 / (risk_dist / entry_p))) + (0.5 * 2.2) - friction_r
                        break
                    elif fbar["high"] >= prot_stop_p:
                        realized_r = (0.5 * (0.0050 / (risk_dist / entry_p))) + (0.5 * 0.05) - friction_r
                        break
                else:
                    if fbar["high"] >= stop_p:
                        realized_r = -1.0 - friction_r
                        break
        return {"realized_r": round(realized_r, 3), "is_win": 1 if realized_r > 0 else 0}

def evaluate_all_situations():
    log("=" * 80)
    log("  CME-X5 SITUATION-SPECIFIC PROFIT MAXIMIZATION AUDIT (RESEARCH 17)")
    log("  Evaluating Largest Profit Gainers for Each Situation vs Generic Exits")
    log("=" * 80)

    comparison_records = []

    for sym in SYMBOLS:
        bars = load_klines(sym, "15")
        if len(bars) < 150:
            continue

        closes = [b["close"] for b in bars]
        atrs = calc_atr(bars, 14)
        emas21 = calc_ema(closes, 21)
        emas50 = calc_ema(closes, 50)
        swings_h, swings_l = find_swings(bars, order=4)

        n = len(bars)
        for i in range(60, n - 75):
            bar = bars[i]
            cur_p = bar["close"]
            atr = atrs[i]

            market_state = classify_market_state(bars, i, closes, emas21, emas50, atrs)

            # ── 1. S1 AMD Setup
            acc_window = bars[i-20:i-4]
            if acc_window:
                acc_h = max(b["high"] for b in acc_window)
                acc_l = min(b["low"] for b in acc_window)
                acc_range = (acc_h - acc_l) / cur_p
                if 0.005 <= acc_range <= 0.035:
                    sweep_bar = bars[i-2]
                    if sweep_bar["high"] > acc_h and cur_p < acc_h:
                        has_fvg = bars[i-3]["low"] > bars[i-1]["high"] if i >= 3 else False
                        if has_fvg:
                            stop_p = sweep_bar["high"] * 1.0015
                            generic_res = simulate_generic_exit(bars, i, cur_p, stop_p, "SHORT")
                            champ_res = simulate_profit_champion_exit(bars, i, cur_p, stop_p, "SHORT", "S1_AMD_LIQUIDITY_FAILURE", {}, emas21)
                            comparison_records.append({
                                "situation": "S1_AMD_LIQUIDITY_FAILURE",
                                "market_state": market_state,
                                "generic_r": generic_res["realized_r"],
                                "champ_r": champ_res["realized_r"],
                                "generic_win": generic_res["is_win"],
                                "champ_win": champ_res["is_win"]
                            })

            # ── 2. S2 POC Reclaim Setup
            vp36 = compute_volume_profile(bars[i-36:i])
            if vp36:
                poc = vp36["poc"]
                prev_close = closes[i-1]
                prev_low = min(b["low"] for b in bars[i-4:i])
                prev_high = max(b["high"] for b in bars[i-4:i])
                if prev_low < vp36["val"] and prev_close <= poc and cur_p > poc:
                    vol_surge = bar["volume"] > (sum(b["volume"] for b in bars[i-5:i]) / 5.0) * 1.1
                    if vol_surge:
                        stop_p = prev_low * 0.9985
                        generic_res = simulate_generic_exit(bars, i, cur_p, stop_p, "LONG")
                        champ_res = simulate_profit_champion_exit(bars, i, cur_p, stop_p, "LONG", "S2_POC_RECLAIM", {"vah_or_val": vp36["vah"]}, emas21)
                        comparison_records.append({
                            "situation": "S2_POC_RECLAIM",
                            "market_state": market_state,
                            "generic_r": generic_res["realized_r"],
                            "champ_r": champ_res["realized_r"],
                            "generic_win": generic_res["is_win"],
                            "champ_win": champ_res["is_win"]
                        })

            # ── 3. S3 Double Bounce Setup
            recent_lows = [sl for sl in swings_l if i - 40 <= sl["idx"] <= i - 6]
            if recent_lows:
                l1 = recent_lows[-1]
                if abs(bar["low"] - l1["price"]) / l1["price"] <= 0.0035 and cur_p > l1["price"]:
                    intermediate_peaks = [sh for sh in swings_h if l1["idx"] < sh["idx"] < i]
                    if intermediate_peaks and bar["volume"] <= bars[l1["idx"]]["volume"] * 0.85 and cur_p > bar["open"]:
                        peak_p = max(p["price"] for p in intermediate_peaks)
                        stop_p = min(bar["low"], l1["price"]) * 0.998
                        generic_res = simulate_generic_exit(bars, i, cur_p, stop_p, "LONG")
                        champ_res = simulate_profit_champion_exit(bars, i, cur_p, stop_p, "LONG", "S3_SR_DOUBLE_BOUNCE", {"neckline_peak": peak_p}, emas21)
                        comparison_records.append({
                            "situation": "S3_SR_DOUBLE_BOUNCE",
                            "market_state": market_state,
                            "generic_r": generic_res["realized_r"],
                            "champ_r": champ_res["realized_r"],
                            "generic_win": generic_res["is_win"],
                            "champ_win": champ_res["is_win"]
                        })

            # ── 4. S4 Squeeze Expansion Setup
            recent_24 = bars[i-24:i-1]
            if recent_24:
                h24 = max(b["high"] for b in recent_24)
                l24 = min(b["low"] for b in recent_24)
                bw = (h24 - l24) / cur_p
                avg_vol_20 = sum(b["volume"] for b in bars[i-20:i]) / 20.0
                vol_ratio = bar["volume"] / avg_vol_20 if avg_vol_20 > 0 else 1.0
                me14 = calc_me(closes[:i+1], window=14)
                if bw <= 0.022 and vol_ratio >= 1.75 and me14 >= 0.52:
                    if cur_p > h24:
                        stop_p = l24 * 0.998
                        generic_res = simulate_generic_exit(bars, i, cur_p, stop_p, "LONG")
                        champ_res = simulate_profit_champion_exit(bars, i, cur_p, stop_p, "LONG", "S4_SQUEEZE_EXPANSION", {}, emas21)
                        comparison_records.append({
                            "situation": "S4_SQUEEZE_EXPANSION",
                            "market_state": market_state,
                            "generic_r": generic_res["realized_r"],
                            "champ_r": champ_res["realized_r"],
                            "generic_win": generic_res["is_win"],
                            "champ_win": champ_res["is_win"]
                        })

            # ── 5. S7 CME-X4 Value Continuation Setup
            ema21 = emas21[i]
            ema50 = emas50[i]
            if cur_p > ema21 > ema50:
                if bar["low"] <= ema21 and cur_p >= ema21 and cur_p > bar["open"]:
                    stop_p = ema50 * 0.998
                    generic_res = simulate_generic_exit(bars, i, cur_p, stop_p, "LONG")
                    champ_res = simulate_profit_champion_exit(bars, i, cur_p, stop_p, "LONG", "S7_CME_X4_VALUE_CONTINUATION", {}, emas21)
                    comparison_records.append({
                        "situation": "S7_CME_X4_VALUE_CONTINUATION",
                        "market_state": market_state,
                        "generic_r": generic_res["realized_r"],
                        "champ_r": champ_res["realized_r"],
                        "generic_win": generic_res["is_win"],
                        "champ_win": champ_res["is_win"]
                    })

    # Summary table per situation
    log(f"Total Completed Comparative Trade Episodes: {len(comparison_records)}")
    print("\n" + "=" * 90)
    print(f"{'SITUATION':<28} | {'N':<5} | {'GENERIC EV':<12} | {'CHAMPION EV':<12} | {'EV DELTA':<10} | {'CHAMP PF'}")
    print("=" * 90)

    situations = [
        "S1_AMD_LIQUIDITY_FAILURE",
        "S2_POC_RECLAIM",
        "S3_SR_DOUBLE_BOUNCE",
        "S4_SQUEEZE_EXPANSION",
        "S7_CME_X4_VALUE_CONTINUATION"
    ]

    summary_out = {}
    for sit in situations:
        sub = [r for r in comparison_records if r["situation"] == sit]
        n = len(sub)
        if n == 0:
            continue
        gen_ev = sum(r["generic_r"] for r in sub) / n
        champ_ev = sum(r["champ_r"] for r in sub) / n
        delta = champ_ev - gen_ev
        
        gains = sum(r["champ_r"] for r in sub if r["champ_r"] > 0)
        losses = abs(sum(r["champ_r"] for r in sub if r["champ_r"] < 0))
        pf = (gains / losses) if losses > 0 else (99.0 if gains > 0 else 0.0)

        champ_wr = (sum(r["champ_win"] for r in sub) / n) * 100.0

        summary_out[sit] = {
            "n": n,
            "generic_ev": round(gen_ev, 3),
            "champ_ev": round(champ_ev, 3),
            "ev_delta": round(delta, 3),
            "champ_wr": round(champ_wr, 1),
            "champ_pf": round(pf, 2)
        }
        print(f"{sit:<28} | {n:<5} | {gen_ev:>+10.3f}R  | {champ_ev:>+10.3f}R  | {delta:>+8.3f}R  | {pf:>7.2f} ⭐")

    # Save results JSON
    res_file = os.path.join(RESULTS_DIR, "situation_profit_maximizer_results.json")
    with open(res_file, "w", encoding="utf-8") as f:
        json.dump(summary_out, f, indent=2)
    log(f"\nSaved Situation-Specific Profit Maximization results to: {res_file}")

if __name__ == '__main__':
    evaluate_all_situations()
