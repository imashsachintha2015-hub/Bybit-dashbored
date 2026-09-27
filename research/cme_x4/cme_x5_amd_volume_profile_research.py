#!/usr/bin/env python3
"""
CME-X5 MULTI-FACTOR MICROSTRUCTURE RESEARCH ENGINE
=============================================================================
Sequential Pipeline:
  Accumulation -> Liquidity Manipulation -> Displacement -> FVG -> POC/Value -> Outcome

Evaluates:
  1. Feature Vector Xt = [At, Mt, Ft, Pt, Vt, Dt, Rt]
  2. Interaction Terms: M x F, M x F x P, M x F x P x V
  3. Three Situations:
     - Situation A: Sweep & Retest Continuation
     - Situation B: Sweep Rejection (Failure to reclaim POC)
     - Situation C: Trap Reversal (Sweep + FVG + POC Reclaim + Reversal)
  4. Hypothesis Test: Simple FVG vs Asymmetric Confluence (Sweep + POC + Disp + FVG)
  5. Chronological Out-of-Sample (60% Train / 40% Holdout) Ablation Matrix:
     - X4 Baseline
     - X4 + Accumulation (A)
     - X4 + Manipulation (M)
     - X4 + FVG (F)
     - X4 + POC (P)
     - X4 + Volume Profile (V)
     - X4 + AMD Sequence (A x M x F)
     - X4 + Full Confluence (A x M x F x P x V)
  6. Realistic Friction: 1.5 bps slippage + 11.0 bps fees = 14.0 bps round-trip
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

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "AVAXUSDT", "LINKUSDT", "XRPUSDT", "DOGEUSDT"]

# Friction constants
TOTAL_FRICTION_PCT = 0.0014  # 14.0 bps total round-trip friction

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

def compute_volume_profile(bars, num_bins=30):
    """
    Constructs Volume Profile across window bars:
    Returns:
      poc_price: Point of Control (highest volume bin)
      vah: Value Area High (70% volume upper bound)
      val: Value Area Low (70% volume lower bound)
      hvn_levels: list of High Volume Node levels
      lvn_levels: list of Low Volume Node levels
      profile_concentration: fraction of volume in Value Area
    """
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
        # Distribute volume across overlapping bins
        c_low = b["low"]
        c_high = b["high"]
        c_range = max(1e-8, c_high - c_low)
        
        for i in range(num_bins):
            b_low = min_p + i * bin_size
            b_high = b_low + bin_size
            
            # Calculate overlap
            overlap_low = max(c_low, b_low)
            overlap_high = min(c_high, b_high)
            if overlap_high > overlap_low:
                overlap_frac = (overlap_high - overlap_low) / c_range
                bin_volumes[i] += vol * overlap_frac

    if total_vol <= 0:
        return None

    # Find POC
    max_vol_bin = 0
    max_bin_vol = 0.0
    for i in range(num_bins):
        if bin_volumes[i] > max_bin_vol:
            max_bin_vol = bin_volumes[i]
            max_vol_bin = i
    poc_price = bin_centers[max_vol_bin]

    # Calculate 70% Value Area
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

    # Detect HVN and LVN
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

def evaluate_cme_x5_candidates():
    print("=" * 75)
    print("  CME-X5 MULTI-FACTOR MICROSTRUCTURE RESEARCH ENGINE")
    print("  Testing: Accumulation -> Sweep -> Displacement -> FVG -> POC -> Retest")
    print("=" * 75)

    all_samples = []

    for sym in SYMBOLS:
        bars = load_klines(sym, "15")
        if len(bars) < 200:
            continue
            
        atrs = calc_atr(bars, 14)
        closes = [b["close"] for b in bars]
        ema21 = calc_ema(closes, 21)
        ema50 = calc_ema(closes, 50)
        
        acc_window = 24  # 6-hour accumulation
        
        i = acc_window + 2
        while i < len(bars) - 40:
            atr_val = atrs[i]
            if atr_val <= 0:
                i += 1
                continue
                
            window = bars[i-acc_window:i]
            vp = compute_volume_profile(window)
            if not vp:
                i += 1
                continue
                
            highs = [b["high"] for b in window]
            lows = [b["low"] for b in window]
            vols = [b["volume"] for b in window]
            
            range_val = max(highs) - min(lows)
            range_pct = (range_val / min(lows)) * 100.0
            
            # --- 1. Accumulation Quality (At in [0, 1]) ---
            expected_exp = atr_val * math.sqrt(acc_window)
            range_ratio = range_val / expected_exp if expected_exp > 0 else 1.0
            range_score = max(0.0, min(1.0, (2.0 - range_ratio) / 1.0))
            
            avg_vol = sum(vols) / acc_window
            recent_vol = sum(vols[-8:]) / 8.0
            vol_score = max(0.0, min(1.0, (1.2 - (recent_vol / avg_vol)) / 0.6)) if avg_vol > 0 else 0.5
            
            # Value area concentration
            vp_conc_score = max(0.0, min(1.0, (vp["concentration"] - 0.60) / 0.20))
            
            At = round((range_score * 0.40) + (vol_score * 0.35) + (vp_conc_score * 0.25), 3)
            
            if At < 0.40:
                i += 1
                continue
                
            acc_h = vp["max_p"]
            acc_l = vp["min_p"]
            poc = vp["poc"]
            val = vp["val"]
            vah = vp["vah"]
            
            # --- 2. Manipulation Sweep Detection (Mt in [0, 1]) ---
            sweep_found = False
            sweep_dir = None
            sweep_idx = -1
            sweep_extreme = 0.0
            
            for s in range(1, 5):
                sidx = i + s
                if sidx >= len(bars) - 30:
                    break
                sbar = bars[sidx]
                if sbar["high"] > acc_h:
                    sweep_found = True
                    sweep_dir = "SHORT"  # Upside sweep -> fade to short
                    sweep_idx = sidx
                    sweep_extreme = sbar["high"]
                    break
                elif sbar["low"] < acc_l:
                    sweep_found = True
                    sweep_dir = "LONG"   # Downside sweep -> fade to long
                    sweep_idx = sidx
                    sweep_extreme = sbar["low"]
                    break
                    
            if not sweep_found:
                i += 1
                continue
                
            sbar = bars[sweep_idx]
            s_atr = atrs[sweep_idx]
            
            if sweep_dir == "SHORT":
                sweep_dist = sweep_extreme - acc_h
                wick = sbar["high"] - max(sbar["open"], sbar["close"])
            else:
                sweep_dist = acc_l - sweep_extreme
                wick = min(sbar["open"], sbar["close"]) - sbar["low"]
                
            bar_tot_range = max(1e-8, sbar["high"] - sbar["low"])
            wick_ratio = wick / bar_tot_range
            sweep_atr_ratio = sweep_dist / s_atr if s_atr > 0 else 0.0
            
            # Mt score: optimal sweep is 0.2 - 0.8 ATR with >40% wick
            sweep_size_score = max(0.0, 1.0 - abs(sweep_atr_ratio - 0.45) / 0.55)
            wick_score = min(1.0, wick_ratio / 0.50)
            Mt = round((sweep_size_score * 0.50) + (wick_score * 0.50), 3)

            # --- 3. Displacement & FVG Detection (Ft, Dt in [0, 1]) ---
            reclaim_found = False
            reclaim_idx = -1
            
            for r in range(1, 4):
                ridx = sweep_idx + r
                if ridx >= len(bars) - 25:
                    break
                rbar = bars[ridx]
                if sweep_dir == "SHORT" and rbar["close"] < acc_h:
                    reclaim_found = True
                    reclaim_idx = ridx
                    break
                elif sweep_dir == "LONG" and rbar["close"] > acc_l:
                    reclaim_found = True
                    reclaim_idx = ridx
                    break
                    
            if not reclaim_found:
                i = sweep_idx + 2
                continue
                
            dbar = bars[reclaim_idx]
            dprev = bars[reclaim_idx - 1]
            dprev2 = bars[reclaim_idx - 2]
            d_atr = atrs[reclaim_idx]
            
            has_fvg = False
            fvg_size = 0.0
            if sweep_dir == "SHORT":
                if dprev2["low"] > dbar["high"]:
                    has_fvg = True
                    fvg_size = dprev2["low"] - dbar["high"]
                body = dbar["open"] - dbar["close"]
                close_loc = (dbar["high"] - dbar["close"]) / (dbar["high"] - dbar["low"]) if (dbar["high"] - dbar["low"]) > 0 else 0.5
            else:
                if dbar["low"] > dprev2["high"]:
                    has_fvg = True
                    fvg_size = dbar["low"] - dprev2["high"]
                body = dbar["close"] - dbar["open"]
                close_loc = (dbar["close"] - dbar["low"]) / (dbar["high"] - dbar["low"]) if (dbar["high"] - dbar["low"]) > 0 else 0.5

            fvg_atr = fvg_size / d_atr if d_atr > 0 else 0.0
            Ft = round(min(1.0, fvg_atr / 0.40) if has_fvg else 0.0, 3)
            
            body_atr = max(0.0, body / d_atr) if d_atr > 0 else 0.0
            vol_surge = dbar["volume"] / dprev["volume"] if dprev["volume"] > 0 else 1.0
            Dt = round(min(1.0, (body_atr * 0.45) + (close_loc * 0.30) + (min(2.0, vol_surge)/2.0 * 0.25)), 3)

            # --- 4. POC & Value Area Relationship (Pt, Vt in [0, 1]) ---
            entry_p = dbar["close"]
            reclaimed_poc = False
            reclaimed_value_area = False
            
            if sweep_dir == "SHORT":
                # Pierced acc_high, now closed back inside. Did it cross below POC or VAH?
                if entry_p < poc:
                    reclaimed_poc = True
                if entry_p < vah:
                    reclaimed_value_area = True
                # Pt: Proximity & reclaim of POC
                dist_to_poc = (poc - entry_p) / range_val if range_val > 0 else 0.0
            else:
                if entry_p > poc:
                    reclaimed_poc = True
                if entry_p > val:
                    reclaimed_value_area = True
                dist_to_poc = (entry_p - poc) / range_val if range_val > 0 else 0.0

            Pt = 1.0 if reclaimed_poc else (0.60 if reclaimed_value_area else 0.20)
            
            # Vt: Volume Profile Asymmetry & LVN/HVN transition
            Vt = round(min(1.0, vp["concentration"] * (1.2 if (reclaimed_poc and reclaimed_value_area) else 0.8)), 3)

            # --- 5. Regime / MTF Alignment (Rt in [0, 1]) ---
            trend_bull = ema21[reclaim_idx] > ema50[reclaim_idx]
            mtf_aligned = (sweep_dir == "LONG" and trend_bull) or (sweep_dir == "SHORT" and not trend_bull)
            Rt = 1.0 if mtf_aligned else 0.35

            # --- 6. Situation Classification (A, B, or C) ---
            # Situation A: Continuation (retest of value holds)
            # Situation B: Rejection (sweep fails to hold reclaim, falls back to sweep direction)
            # Situation C: Trap Reversal (Classic CME-X4: high volume sweep + violent displacement + FVG + POC reclaim)
            if (has_fvg or Ft >= 0.20) and Pt >= 0.60 and Dt >= 0.40:
                situation = "SITUATION_C_TRAP_REVERSAL"
            elif Pt < 0.50:
                situation = "SITUATION_B_WEAK_RECLAIM"
            else:
                situation = "SITUATION_A_VALUE_EXPANSION"

            # --- 7. Execution Simulation (Chronological Holdout Ready) ---
            stop_p = sweep_extreme
            if sweep_dir == "SHORT":
                risk = stop_p - entry_p
                tp1_p = entry_p * (1.0 - 0.0040)       # Staged harvest +0.40%
                prot_stop_p = entry_p * (1.0 - 0.0025) # Protected breakeven +0.25%
                tp2_p = entry_p - (risk * 2.0)         # 2.0R target
            else:
                risk = entry_p - stop_p
                tp1_p = entry_p * (1.0 + 0.0040)
                prot_stop_p = entry_p * (1.0 + 0.0025)
                tp2_p = entry_p + (risk * 2.0)

            risk_pct = (risk / entry_p) * 100.0
            if risk_pct < 0.20 or risk_pct > 3.0:
                i = reclaim_idx + 1
                continue

            harvest_hit = False
            exit_reason = "TIMEOUT"
            realized_r = 0.0
            mfe_pct = 0.0
            mae_pct = 0.0

            for fwd in range(1, 37):
                fidx = reclaim_idx + fwd
                if fidx >= len(bars):
                    break
                fbar = bars[fidx]

                if sweep_dir == "SHORT":
                    cur_fav = (entry_p - fbar["low"]) / entry_p * 100.0
                    cur_adv = (fbar["high"] - entry_p) / entry_p * 100.0
                    mfe_pct = max(mfe_pct, cur_fav)
                    mae_pct = max(mae_pct, cur_adv)

                    if not harvest_hit and fbar["low"] <= tp1_p:
                        harvest_hit = True

                    if harvest_hit:
                        if fbar["low"] <= tp2_p:
                            exit_reason = "FULL_2R_TARGET"
                            realized_r = 2.0 - (TOTAL_FRICTION_PCT / (risk / entry_p))
                            break
                        elif fbar["high"] >= prot_stop_p:
                            exit_reason = "PROTECTED_STOP"
                            realized_r = (0.25 / risk_pct) - (TOTAL_FRICTION_PCT / (risk / entry_p))
                            break
                    else:
                        if fbar["high"] >= stop_p:
                            exit_reason = "STOP_LOSS"
                            realized_r = -1.0 - (TOTAL_FRICTION_PCT / (risk / entry_p))
                            break
                else: # LONG
                    cur_fav = (fbar["high"] - entry_p) / entry_p * 100.0
                    cur_adv = (entry_p - fbar["low"]) / entry_p * 100.0
                    mfe_pct = max(mfe_pct, cur_fav)
                    mae_pct = max(mae_pct, cur_adv)

                    if not harvest_hit and fbar["high"] >= tp1_p:
                        harvest_hit = True

                    if harvest_hit:
                        if fbar["high"] >= tp2_p:
                            exit_reason = "FULL_2R_TARGET"
                            realized_r = 2.0 - (TOTAL_FRICTION_PCT / (risk / entry_p))
                            break
                        elif fbar["low"] <= prot_stop_p:
                            exit_reason = "PROTECTED_STOP"
                            realized_r = (0.25 / risk_pct) - (TOTAL_FRICTION_PCT / (risk / entry_p))
                            break
                    else:
                        if fbar["low"] <= stop_p:
                            exit_reason = "STOP_LOSS"
                            realized_r = -1.0 - (TOTAL_FRICTION_PCT / (risk / entry_p))
                            break

            sample_record = {
                "timestamp": dbar["start"],
                "symbol": sym,
                "direction": sweep_dir,
                "situation": situation,
                "At": At,
                "Mt": Mt,
                "Ft": Ft,
                "Pt": Pt,
                "Vt": Vt,
                "Dt": Dt,
                "Rt": Rt,
                "M_x_F": round(Mt * Ft, 3),
                "M_x_F_x_P": round(Mt * Ft * Pt, 3),
                "M_x_F_x_P_x_V": round(Mt * Ft * Pt * Vt, 3),
                "has_fvg": has_fvg,
                "reclaimed_poc": reclaimed_poc,
                "reclaimed_vah_val": reclaimed_value_area,
                "mtf_aligned": mtf_aligned,
                "risk_pct": round(risk_pct, 3),
                "mfe_pct": round(mfe_pct, 3),
                "mae_pct": round(mae_pct, 3),
                "harvest_hit": harvest_hit,
                "exit_reason": exit_reason,
                "realized_r": round(realized_r, 3),
                "is_win": 1 if realized_r > 0 else 0
            }
            all_samples.append(sample_record)
            i = reclaim_idx + 8

    # Sort strictly chronologically for out-of-sample partitioning
    all_samples.sort(key=lambda x: x["timestamp"])
    print(f"\nTotal Multi-Factor Microstructure Samples Harvested: {len(all_samples)}")
    return all_samples

def evaluate_metrics(subset):
    n = len(subset)
    if n == 0:
        return {"n": 0, "win_rate": 0.0, "net_ev": 0.0, "profit_factor": 0.0, "harvest_rate": 0.0}
    w = sum(s["is_win"] for s in subset)
    wr = (w / n) * 100.0
    ev = sum(s["realized_r"] for s in subset) / n
    harv = (sum(s["harvest_hit"] for s in subset) / n) * 100.0
    
    gross_gains = sum(s["realized_r"] for s in subset if s["realized_r"] > 0)
    gross_losses = abs(sum(s["realized_r"] for s in subset if s["realized_r"] < 0))
    pf = (gross_gains / gross_losses) if gross_losses > 0 else (99.0 if gross_gains > 0 else 0.0)
    
    return {
        "n": n,
        "win_rate": round(wr, 1),
        "net_ev": round(ev, 3),
        "profit_factor": round(pf, 2),
        "harvest_rate": round(harv, 1)
    }

def run_comprehensive_analysis(samples):
    total = len(samples)
    if total == 0:
        return

    # Chronological Holdout Split: 60% Train (In-Sample), 40% Test (Out-of-Sample)
    split_idx = int(total * 0.60)
    train_data = samples[:split_idx]
    test_data = samples[split_idx:]
    
    print("\n" + "=" * 75)
    print(f"  PARTITIONING: {len(train_data)} IN-SAMPLE (60%) | {len(test_data)} OUT-OF-SAMPLE HOLDOUT (40%)")
    print("=" * 75)

    # 1. Test Situations (A, B, C)
    print("\n--- 1. SITUATION ANALYSIS (A vs B vs C) ---")
    print(f"{'SITUATION':<32} | {'SAMPLES':<8} | {'WIN RATE':<10} | {'NET EV':<10} | {'PROFIT FACTOR'}")
    print("-" * 75)
    for sit in ["SITUATION_A_VALUE_EXPANSION", "SITUATION_B_WEAK_RECLAIM", "SITUATION_C_TRAP_REVERSAL"]:
        m = evaluate_metrics([s for s in samples if s["situation"] == sit])
        print(f"{sit:<32} | {m['n']:<8} | {m['win_rate']:>7.1f}% | {m['net_ev']:>+8.3f}R | {m['profit_factor']:>10.2f}")

    # 2. Hypothesis Test: Simple FVG vs Asymmetric Confluence
    print("\n--- 2. CORE HYPOTHESIS TEST ---")
    print("Hypothesis: Asymmetric Confluence (Sweep + POC Reclaim + Disp + FVG) >> Simple FVG")
    simple_fvg = [s for s in samples if s["has_fvg"]]
    asymmetric_confluence = [s for s in samples if s["has_fvg"] and s["reclaimed_poc"] and s["Dt"] >= 0.50 and s["Mt"] >= 0.40]
    
    m_simple = evaluate_metrics(simple_fvg)
    m_asym = evaluate_metrics(asymmetric_confluence)
    
    print(f"  Simple FVG Alone            : N={m_simple['n']:<3} | Win Rate: {m_simple['win_rate']:.1f}% | Net EV: {m_simple['net_ev']:+.3f}R | PF: {m_simple['profit_factor']}")
    print(f"  Asymmetric Confluence Model : N={m_asym['n']:<3} | Win Rate: {m_asym['win_rate']:.1f}% | Net EV: {m_asym['net_ev']:+.3f}R | PF: {m_asym['profit_factor']} ⭐")

    # 3. Interaction Terms Multiplier Test
    print("\n--- 3. INTERACTION TERMS MULTIPLIER ANALYSIS (Out-of-Sample 40%) ---")
    print(f"{'INTERACTION LEVEL':<42} | {'N (OOS)':<8} | {'OOS WIN %':<10} | {'OOS NET EV':<12} | {'PF (OOS)'}")
    print("-" * 85)
    
    interactions = [
        ("Baseline (All Events)", lambda s: True),
        ("M alone (Mt >= 0.50 Clean Sweep)", lambda s: s["Mt"] >= 0.50),
        ("F alone (has_fvg == True)", lambda s: s["has_fvg"]),
        ("P alone (Pt >= 0.60 Reclaimed Value Area)", lambda s: s["Pt"] >= 0.60),
        ("P strict (Pt == 1.0 Reclaimed POC)", lambda s: s["reclaimed_poc"]),
        ("M x P (Sweep + Value Area Reclaim)", lambda s: s["Mt"] >= 0.50 and s["Pt"] >= 0.60),
        ("M x P x D (Sweep + Value + Displacement)", lambda s: s["Mt"] >= 0.50 and s["Pt"] >= 0.60 and s["Dt"] >= 0.50),
        ("M x F x P (Sweep + FVG + Value Reclaim)", lambda s: s["Mt"] >= 0.50 and s["has_fvg"] and s["Pt"] >= 0.60),
        ("CME-X5 FULL ASYMMETRIC (At>=0.55 + MxPxD + Rt)", 
         lambda s: s["At"] >= 0.55 and s["Mt"] >= 0.50 and s["Pt"] >= 0.60 and s["Dt"] >= 0.50 and s["mtf_aligned"])
    ]
    
    for label, fn in interactions:
        oos_m = evaluate_metrics([s for s in test_data if fn(s)])
        print(f"{label:<42} | {oos_m['n']:<8} | {oos_m['win_rate']:>7.1f}% | {oos_m['net_ev']:>+9.3f}R | {oos_m['profit_factor']:>8.2f}")

    # 4. Step-by-Step CME-X4 Ablation Matrix on Chronological Holdout (Unseen Future Data)
    print("\n--- 4. CME-X4 STEP-BY-STEP ABLATION ON UNSEEN CHRONOLOGICAL HOLDOUT (40%) ---")
    print(f"{'PIPELINE ARCHITECTURE':<45} | {'N':<6} | {'WIN %':<8} | {'NET EV':<10} | {'PF':<6} | {'STATUS'}")
    print("-" * 88)

    ablations = [
        ("1. X4 Baseline (All Sweeps Unfiltered)", lambda s: True),
        ("2. X4 + Accumulation (At >= 0.60)", lambda s: s["At"] >= 0.60),
        ("3. X4 + Manipulation Sweep (Mt >= 0.55)", lambda s: s["Mt"] >= 0.55),
        ("4. X4 + FVG Imbalance (has_fvg == True)", lambda s: s["has_fvg"]),
        ("5. X4 + Value Area Reclaim (Pt >= 0.60)", lambda s: s["Pt"] >= 0.60),
        ("6. X4 + POC Reclaim (reclaimed_poc == True)", lambda s: s["reclaimed_poc"]),
        ("7. X4 + Volume Profile Concentration (Vt >= 0.58)", lambda s: s["Vt"] >= 0.58),
        ("8. X4 + AMD Sequence (At>=0.55 & Mt>=0.50 & Dt>=0.50)", 
         lambda s: s["At"] >= 0.55 and s["Mt"] >= 0.50 and s["Dt"] >= 0.50),
        ("9. CME-X5 COMPOSITE (At>=0.55 + Mt>=0.50 + Pt>=0.60 + Dt>=0.50)", 
         lambda s: s["At"] >= 0.55 and s["Mt"] >= 0.50 and s["Pt"] >= 0.60 and s["Dt"] >= 0.50),
        ("10. CME-X5 INSTITUTIONAL (Model 9 + MTF Trend Aligned)", 
         lambda s: s["At"] >= 0.55 and s["Mt"] >= 0.50 and s["Pt"] >= 0.60 and s["Dt"] >= 0.50 and s["mtf_aligned"])
    ]

    ablation_results = []
    for label, fn in ablations:
        oos_m = evaluate_metrics([s for s in test_data if fn(s)])
        is_best = oos_m["net_ev"] > 0.05 and oos_m["win_rate"] >= 70.0
        status = "⭐ EDGE CONFIRMED" if is_best else ("MARGINAL" if oos_m["net_ev"] > 0 else "DRAG")
        
        ablation_results.append({
            "model": label,
            "metrics": oos_m,
            "status": status
        })
        print(f"{label:<40} | {oos_m['n']:<6} | {oos_m['win_rate']:>6.1f}% | {oos_m['net_ev']:>+8.3f}R | {oos_m['profit_factor']:>5.2f} | {status}")

    print("-" * 80)

    # Save full JSON report
    report_data = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_samples": total,
        "train_samples": len(train_data),
        "holdout_samples": len(test_data),
        "core_hypothesis": {
            "simple_fvg": m_simple,
            "asymmetric_confluence": m_asym
        },
        "ablations_holdout": ablation_results
    }
    
    out_json = os.path.join(RESULTS_DIR, "cme_x5_research_results.json")
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(report_data, f, indent=2)
    print(f"\n[Saved] Complete CME-X5 Research Dataset saved to: {out_json}")

if __name__ == "__main__":
    samples = evaluate_cme_x5_candidates()
    run_comprehensive_analysis(samples)
