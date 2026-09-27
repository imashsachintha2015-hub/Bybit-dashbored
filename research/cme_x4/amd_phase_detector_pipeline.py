#!/usr/bin/env python3
"""
CME-X4 AMD PHASE DETECTOR & OBSERVATIONAL FAILURE PIPELINE
Evaluates:
  1. Multi-metric Accumulation Score (0-100)
  2. Liquidity Sweep Candidate Detection (Upside / Downside)
  3. Displacement & FVG Composite Score (0-100)
  4. Immediate Thesis Failure Reversal Confirmation
  5. Observational Metric Harvester & Variable Permutation Optimizer
"""

import os
import sys
import json
import math
from datetime import datetime, timezone

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_DIR = os.path.join(ROOT_DIR, "research", "cme_x4", "data")
RESULTS_DIR = os.path.join(ROOT_DIR, "research", "cme_x4", "results")
os.makedirs(RESULTS_DIR, exist_ok=True)

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "AVAXUSDT", "LINKUSDT", "XRPUSDT", "DOGEUSDT"]

# Friction parameters
MAKER_FEE_BPS = 2.0
TAKER_FEE_BPS = 5.5
SLIPPAGE_BPS = 1.5
TOTAL_ENTRY_FRICTION_PCT = (TAKER_FEE_BPS + SLIPPAGE_BPS) / 10000.0  # ~0.0007
TOTAL_EXIT_FRICTION_PCT = (TAKER_FEE_BPS + SLIPPAGE_BPS) / 10000.0

def load_klines(sym, interval="15"):
    filename = f"{sym}_{interval}.json"
    filepath = os.path.join(DATA_DIR, filename)
    if not os.path.exists(filepath):
        return []
    with open(filepath, "r", encoding="utf-8") as f:
        bars = json.load(f)
    bars.sort(key=lambda x: x["start"])
    return bars

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

def get_session(ts_ms):
    dt = datetime.fromtimestamp(ts_ms / 1000.0, timezone.utc)
    hour = dt.hour
    if 0 <= hour < 8:
        return "ASIA"
    elif 8 <= hour < 16:
        return "LONDON"
    else:
        return "NEW_YORK"

def compute_accumulation_score(window_bars, atr_val):
    """
    Computes a composite Accumulation Score (0-100) based on 5 independent characteristics:
    1. Range compression vs ATR
    2. Volume compression vs historical
    3. Order flow balance (CVD flatline)
    4. Price containment within 25th-75th quartile
    5. Value zone duration consistency
    """
    n = len(window_bars)
    if n < 15 or atr_val <= 0:
        return 0.0, {}

    highs = [b["high"] for b in window_bars]
    lows = [b["low"] for b in window_bars]
    closes = [b["close"] for b in window_bars]
    volumes = [b["volume"] for b in window_bars]
    
    max_h = max(highs)
    min_l = min(lows)
    range_val = max_h - min_l
    range_pct = (range_val / min_l) * 100.0

    # 1. Range Compression Score (0-25 pts)
    # Compare range to expected random walk expansion (ATR * sqrt(N))
    expected_expansion = atr_val * math.sqrt(n)
    compression_ratio = range_val / expected_expansion if expected_expansion > 0 else 1.0
    if compression_ratio <= 1.2:
        range_score = 25.0
    elif compression_ratio <= 2.0:
        range_score = 25.0 * (2.0 - compression_ratio) / 0.8
    else:
        range_score = 0.0

    # 2. Volume Compression Score (0-25 pts)
    # Compare mean volume to first half vs second half, or volume variance
    avg_vol = sum(volumes) / n
    recent_vol = sum(volumes[-10:]) / 10.0
    vol_ratio = recent_vol / avg_vol if avg_vol > 0 else 1.0
    if vol_ratio <= 0.70:
        vol_score = 25.0
    elif vol_ratio <= 1.10:
        vol_score = 25.0 * (1.10 - vol_ratio) / 0.40
    else:
        vol_score = 0.0

    # 3. Order-flow Balance / CVD Flatline (0-20 pts)
    # Intra-candle body delta proxy
    deltas = []
    for b in window_bars:
        candle_range = b["high"] - b["low"]
        if candle_range > 0:
            delta_ratio = (b["close"] - b["open"]) / candle_range
            deltas.append(delta_ratio * b["volume"])
        else:
            deltas.append(0.0)
    net_delta = sum(deltas)
    total_abs_delta = sum(abs(d) for d in deltas)
    delta_imbalance = abs(net_delta) / total_abs_delta if total_abs_delta > 0 else 0.0
    # Imbalance near 0 means balance/neutral accumulation
    delta_score = max(0.0, 20.0 * (1.0 - delta_imbalance * 2.5))

    # 4. Price Containment (0-15 pts)
    # Fraction of bars closing in the inner 50% band
    midpoint = (max_h + min_l) / 2.0
    band_half = range_val * 0.30
    contained_bars = sum(1 for c in closes if abs(c - midpoint) <= band_half)
    containment_pct = contained_bars / n
    contain_score = min(15.0, containment_pct * 15.0 / 0.60)

    # 5. Time Spent at Value (0-15 pts)
    # Stability of closes around midpoint
    time_score = 15.0 if n >= 25 else (n / 25.0) * 15.0

    total_score = round(range_score + vol_score + delta_score + contain_score + time_score, 1)
    
    meta = {
        "range_pct": round(range_pct, 3),
        "range_compression_ratio": round(compression_ratio, 2),
        "volume_compression_ratio": round(vol_ratio, 2),
        "delta_imbalance": round(delta_imbalance, 3),
        "containment_pct": round(containment_pct * 100, 1),
        "acc_high": max_h,
        "acc_low": min_l,
        "acc_mid": midpoint,
        "acc_range": range_val
    }
    return total_score, meta

def compute_displacement_score(bar, prev_bar, prev2_bar, atr_val, direction):
    """
    Computes Displacement & FVG Score (0-100):
    - FVG magnitude normalized by ATR
    - Candle body / ATR ratio
    - Close location relative to extreme (no rejection in direction of displacement)
    - Volume surge
    """
    if atr_val <= 0:
        return 0.0, 0.0, False

    bar_range = bar["high"] - bar["low"]
    body = abs(bar["close"] - bar["open"])
    body_atr = body / atr_val
    
    has_fvg = False
    fvg_size = 0.0
    
    if direction == "SHORT": # Bearish displacement after High Sweep
        # Bearish FVG: Low of candle 2 bars ago > High of current candle
        if prev2_bar["low"] > bar["high"]:
            fvg_size = prev2_bar["low"] - bar["high"]
            has_fvg = True
        close_location = (bar["high"] - bar["close"]) / bar_range if bar_range > 0 else 0.5
        is_bear_candle = bar["close"] < bar["open"]
    else: # Bullish displacement after Low Sweep
        # Bullish FVG: Low of current candle > High of candle 2 bars ago
        if bar["low"] > prev2_bar["high"]:
            fvg_size = bar["low"] - prev2_bar["high"]
            has_fvg = True
        close_location = (bar["close"] - bar["low"]) / bar_range if bar_range > 0 else 0.5
        is_bear_candle = bar["close"] > bar["open"]

    fvg_atr = fvg_size / atr_val

    # FVG component: 0-35 pts
    fvg_score = min(35.0, (fvg_atr / 0.50) * 35.0) if has_fvg else 0.0
    
    # Body magnitude: 0-30 pts (strong body = strong displacement)
    body_score = min(30.0, (body_atr / 0.80) * 30.0) if is_bear_candle else 0.0

    # Close location: 0-20 pts (closed near extreme of candle)
    close_score = min(20.0, close_location * 20.0) if close_location >= 0.65 else 0.0

    # Volume: 0-15 pts
    vol_ratio = bar["volume"] / prev_bar["volume"] if prev_bar["volume"] > 0 else 1.0
    vol_score = min(15.0, (vol_ratio / 1.50) * 15.0)

    disp_score = round(fvg_score + body_score + close_score + vol_score, 1)
    return disp_score, round(fvg_atr, 3), has_fvg

def scan_amd_pipeline():
    print("=" * 70)
    print("  CME-X4 AMD PHASE DETECTOR & OBSERVATIONAL FAILURE RESEARCH")
    print("=" * 70)

    all_events = []
    
    for sym in SYMBOLS:
        bars_15 = load_klines(sym, "15")
        if len(bars_15) < 150:
            continue
        
        atrs = calc_atr(bars_15, 14)
        closes = [b["close"] for b in bars_15]
        ema21 = calc_ema(closes, 21)
        ema50 = calc_ema(closes, 50)

        coin_events = []
        acc_window_size = 24  # 24 * 15m = 6 hours accumulation window

        i = acc_window_size + 2
        while i < len(bars_15) - 30:
            window_bars = bars_15[i-acc_window_size:i]
            atr_val = atrs[i]
            
            acc_score, acc_meta = compute_accumulation_score(window_bars, atr_val)
            
            # Step 1: Candidate must show minimum accumulation compression
            if acc_score < 45.0:
                i += 1
                continue

            acc_h = acc_meta["acc_high"]
            acc_l = acc_meta["acc_low"]

            # Step 2: Check for Liquidity Sweep in the next 1-4 bars
            # Upside Sweep (Bearish setup) or Downside Sweep (Bullish setup)
            sweep_found = False
            sweep_dir = None
            sweep_idx = -1
            sweep_price = 0.0
            
            for s in range(1, 5):
                idx = i + s
                if idx >= len(bars_15) - 20:
                    break
                curr = bars_15[idx]
                
                # Upside sweep: High pierced acc_high
                if curr["high"] > acc_h:
                    sweep_found = True
                    sweep_dir = "SHORT"  # Intending to fade failure
                    sweep_idx = idx
                    sweep_price = curr["high"]
                    break
                # Downside sweep: Low pierced acc_low
                elif curr["low"] < acc_l:
                    sweep_found = True
                    sweep_dir = "LONG"   # Intending to fade failure
                    sweep_idx = idx
                    sweep_price = curr["low"]
                    break

            if not sweep_found:
                i += 1
                continue

            sweep_bar = bars_15[sweep_idx]
            sweep_atr = atrs[sweep_idx]
            
            # Sweep size & wick metrics
            if sweep_dir == "SHORT":
                sweep_size = sweep_price - acc_h
                sweep_size_pct = (sweep_size / acc_h) * 100.0
                wick = sweep_bar["high"] - max(sweep_bar["open"], sweep_bar["close"])
                wick_pct = (wick / (sweep_bar["high"] - sweep_bar["low"])) * 100.0 if (sweep_bar["high"] - sweep_bar["low"]) > 0 else 0.0
            else:
                sweep_size = acc_l - sweep_price
                sweep_size_pct = (sweep_size / acc_l) * 100.0
                wick = min(sweep_bar["open"], sweep_bar["close"]) - sweep_bar["low"]
                wick_pct = (wick / (sweep_bar["high"] - sweep_bar["low"])) * 100.0 if (sweep_bar["high"] - sweep_bar["low"]) > 0 else 0.0

            sweep_size_atr = sweep_size / sweep_atr if sweep_atr > 0 else 0.0
            sweep_duration_bars = sweep_idx - i

            # Step 3: Check for Displacement & Reclaim within next 1-3 bars
            reclaim_found = False
            reclaim_idx = -1
            
            for r in range(1, 4):
                ridx = sweep_idx + r
                if ridx >= len(bars_15) - 15:
                    break
                rbar = bars_15[ridx]
                
                # Check reclaim condition:
                # Bearish: close returns below acc_h
                if sweep_dir == "SHORT" and rbar["close"] < acc_h:
                    reclaim_found = True
                    reclaim_idx = ridx
                    break
                # Bullish: close returns above acc_l
                elif sweep_dir == "LONG" and rbar["close"] > acc_l:
                    reclaim_found = True
                    reclaim_idx = ridx
                    break

            if not reclaim_found:
                i = sweep_idx + 2
                continue

            # Step 4: Evaluate Displacement Score & FVG at Reclaim
            disp_bar = bars_15[reclaim_idx]
            disp_prev = bars_15[reclaim_idx - 1]
            disp_prev2 = bars_15[reclaim_idx - 2]
            disp_atr = atrs[reclaim_idx]
            
            disp_score, fvg_atr, has_fvg = compute_displacement_score(disp_bar, disp_prev, disp_prev2, disp_atr, sweep_dir)

            # MTF Trend Context (EMA21 vs EMA50)
            trend_bull = ema21[reclaim_idx] > ema50[reclaim_idx]
            mtf_aligned = (sweep_dir == "LONG" and trend_bull) or (sweep_dir == "SHORT" and not trend_bull)

            # Session
            session = get_session(disp_bar["start"])

            # Step 5: Forward Tracking & Simulated Trade Outcome
            entry_price = disp_bar["close"]
            stop_price = sweep_price  # Stop at sweep extreme
            
            if sweep_dir == "SHORT":
                initial_risk_dist = stop_price - entry_price
                if initial_risk_dist <= 0 or entry_price <= 0:
                    i = reclaim_idx + 1
                    continue
                risk_pct = (initial_risk_dist / entry_price) * 100.0
                tp1_price = entry_price * (1.0 - 0.0040)       # +0.40% Harvest
                prot_stop_price = entry_price * (1.0 - 0.0025) # +0.25% Protected Stop
                tp2_price = entry_price - (initial_risk_dist * 2.0) # 2.0R Target
            else:
                initial_risk_dist = entry_price - stop_price
                if initial_risk_dist <= 0 or entry_price <= 0:
                    i = reclaim_idx + 1
                    continue
                risk_pct = (initial_risk_dist / entry_price) * 100.0
                tp1_price = entry_price * (1.0 + 0.0040)
                prot_stop_price = entry_price * (1.0 + 0.0025)
                tp2_price = entry_price + (initial_risk_dist * 2.0)

            # Enforce reasonable risk bounds: between 0.25% and 2.5%
            if risk_pct < 0.20 or risk_pct > 3.0:
                i = reclaim_idx + 1
                continue

            # Forward simulate forward up to 36 bars (9 hours)
            mfe_pct = 0.0
            mae_pct = 0.0
            time_to_mfe = 0
            time_to_mae = 0
            harvest_hit = False
            exit_reason = "TIMEOUT"
            exit_price = entry_price
            realized_r = 0.0
            
            for fwd in range(1, 37):
                fidx = reclaim_idx + fwd
                if fidx >= len(bars_15):
                    break
                fbar = bars_15[fidx]

                if sweep_dir == "SHORT":
                    cur_fav = (entry_price - fbar["low"]) / entry_price * 100.0
                    cur_adv = (fbar["high"] - entry_price) / entry_price * 100.0
                    
                    if cur_fav > mfe_pct:
                        mfe_pct = cur_fav
                        time_to_mfe = fwd * 15
                    if cur_adv > mae_pct:
                        mae_pct = cur_adv
                        time_to_mae = fwd * 15

                    # Harvest check (+0.40%)
                    if not harvest_hit and fbar["low"] <= tp1_price:
                        harvest_hit = True

                    # Exit checks
                    if harvest_hit:
                        # Full 2R Target
                        if fbar["low"] <= tp2_price:
                            exit_reason = "FULL_2R_TARGET"
                            exit_price = tp2_price
                            realized_r = 2.0 - ((TOTAL_ENTRY_FRICTION_PCT + TOTAL_EXIT_FRICTION_PCT) / (risk_pct / 100.0))
                            break
                        # Protected Breakeven Stop (+0.25%)
                        elif fbar["high"] >= prot_stop_price:
                            exit_reason = "PROTECTED_STOP_EXIT"
                            exit_price = prot_stop_price
                            r_locked = 0.25 / risk_pct
                            realized_r = r_locked - ((TOTAL_ENTRY_FRICTION_PCT + TOTAL_EXIT_FRICTION_PCT) / (risk_pct / 100.0))
                            break
                    else:
                        # Hard Stop
                        if fbar["high"] >= stop_price:
                            exit_reason = "HARD_STOP_LOSS"
                            exit_price = stop_price
                            realized_r = -1.0 - ((TOTAL_ENTRY_FRICTION_PCT + TOTAL_EXIT_FRICTION_PCT) / (risk_pct / 100.0))
                            break
                else: # LONG
                    cur_fav = (fbar["high"] - entry_price) / entry_price * 100.0
                    cur_adv = (entry_price - fbar["low"]) / entry_price * 100.0
                    
                    if cur_fav > mfe_pct:
                        mfe_pct = cur_fav
                        time_to_mfe = fwd * 15
                    if cur_adv > mae_pct:
                        mae_pct = cur_adv
                        time_to_mae = fwd * 15

                    # Harvest check (+0.40%)
                    if not harvest_hit and fbar["high"] >= tp1_price:
                        harvest_hit = True

                    # Exit checks
                    if harvest_hit:
                        if fbar["high"] >= tp2_price:
                            exit_reason = "FULL_2R_TARGET"
                            exit_price = tp2_price
                            realized_r = 2.0 - ((TOTAL_ENTRY_FRICTION_PCT + TOTAL_EXIT_FRICTION_PCT) / (risk_pct / 100.0))
                            break
                        elif fbar["low"] <= prot_stop_price:
                            exit_reason = "PROTECTED_STOP_EXIT"
                            exit_price = prot_stop_price
                            r_locked = 0.25 / risk_pct
                            realized_r = r_locked - ((TOTAL_ENTRY_FRICTION_PCT + TOTAL_EXIT_FRICTION_PCT) / (risk_pct / 100.0))
                            break
                    else:
                        if fbar["low"] <= stop_price:
                            exit_reason = "HARD_STOP_LOSS"
                            exit_price = stop_price
                            realized_r = -1.0 - ((TOTAL_ENTRY_FRICTION_PCT + TOTAL_EXIT_FRICTION_PCT) / (risk_pct / 100.0))
                            break

            if exit_reason == "TIMEOUT":
                # Mark to market at bar 36
                end_bar = bars_15[min(reclaim_idx + 36, len(bars_15)-1)]
                cur_diff = (entry_price - end_bar["close"]) if sweep_dir == "SHORT" else (end_bar["close"] - entry_price)
                realized_r = (cur_diff / initial_risk_dist) - ((TOTAL_ENTRY_FRICTION_PCT + TOTAL_EXIT_FRICTION_PCT) / (risk_pct / 100.0))

            event_record = {
                "timestamp": disp_bar["start"],
                "symbol": sym,
                "direction": sweep_dir,
                "accumulation_score": acc_score,
                "range_pct": acc_meta["range_pct"],
                "compression_ratio": acc_meta["range_compression_ratio"],
                "volume_ratio": acc_meta["volume_compression_ratio"],
                "delta_imbalance": acc_meta["delta_imbalance"],
                "sweep_size_pct": round(sweep_size_pct, 3),
                "sweep_size_atr": round(sweep_size_atr, 2),
                "sweep_duration_bars": sweep_duration_bars,
                "wick_pct": round(wick_pct, 1),
                "has_fvg": has_fvg,
                "fvg_size_atr": fvg_atr,
                "displacement_score": disp_score,
                "mtf_aligned": mtf_aligned,
                "session": session,
                "risk_pct": round(risk_pct, 3),
                "mfe_pct": round(mfe_pct, 3),
                "mae_pct": round(mae_pct, 3),
                "time_to_mfe_min": time_to_mfe,
                "time_to_mae_min": time_to_mae,
                "harvest_hit": harvest_hit,
                "exit_reason": exit_reason,
                "realized_r": round(realized_r, 3),
                "is_win": 1 if realized_r > 0 else 0
            }
            all_events.append(event_record)
            coin_events.append(event_record)
            
            # Jump ahead past the trade lifecycle to avoid overlapping duplicate signals
            i = reclaim_idx + 8

        print(f"[{sym}] Processed: {len(coin_events)} AMD phase occurrences recorded.")

    print(f"\nTotal AMD Failure Occurrences Harvested: {len(all_events)}")
    return all_events

def analyze_variable_permutations(events):
    print("\n" + "=" * 70)
    print("  AMD VARIABLE PERMUTATION OPTIMIZER (Statistical Edge Search)")
    print("=" * 70)

    # Baseline performance (Generic AMD without filters)
    total = len(events)
    if total == 0:
        print("No events captured.")
        return

    wins = sum(e["is_win"] for e in events)
    base_wr = (wins / total) * 100.0
    base_ev = sum(e["realized_r"] for e in events) / total
    print(f"\n1. BASELINE (All Unfiltered AMD Events):")
    print(f"   Samples: {total} | Win Rate: {base_wr:.1f}% | Net EV: {base_ev:+.3f}R | Harvest Rate: {(sum(e['harvest_hit'] for e in events)/total)*100:.1f}%")

    # Parameter sweeps
    filters_to_test = [
        # Accumulation Score Cutoffs
        ("Accumulation Score >= 55", lambda e: e["accumulation_score"] >= 55),
        ("Accumulation Score >= 65", lambda e: e["accumulation_score"] >= 65),
        ("Accumulation Score >= 75", lambda e: e["accumulation_score"] >= 75),

        # Sweep Size
        ("Sweep Size <= 1.0 ATR (Exhaustion, not breakout)", lambda e: e["sweep_size_atr"] <= 1.0),
        ("Sweep Size between 0.2 - 0.8 ATR", lambda e: 0.2 <= e["sweep_size_atr"] <= 0.8),
        ("Wick % >= 40% (Strong rejection)", lambda e: e["wick_pct"] >= 40.0),

        # Displacement & FVG
        ("FVG Required (has_fvg == True)", lambda e: e["has_fvg"]),
        ("Displacement Score >= 50", lambda e: e["displacement_score"] >= 50),
        ("Displacement Score >= 65", lambda e: e["displacement_score"] >= 65),

        # MTF Context
        ("MTF Trend Aligned Only", lambda e: e["mtf_aligned"]),
        ("Counter-MTF Trend Fade", lambda e: not e["mtf_aligned"]),

        # Sessions
        ("London & New York Sessions", lambda e: e["session"] in ["LONDON", "NEW_YORK"]),
        ("Asia Session", lambda e: e["session"] == "ASIA"),

        # Composite V4 Architecture Model
        ("COMPOSITE MODEL A: Acc >= 60 + Disp >= 50 + FVG", 
         lambda e: e["accumulation_score"] >= 60 and e["displacement_score"] >= 50 and e["has_fvg"]),
        ("COMPOSITE MODEL B: Acc >= 65 + Sweep <= 0.8 ATR + Wick >= 35%", 
         lambda e: e["accumulation_score"] >= 65 and e["sweep_size_atr"] <= 0.8 and e["wick_pct"] >= 35.0),
        ("COMPOSITE MODEL C: Acc >= 65 + Disp >= 55 + MTF Aligned + London/NY", 
         lambda e: e["accumulation_score"] >= 65 and e["displacement_score"] >= 55 and e["mtf_aligned"] and e["session"] in ["LONDON", "NEW_YORK"]),
        ("OPTIMAL FAILURE REVERSAL: Acc >= 60 + Disp >= 50 + Sweep <= 1.0 ATR + Wick >= 30% + London/NY",
         lambda e: e["accumulation_score"] >= 60 and e["displacement_score"] >= 50 and e["sweep_size_atr"] <= 1.0 and e["wick_pct"] >= 30.0 and e["session"] in ["LONDON", "NEW_YORK"])
    ]

    print("\n2. PARAMETER PERMUTATION MATRIX:")
    print(f"{'FILTER / RULE COMBINATION':<48} | {'N':<6} | {'WIN %':<8} | {'NET EV':<10} | {'HARVEST %'}")
    print("-" * 85)

    best_model = None
    best_ev = -999.0

    results_table = []

    for name, fn in filters_to_test:
        subset = [e for e in events if fn(e)]
        sub_n = len(subset)
        if sub_n < 10:
            continue
        sub_w = sum(e["is_win"] for e in subset)
        sub_wr = (sub_w / sub_n) * 100.0
        sub_ev = sum(e["realized_r"] for e in subset) / sub_n
        sub_harv = (sum(e["harvest_hit"] for e in subset) / sub_n) * 100.0
        
        results_table.append({
            "name": name,
            "count": sub_n,
            "win_rate": round(sub_wr, 1),
            "net_ev": round(sub_ev, 3),
            "harvest_rate": round(sub_harv, 1)
        })

        if sub_n >= 25 and sub_ev > best_ev:
            best_ev = sub_ev
            best_model = (name, sub_n, sub_wr, sub_ev, sub_harv)

        status_flag = " ⭐" if sub_ev >= 0.20 and sub_wr >= 60.0 else ""
        print(f"{name:<48} | {sub_n:<6} | {sub_wr:>6.1f}% | {sub_ev:>+8.3f}R | {sub_harv:>8.1f}%{status_flag}")

    print("-" * 85)
    if best_model:
        print(f"\n>>> HIGHEST EV CONFIGURATION: {best_model[0]}")
        print(f"    Sample Size : {best_model[1]} trades")
        print(f"    Win Rate    : {best_model[2]:.1f}%")
        print(f"    Expected Net: {best_model[3]:+.3f}R per trade (After 1.5 bps slippage + 11.0 bps fees)")
        print(f"    Harvest Rate: {best_model[4]:.1f}% triggered (+0.40% locked)")

    # Save detailed forensic log and results
    log_output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_events_collected": total,
        "baseline": {
            "win_rate": round(base_wr, 1),
            "net_ev": round(base_ev, 3),
            "count": total
        },
        "permutations": results_table,
        "sample_forensic_records": events[:15]
    }
    
    out_file = os.path.join(RESULTS_DIR, "amd_pipeline_research_results.json")
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(log_output, f, indent=2)
    print(f"\n[Saved] Full forensic dataset saved to: {out_file}")

if __name__ == "__main__":
    events = scan_amd_pipeline()
    analyze_variable_permutations(events)
