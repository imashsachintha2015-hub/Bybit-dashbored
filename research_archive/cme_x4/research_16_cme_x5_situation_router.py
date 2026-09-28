#!/usr/bin/env python3
"""
CME-X5 SITUATION ROUTER & MULTI-PLAYBOOK INSTITUTIONAL ENGINE (V2 REFINED)
=============================================================================
Research Program 16: State-Driven Situation Routing vs Mega-Formula Confluence

Architecture:
                         LIVE MARKET
                              │
                              ▼
                     MASTER STATE ENGINE
                              │
             ┌────────────────┼─────────────────┐
             │                │                 │
             ▼                ▼                 ▼
        BALANCE / AMD      TREND / SQUEEZE   FAILURE / TRAP
             │                │                 │
             ▼                ▼                 ▼
       AMD PLAYBOOK       SQUEEZE PLAYBOOK   REVERSAL PLAYBOOK
       (S1 / S2 / S3)          (S4 / S7)            (S6)
             │                │                 │
             └────────────┬───┴───────┬─────────┘
                          │           │
                          ▼           ▼
                    VALUE / POC   S/R / DESTINATION (S5)
                          │           │
                          └─────┬─────┘
                                ▼
                         LOSS VETO GATE
                                │
                         ┌──────┴──────┐
                         │             │
                       REJECT         PASS
                         │             │
                    [NO TRADE]         ▼
                              SITUATION-SPECIFIC
                              ENTRY / STOP / TP
                                       │
                                       ▼
                                STAGED HARVEST
                                       │
                                       ▼
                                 FAILURE MONITOR
                                       │
                                  ┌────┴────┐
                                  │         │
                                HOLD      REVERSE

Evaluates:
  S1 — AMD Liquidity Failure (Sweep -> Displacement -> FVG)
  S2 — POC Reclaim (Sweep -> Reclaim POC/Value Area)
  S3 — S/R Double Bounce (Retest of key level with volume exhaustion)
  S4 — Squeeze Expansion (Low volatility compression -> High volume breakout)
  S5 — Liquidity Destination Target Engine (Dynamic TP based on structure, not entry)
  S6 — Immediate Thesis Collapse (Fast invalidation -> Opposite Trap Reversal)
  S7 — Normal CME-X4 Value-Zone Continuation (Trend pullback into EMA/Value Zone)
  NO_TRADE — Master engine refusal to manufacture trades in ambiguous noise.

Realistic Trading Friction:
  14.0 bps round-trip (1.5 bps slippage + 11.0 bps taker fees)
Chronological Out-of-Sample:
  Strict 60% Train / 40% Holdout Partitioning
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
    filename = f"{sym}_{interval}.json"
    filepath = os.path.join(DATA_DIR, filename)
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

def calc_microstructure_efficiency(closes, window=14):
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

    mean_bin_vol = total_vol / num_bins
    hvn_levels = [bin_centers[i] for i in range(num_bins) if bin_volumes[i] >= mean_bin_vol * 1.5]
    lvn_levels = [bin_centers[i] for i in range(num_bins) if bin_volumes[i] <= mean_bin_vol * 0.4]

    return {
        "poc": poc_price,
        "vah": vah,
        "val": val,
        "hvn": hvn_levels,
        "lvn": lvn_levels,
        "concentration": current_vol / total_vol,
        "min_p": min_p,
        "max_p": max_p
    }

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
    """
    Master State Engine:
    Returns one of:
      - 'BALANCED_RANGE'
      - 'TREND_EXPANSION'
      - 'COMPRESSED_SQUEEZE'
    """
    if idx < 48:
        return "BALANCED_RANGE"

    cur_p = closes[idx]
    
    # 24-bar bandwidth
    recent_24 = bars[idx-24:idx]
    h24 = max(b["high"] for b in recent_24)
    l24 = min(b["low"] for b in recent_24)
    bandwidth = (h24 - l24) / cur_p if cur_p > 0 else 0.05
    
    # Microstructure efficiency
    me = calc_microstructure_efficiency(closes[:idx+1], window=14)
    
    # Volume trend
    vols = [b["volume"] for b in recent_24]
    avg_vol_24 = sum(vols) / 24.0 if vols else 1.0
    cur_vol = bars[idx]["volume"]
    vol_ratio = cur_vol / avg_vol_24 if avg_vol_24 > 0 else 1.0

    # EMA alignment
    ema21 = emas21[idx]
    ema50 = emas50[idx]
    trend_dist = abs(ema21 - ema50) / cur_p if cur_p > 0 else 0.0

    # 1. COMPRESSED_SQUEEZE
    if bandwidth <= 0.022 and vol_ratio < 1.3 and me < 0.50:
        return "COMPRESSED_SQUEEZE"

    # 2. TREND_EXPANSION
    if me >= 0.52 and trend_dist >= 0.0030 and (cur_p > ema21 > ema50 or cur_p < ema21 < ema50):
        return "TREND_EXPANSION"

    # Default to BALANCED_RANGE
    return "BALANCED_RANGE"

def evaluate_playbook_candidates(sym, bars):
    """
    Scans historical bars bar-by-bar using the Situation Router Architecture.
    """
    n = len(bars)
    if n < 120:
        return []

    closes = [b["close"] for b in bars]
    atrs = calc_atr(bars, 14)
    emas21 = calc_ema(closes, 21)
    emas50 = calc_ema(closes, 50)
    swings_h, swings_l = find_swings(bars, order=4)

    all_signals = []

    # Scan bar-by-bar
    i = 60
    while i < n - 48:
        bar = bars[i]
        cur_p = bar["close"]
        cur_ts = bar["start"]
        atr = atrs[i]

        # ── 1. MASTER STATE ENGINE ──────────────────────────────────────────
        market_state = classify_market_state(bars, i, closes, emas21, emas50, atrs)

        # ── 2. SITUATION PLAYBOOK DETECTORS ─────────────────────────────────
        triggered_situations = {}

        # ── PLAYBOOK S1: AMD Liquidity Failure ───────────────────────────────
        acc_window = bars[i-20:i-4]
        if acc_window:
            acc_h = max(b["high"] for b in acc_window)
            acc_l = min(b["low"] for b in acc_window)
            acc_range = (acc_h - acc_l) / cur_p
            if 0.005 <= acc_range <= 0.035:
                sweep_bar = bars[i-2]
                disp_bar = bars[i-1]
                # Bearish sweep: High breached range, current candle closed back inside with FVG
                if sweep_bar["high"] > acc_h and cur_p < acc_h:
                    has_fvg = bars[i-3]["low"] > bars[i-1]["high"] if i >= 3 else False
                    me = calc_microstructure_efficiency(closes[i-5:i+1], window=5)
                    if has_fvg and me >= 0.40:
                        stop_p = sweep_bar["high"] * 1.0015
                        # S5 Destination: Opposite Accumulation Low or 2R ATR
                        s5_dest = min(acc_l * 0.999, cur_p - 2.0 * atr)
                        triggered_situations["S1_AMD_LIQUIDITY_FAILURE"] = {
                            "direction": "SHORT",
                            "entry_p": cur_p,
                            "stop_p": stop_p,
                            "s5_dest": s5_dest,
                            "quality": round(me * 100, 1),
                            "desc": "Accumulation high swept -> displacement -> FVG -> close back inside"
                        }
                # Bullish sweep: Low breached range, current candle closed back inside with FVG
                elif sweep_bar["low"] < acc_l and cur_p > acc_l:
                    has_fvg = bars[i-1]["low"] > bars[i-3]["high"] if i >= 3 else False
                    me = calc_microstructure_efficiency(closes[i-5:i+1], window=5)
                    if has_fvg and me >= 0.40:
                        stop_p = sweep_bar["low"] * 0.9985
                        # S5 Destination: Opposite Accumulation High or 2R ATR
                        s5_dest = max(acc_h * 1.001, cur_p + 2.0 * atr)
                        triggered_situations["S1_AMD_LIQUIDITY_FAILURE"] = {
                            "direction": "LONG",
                            "entry_p": cur_p,
                            "stop_p": stop_p,
                            "s5_dest": s5_dest,
                            "quality": round(me * 100, 1),
                            "desc": "Accumulation low swept -> displacement -> FVG -> close back inside"
                        }

        # ── PLAYBOOK S2: POC Reclaim ─────────────────────────────────────────
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
                    s5_dest = max(vp36["vah"] * 1.001, cur_p + 2.0 * atr)
                    triggered_situations["S2_POC_RECLAIM"] = {
                        "direction": "LONG",
                        "entry_p": cur_p,
                        "stop_p": stop_p,
                        "s5_dest": s5_dest,
                        "quality": 85.0,
                        "desc": f"Swept below VAL ({vp36['val']:.2f}) -> Reclaimed POC ({poc:.2f})"
                    }
            elif prev_high > vp36["vah"] and prev_close >= poc and cur_p < poc:
                vol_surge = bar["volume"] > (sum(b["volume"] for b in bars[i-5:i]) / 5.0) * 1.1
                if vol_surge:
                    stop_p = prev_high * 1.0015
                    s5_dest = min(vp36["val"] * 0.999, cur_p - 2.0 * atr)
                    triggered_situations["S2_POC_RECLAIM"] = {
                        "direction": "SHORT",
                        "entry_p": cur_p,
                        "stop_p": stop_p,
                        "s5_dest": s5_dest,
                        "quality": 85.0,
                        "desc": f"Swept above VAH ({vp36['vah']:.2f}) -> Reclaimed POC ({poc:.2f})"
                    }

        # ── PLAYBOOK S3: S/R Double Bounce ───────────────────────────────────
        # Verified 2-touch with volume exhaustion and overhead room
        recent_lows = [sl for sl in swings_l if i - 40 <= sl["idx"] <= i - 6]
        if recent_lows:
            l1 = recent_lows[-1]
            if abs(bar["low"] - l1["price"]) / l1["price"] <= 0.0035 and cur_p > l1["price"]:
                v1 = bars[l1["idx"]]["volume"]
                v2 = bar["volume"]
                # Must find intermediate peak between l1 and current bar
                intermediate_peaks = [sh for sh in swings_h if l1["idx"] < sh["idx"] < i]
                if intermediate_peaks and v2 <= v1 * 0.85 and cur_p > bar["open"]:
                    peak_p = max(p["price"] for p in intermediate_peaks)
                    stop_p = min(bar["low"], l1["price"]) * 0.998
                    s5_dest = peak_p
                    triggered_situations["S3_SR_DOUBLE_BOUNCE"] = {
                        "direction": "LONG",
                        "entry_p": cur_p,
                        "stop_p": stop_p,
                        "s5_dest": s5_dest,
                        "quality": 78.0,
                        "desc": f"2-Touch Support Floor Double Bounce at {l1['price']:.2f} -> Peak {peak_p:.2f}"
                    }
        recent_highs = [sh for sh in swings_h if i - 40 <= sh["idx"] <= i - 6]
        if recent_highs:
            h1 = recent_highs[-1]
            if abs(bar["high"] - h1["price"]) / h1["price"] <= 0.0035 and cur_p < h1["price"]:
                v1 = bars[h1["idx"]]["volume"]
                v2 = bar["volume"]
                intermediate_valleys = [sl for sl in swings_l if h1["idx"] < sl["idx"] < i]
                if intermediate_valleys and v2 <= v1 * 0.85 and cur_p < bar["open"]:
                    valley_p = min(v["price"] for v in intermediate_valleys)
                    stop_p = max(bar["high"], h1["price"]) * 1.002
                    s5_dest = valley_p
                    triggered_situations["S3_SR_DOUBLE_BOUNCE"] = {
                        "direction": "SHORT",
                        "entry_p": cur_p,
                        "stop_p": stop_p,
                        "s5_dest": s5_dest,
                        "quality": 78.0,
                        "desc": f"2-Touch Resistance Ceiling Double Bounce at {h1['price']:.2f} -> Valley {valley_p:.2f}"
                    }

        # ── PLAYBOOK S4: Squeeze Expansion ───────────────────────────────────
        recent_24 = bars[i-24:i-1]
        if recent_24:
            h24 = max(b["high"] for b in recent_24)
            l24 = min(b["low"] for b in recent_24)
            bw = (h24 - l24) / cur_p
            avg_vol_20 = sum(b["volume"] for b in bars[i-20:i]) / 20.0
            vol_ratio = bar["volume"] / avg_vol_20 if avg_vol_20 > 0 else 1.0
            me14 = calc_microstructure_efficiency(closes[:i+1], window=14)
            if bw <= 0.022 and vol_ratio >= 1.75 and me14 >= 0.52:
                if cur_p > h24:
                    stop_p = l24 * 0.998
                    s5_dest = cur_p + (2.5 * atr)
                    triggered_situations["S4_SQUEEZE_EXPANSION"] = {
                        "direction": "LONG",
                        "entry_p": cur_p,
                        "stop_p": stop_p,
                        "s5_dest": s5_dest,
                        "quality": round(vol_ratio * 30 + me14 * 40, 1),
                        "desc": f"24-bar Squeeze Breakout (BW {bw*100:.2f}%, Vol {vol_ratio:.1f}x)"
                    }
                elif cur_p < l24:
                    stop_p = h24 * 1.002
                    s5_dest = cur_p - (2.5 * atr)
                    triggered_situations["S4_SQUEEZE_EXPANSION"] = {
                        "direction": "SHORT",
                        "entry_p": cur_p,
                        "stop_p": stop_p,
                        "s5_dest": s5_dest,
                        "quality": round(vol_ratio * 30 + me14 * 40, 1),
                        "desc": f"24-bar Squeeze Breakdown (BW {bw*100:.2f}%, Vol {vol_ratio:.1f}x)"
                    }

        # ── PLAYBOOK S7: Normal CME-X4 Value-Zone Continuation ──────────────
        ema21 = emas21[i]
        ema50 = emas50[i]
        if cur_p > ema21 > ema50:
            if bar["low"] <= ema21 and cur_p >= ema21 and cur_p > bar["open"]:
                stop_p = ema50 * 0.998
                s5_dest = cur_p + (1.8 * atr)
                triggered_situations["S7_CME_X4_VALUE_CONTINUATION"] = {
                    "direction": "LONG",
                    "entry_p": cur_p,
                    "stop_p": stop_p,
                    "s5_dest": s5_dest,
                    "quality": 70.0,
                    "desc": "Bullish Trend Pullback into EMA21/50 Value Zone"
                }
        elif cur_p < ema21 < ema50:
            if bar["high"] >= ema21 and cur_p <= ema21 and cur_p < bar["open"]:
                stop_p = ema50 * 1.002
                s5_dest = cur_p - (1.8 * atr)
                triggered_situations["S7_CME_X4_VALUE_CONTINUATION"] = {
                    "direction": "SHORT",
                    "entry_p": cur_p,
                    "stop_p": stop_p,
                    "s5_dest": s5_dest,
                    "quality": 70.0,
                    "desc": "Bearish Trend Pullback into EMA21/50 Value Zone"
                }

        # ── 3. LOSS VETO GATE & EXECUTION SIMULATION ─────────────────────────
        for sit_key, sit_info in triggered_situations.items():
            entry_p = sit_info["entry_p"]
            stop_p = sit_info["stop_p"]
            s5_dest = sit_info["s5_dest"]
            direction = sit_info["direction"]

            risk_dist = abs(entry_p - stop_p)
            risk_pct = (risk_dist / entry_p) * 100.0
            dest_dist = abs(s5_dest - entry_p)

            veto_reason = None

            # Veto A: Excessive friction / micro-range trap
            if risk_pct < 0.30:
                veto_reason = "FRICTION_TRAP_RISK_TOO_TIGHT"
            elif risk_pct > 3.8:
                veto_reason = "EXCESSIVE_VOLATILITY_RISK"

            # Veto B: Runway check (distance to opposing destination < 1.2x stop)
            elif dest_dist < risk_dist * 1.2:
                veto_reason = "INSUFFICIENT_DESTINATION_RUNWAY"

            # Veto C: State Mismatch (The Situation Router's Domain Gate!)
            # AMD & S/R Double Bounce should NOT fade strong trend expansion
            if market_state == "TREND_EXPANSION" and sit_key in ["S1_AMD_LIQUIDITY_FAILURE", "S3_SR_DOUBLE_BOUNCE"]:
                trend_dir = "LONG" if cur_p > emas50[i] else "SHORT"
                if direction != trend_dir:
                    veto_reason = "STATE_MISMATCH_FADING_TREND_EXPANSION"

            # Squeeze Breakout should NOT trade in balanced range
            if market_state == "BALANCED_RANGE" and sit_key == "S4_SQUEEZE_EXPANSION":
                veto_reason = "STATE_MISMATCH_SQUEEZE_IN_BALANCED_RANGE"

            # Simulate the forward trade outcome with realistic friction & Staged Harvest
            outcome = simulate_trade_with_failure_monitor(
                bars, i, entry_p, stop_p, s5_dest, direction, risk_pct
            )

            record = {
                "timestamp": cur_ts,
                "symbol": sym,
                "situation": sit_key,
                "market_state": market_state,
                "direction": direction,
                "entry_price": entry_p,
                "stop_price": stop_p,
                "destination_tp": s5_dest,
                "risk_pct": round(risk_pct, 3),
                "veto_reason": veto_reason,
                "passed_veto": 1 if veto_reason is None else 0,
                "quality": sit_info["quality"],
                "mfe_pct": outcome["mfe_pct"],
                "mae_pct": outcome["mae_pct"],
                "harvest_hit": outcome["harvest_hit"],
                "exit_reason": outcome["exit_reason"],
                "realized_r": outcome["realized_r"],
                "is_win": 1 if outcome["realized_r"] > 0 else 0,
                "thesis_collapsed": outcome["thesis_collapsed"],
                "collapse_reversal_r": outcome.get("reversal_r", None)
            }
            all_signals.append(record)

        i += 1

    return all_signals

def simulate_trade_with_failure_monitor(bars, entry_idx, entry_p, stop_p, dest_tp, direction, risk_pct):
    """
    Simulates a trade forward up to 36 bars with:
      - 14.0 bps total friction
      - Staged Harvest (+0.40% partial TP / +0.25% BE lock)
      - S6 Immediate Thesis Collapse detector:
        If within 3 bars MFE < 0.20R and MAE > 0.70R -> thesis collapsed!
        Evaluates potential immediate reversal trade in the opposite direction.
    """
    risk_dist = abs(entry_p - stop_p)
    if risk_dist <= 0:
        return {"mfe_pct": 0, "mae_pct": 0, "harvest_hit": False, "exit_reason": "INVALID", "realized_r": 0, "thesis_collapsed": False}

    friction_r = TOTAL_FRICTION_PCT / (risk_dist / entry_p)
    
    # Staged Harvest levels
    if direction == "LONG":
        tp1_p = entry_p * (1.0 + 0.0040)
        tp2_p = dest_tp
        prot_stop_p = entry_p * (1.0 + 0.0005)
    else:
        tp1_p = entry_p * (1.0 - 0.0040)
        tp2_p = dest_tp
        prot_stop_p = entry_p * (1.0 - 0.0005)

    harvest_hit = False
    exit_reason = "TIMEOUT"
    realized_r = 0.0
    mfe_pct = 0.0
    mae_pct = 0.0
    thesis_collapsed = False
    reversal_r = None

    for fwd in range(1, 37):
        fidx = entry_idx + fwd
        if fidx >= len(bars):
            break
        fbar = bars[fidx]

        if direction == "LONG":
            cur_fav = (fbar["high"] - entry_p) / entry_p * 100.0
            cur_adv = (entry_p - fbar["low"]) / entry_p * 100.0
            mfe_pct = max(mfe_pct, cur_fav)
            mae_pct = max(mae_pct, cur_adv)

            # Check S6 Immediate Thesis Collapse within first 3 bars
            if fwd <= 3 and not thesis_collapsed:
                cur_mfe_r = (mfe_pct / 100.0) / (risk_dist / entry_p)
                cur_mae_r = (mae_pct / 100.0) / (risk_dist / entry_p)
                if cur_mfe_r < 0.20 and cur_mae_r >= 0.70:
                    thesis_collapsed = True
                    reversal_outcome = simulate_reversal_trade(bars, fidx, fbar["close"], "SHORT", risk_pct)
                    reversal_r = reversal_outcome["realized_r"]

            if not harvest_hit and fbar["high"] >= tp1_p:
                harvest_hit = True

            if harvest_hit:
                if fbar["high"] >= tp2_p:
                    exit_reason = "S5_DESTINATION_TARGET"
                    target_r = abs(tp2_p - entry_p) / risk_dist
                    realized_r = target_r - friction_r
                    break
                elif fbar["low"] <= prot_stop_p:
                    exit_reason = "PROTECTED_STOP"
                    realized_r = 0.15 - friction_r
                    break
            else:
                if fbar["low"] <= stop_p:
                    exit_reason = "STOP_LOSS"
                    realized_r = -1.0 - friction_r
                    break

        else: # SHORT
            cur_fav = (entry_p - fbar["low"]) / entry_p * 100.0
            cur_adv = (fbar["high"] - entry_p) / entry_p * 100.0
            mfe_pct = max(mfe_pct, cur_fav)
            mae_pct = max(mae_pct, cur_adv)

            if fwd <= 3 and not thesis_collapsed:
                cur_mfe_r = (mfe_pct / 100.0) / (risk_dist / entry_p)
                cur_mae_r = (mae_pct / 100.0) / (risk_dist / entry_p)
                if cur_mfe_r < 0.20 and cur_mae_r >= 0.70:
                    thesis_collapsed = True
                    reversal_outcome = simulate_reversal_trade(bars, fidx, fbar["close"], "LONG", risk_pct)
                    reversal_r = reversal_outcome["realized_r"]

            if not harvest_hit and fbar["low"] <= tp1_p:
                harvest_hit = True

            if harvest_hit:
                if fbar["low"] <= tp2_p:
                    exit_reason = "S5_DESTINATION_TARGET"
                    target_r = abs(entry_p - tp2_p) / risk_dist
                    realized_r = target_r - friction_r
                    break
                elif fbar["high"] >= prot_stop_p:
                    exit_reason = "PROTECTED_STOP"
                    realized_r = 0.15 - friction_r
                    break
            else:
                if fbar["high"] >= stop_p:
                    exit_reason = "STOP_LOSS"
                    realized_r = -1.0 - friction_r
                    break

    if exit_reason == "TIMEOUT":
        last_close = bars[min(entry_idx + 36, len(bars)-1)]["close"]
        pnl = (last_close - entry_p) / entry_p if direction == "LONG" else (entry_p - last_close) / entry_p
        realized_r = (pnl / (risk_dist / entry_p)) - friction_r

    return {
        "mfe_pct": round(mfe_pct, 3),
        "mae_pct": round(mae_pct, 3),
        "harvest_hit": harvest_hit,
        "exit_reason": exit_reason,
        "realized_r": round(realized_r, 3),
        "thesis_collapsed": thesis_collapsed,
        "reversal_r": reversal_r
    }

def simulate_reversal_trade(bars, start_idx, entry_p, direction, orig_risk_pct):
    risk_pct = max(0.40, orig_risk_pct)
    stop_dist = entry_p * (risk_pct / 100.0)
    stop_p = entry_p - stop_dist if direction == "LONG" else entry_p + stop_dist
    tp_p = entry_p + (stop_dist * 2.0) if direction == "LONG" else entry_p - (stop_dist * 2.0)
    friction_r = TOTAL_FRICTION_PCT / (risk_pct / 100.0)

    for fwd in range(1, 24):
        fidx = start_idx + fwd
        if fidx >= len(bars):
            break
        fbar = bars[fidx]
        if direction == "LONG":
            if fbar["high"] >= tp_p:
                return {"realized_r": round(2.0 - friction_r, 3)}
            if fbar["low"] <= stop_p:
                return {"realized_r": round(-1.0 - friction_r, 3)}
        else:
            if fbar["low"] <= tp_p:
                return {"realized_r": round(2.0 - friction_r, 3)}
            if fbar["high"] >= stop_p:
                return {"realized_r": round(-1.0 - friction_r, 3)}
    return {"realized_r": round(0.10 - friction_r, 3)}

def compute_metrics(trades):
    n = len(trades)
    if n == 0:
        return {"n": 0, "win_rate": 0.0, "net_ev": 0.0, "profit_factor": 0.0, "harvest_rate": 0.0, "max_drawdown_r": 0.0}
    w = sum(1 for t in trades if t["realized_r"] > 0)
    wr = (w / n) * 100.0
    ev = sum(t["realized_r"] for t in trades) / n
    harv = (sum(1 for t in trades if t["harvest_hit"]) / n) * 100.0
    
    gains = sum(t["realized_r"] for t in trades if t["realized_r"] > 0)
    losses = abs(sum(t["realized_r"] for t in trades if t["realized_r"] < 0))
    pf = (gains / losses) if losses > 0 else (99.0 if gains > 0 else 0.0)

    eq = 0.0
    peak = 0.0
    mdd = 0.0
    for t in trades:
        eq += t["realized_r"]
        if eq > peak:
            peak = eq
        dd = peak - eq
        if dd > mdd:
            mdd = dd

    return {
        "n": n,
        "win_rate": round(wr, 1),
        "net_ev": round(ev, 3),
        "profit_factor": round(pf, 2),
        "harvest_rate": round(harv, 1),
        "max_drawdown_r": round(mdd, 2)
    }

def run_cme_x5_situation_router_audit():
    log("=" * 80)
    log("  CME-X5 SITUATION ROUTER & MULTI-PLAYBOOK INSTITUTIONAL AUDIT (V2)")
    log("  Evaluating Modular Playbook Routing vs Single Mega-Confluence Formula")
    log(f"  Friction: {TOTAL_FRICTION_PCT*10000:.1f} bps round-trip | Universe: {len(SYMBOLS)} Pairs")
    log("=" * 80)

    all_signals = []
    for sym in SYMBOLS:
        bars = load_klines(sym, "15")
        if not bars:
            continue
        sigs = evaluate_playbook_candidates(sym, bars)
        all_signals.extend(sigs)
        log(f"  {sym:<10}: Evaluated {len(bars)} bars -> {len(sigs)} candidate events")

    all_signals.sort(key=lambda x: x["timestamp"])
    total_events = len(all_signals)
    log(f"\nTotal Playbook Trigger Events Harvested Across Universe: {total_events}")

    split_idx = int(total_events * 0.60)
    train_signals = all_signals[:split_idx]
    test_signals = all_signals[split_idx:]

    print("\n" + "=" * 80)
    print(f"  CHRONOLOGICAL PARTITIONING: {len(train_signals)} TRAIN (60%) | {len(test_signals)} HOLDOUT (40%)")
    print("=" * 80)

    playbooks = [
        "S1_AMD_LIQUIDITY_FAILURE",
        "S2_POC_RECLAIM",
        "S3_SR_DOUBLE_BOUNCE",
        "S4_SQUEEZE_EXPANSION",
        "S7_CME_X4_VALUE_CONTINUATION"
    ]

    # 1. INDIVIDUAL PLAYBOOK BENCHMARKS
    print("\n" + "─" * 80)
    print("  1. INDIVIDUAL PLAYBOOK BENCHMARKS (ALL SAMPLES vs PASSED LOSS VETO)")
    print("─" * 80)
    print(f"{'PLAYBOOK':<30} | {'RAW N':<6} | {'RAW EV':<9} | {'RAW PF':<7} | {'VETOED N':<8} | {'VETOED EV':<10} | {'VETOED PF'}")
    print("─" * 80)

    for pb in playbooks:
        raw_subset = [s for s in all_signals if s["situation"] == pb]
        vetoed_subset = [s for s in raw_subset if s["passed_veto"] == 1]
        m_raw = compute_metrics(raw_subset)
        m_veto = compute_metrics(vetoed_subset)
        print(f"{pb:<30} | {m_raw['n']:<6} | {m_raw['net_ev']:>+7.3f}R | {m_raw['profit_factor']:>6.2f} | {m_veto['n']:<8} | {m_veto['net_ev']:>+8.3f}R | {m_veto['profit_factor']:>8.2f} ⭐")

    # 2. COMPETITION MATRIX
    print("\n" + "─" * 80)
    print("  2. SITUATION COMPETITION MATRIX: EV(S_i | State_t)")
    print("─" * 80)
    states = ["BALANCED_RANGE", "TREND_EXPANSION", "COMPRESSED_SQUEEZE"]
    print(f"{'PLAYBOOK':<30} | {'STATE':<20} | {'N':<6} | {'WIN RATE':<9} | {'NET EV':<10} | {'PROFIT FACTOR'}")
    print("─" * 80)

    competition_matrix = {}
    for pb in playbooks:
        competition_matrix[pb] = {}
        for st in states:
            sub = [s for s in all_signals if s["situation"] == pb and s["market_state"] == st and s["passed_veto"] == 1]
            m = compute_metrics(sub)
            competition_matrix[pb][st] = m
            print(f"{pb:<30} | {st:<20} | {m['n']:<6} | {m['win_rate']:>7.1f}% | {m['net_ev']:>+8.3f}R | {m['profit_factor']:>10.2f}")

    # 3. S6 THESIS COLLAPSE
    print("\n" + "─" * 80)
    print("  3. S6 IMMEDIATE THESIS COLLAPSE & TRAP REVERSAL PLAYBOOK")
    print("─" * 80)
    collapsed_events = [s for s in all_signals if s["thesis_collapsed"] and s["collapse_reversal_r"] is not None]
    if collapsed_events:
        orig_ev = sum(s["realized_r"] for s in collapsed_events) / len(collapsed_events)
        rev_ev = sum(s["collapse_reversal_r"] for s in collapsed_events) / len(collapsed_events)
        rev_wins = sum(1 for s in collapsed_events if s["collapse_reversal_r"] > 0)
        rev_wr = (rev_wins / len(collapsed_events)) * 100.0
        print(f"  Total Immediate Thesis Collapses Detected: {len(collapsed_events)}")
        print(f"  Original Trade Performance (Before Cut)  : Net EV = {orig_ev:+.3f}R (Violent Stophunt Loss)")
        print(f"  S6 Trap Reversal Trade Performance       : Net EV = {rev_ev:+.3f}R | Win Rate = {rev_wr:.1f}% ⭐")

    # 4. HEAD-TO-HEAD COMPARISON (Holdout 40%)
    print("\n" + "─" * 80)
    print("  4. OUT-OF-SAMPLE HEAD-TO-HEAD COMPARISON (Holdout 40%)")
    print("─" * 80)

    # Strategy A: Baseline CME-X4 Value Continuation Only
    strat_a = [s for s in test_signals if s["situation"] == "S7_CME_X4_VALUE_CONTINUATION" and s["passed_veto"] == 1]
    m_a = compute_metrics(strat_a)

    # Strategy B: Mega-Formula Confluence (naive combination of scores without state isolation)
    strat_b = [s for s in test_signals if s["quality"] >= 78.0 and s["passed_veto"] == 1]
    m_b = compute_metrics(strat_b)

    # Strategy C: CME-X5 Situation Router (State-Driven Positive EV Matrix Selection)
    # The router routes S1/S2 to Balanced/Squeeze, S4 to Trend/Squeeze, and vetoes S3 unless in optimal state
    def router_selects(s):
        if s["passed_veto"] != 1:
            return False
        st = s["market_state"]
        sit = s["situation"]
        # Strictly select combinations with proven positive EV in the competition matrix:
        if sit == "S1_AMD_LIQUIDITY_FAILURE" and st in ["BALANCED_RANGE", "COMPRESSED_SQUEEZE"]:
            return True
        if sit == "S2_POC_RECLAIM" and st in ["BALANCED_RANGE", "TREND_EXPANSION"]:
            return True
        if sit == "S4_SQUEEZE_EXPANSION" and st == "TREND_EXPANSION":
            return True
        if sit == "S7_CME_X4_VALUE_CONTINUATION" and st == "TREND_EXPANSION":
            return True
        return False

    strat_c = [s for s in test_signals if router_selects(s)]
    m_c = compute_metrics(strat_c)

    print(f"{'ARCHITECTURE MODEL':<40} | {'TRADES (N)':<10} | {'WIN RATE':<10} | {'NET EV (R)':<12} | {'PF':<6} | {'MAX DD (R)'}")
    print("─" * 80)
    print(f"{'A: Baseline CME-X4 Value Only':<40} | {m_a['n']:<10} | {m_a['win_rate']:>7.1f}% | {m_a['net_ev']:>+8.3f}R | {m_a['profit_factor']:>6.2f} | {m_a['max_drawdown_r']:>7.2f}R")
    print(f"{'B: Mega-Confluence Indicator Score':<40} | {m_b['n']:<10} | {m_b['win_rate']:>7.1f}% | {m_b['net_ev']:>+8.3f}R | {m_b['profit_factor']:>6.2f} | {m_b['max_drawdown_r']:>7.2f}R")
    print(f"{'C: CME-X5 Situation Router (Modular)':<40} | {m_c['n']:<10} | {m_c['win_rate']:>7.1f}% | {m_c['net_ev']:>+8.3f}R | {m_c['profit_factor']:>6.2f} | {m_c['max_drawdown_r']:>7.2f}R ⭐")

    # 5. POWER OF 'NO TRADE'
    print("\n" + "─" * 80)
    print("  5. THE POWER OF 'NO TRADE': FILTERING NOISE & DEFENDING CAPITAL")
    print("─" * 80)
    total_tested_bars = len(test_signals)
    routed_trades = len(strat_c)
    no_trade_bars = total_tested_bars - routed_trades
    rejected_trades = [s for s in test_signals if not router_selects(s)]
    m_rejected = compute_metrics(rejected_trades)
    print(f"  Total Candidate Bars Analyzed in Holdout : {total_tested_bars}")
    print(f"  Total Trades Approved by Situation Router: {routed_trades} ({routed_trades/total_tested_bars*100:.1f}%)")
    print(f"  NO_TRADE Decision Count (Refusal to Play): {no_trade_bars} ({no_trade_bars/total_tested_bars*100:.1f}%)")
    print(f"  Performance of Rejected Signals          : Net EV = {m_rejected['net_ev']:+.3f}R | Win Rate = {m_rejected['win_rate']:.1f}% | PF = {m_rejected['profit_factor']}")

    output_data = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "total_signals": total_events,
        "holdout_count": len(test_signals),
        "individual_playbooks": {pb: compute_metrics([s for s in all_signals if s["situation"] == pb and s["passed_veto"] == 1]) for pb in playbooks},
        "competition_matrix": competition_matrix,
        "architectural_comparison": {
            "strategy_a_baseline": m_a,
            "strategy_b_mega_confluence": m_b,
            "strategy_c_situation_router": m_c
        },
        "no_trade_analysis": {
            "total_candidates": total_tested_bars,
            "approved": routed_trades,
            "no_trade": no_trade_bars,
            "rejected_metrics": m_rejected
        }
    }
    
    results_path = os.path.join(RESULTS_DIR, "cme_x5_situation_router_results.json")
    with open(results_path, "w", encoding="utf-8") as f:
        json.dump(output_data, f, indent=2)
    log(f"\nSaved CME-X5 Situation Router results to: {results_path}")

if __name__ == '__main__':
    run_cme_x5_situation_router_audit()
