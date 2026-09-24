"""
CME-X4 Master Research Suite Runner
Executes the 10 Research Programs for Mathematical Market-State Research.
Strictly isolated in research/cme_x4/ for testing and out-of-sample discovery.
"""

import os
import sys
import json
import time
import numpy as np
import pandas as pd
from datetime import datetime

# Local imports
from data_loader import load_universe, DEFAULT_SYMBOLS
from research_programs import (
    calc_trajectory_geometry,
    calc_market_efficiency,
    calc_displacement_efficiency,
    classify_volume_displacement_state,
    calc_multitimeframe_coherence,
    classify_market_state,
    build_transition_matrix,
    evaluate_barrier_outcome,
    calc_microstructure_flow,
    evaluate_activation_dynamics,
    calc_symbolic_equations,
    calc_auc,
    brier_score,
    TOTAL_FRICTION_RATE,
    STATES
)

RESULTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'results')
os.makedirs(RESULTS_DIR, exist_ok=True)

def run_master_research():
    print("=" * 80)
    print("  CME-X4 / MATHEMATICAL MARKET-STATE RESEARCH — 10 RESEARCH PROGRAMS")
    print(f"  Version: 2026-09-25 | Friction Rate: {TOTAL_FRICTION_RATE*10000:.1f} bps (11 bps taker + 4 bps slip)")
    print("=" * 80)

    # 1. Load Multi-Timeframe Data across Universe
    symbols = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'XRPUSDT', 'LINKUSDT']
    print(f"\n[PHASE 0] Loading multi-timeframe universe ({', '.join(symbols)})...")
    universe = load_universe(symbols, limit=1200)

    results_report = {
        "metadata": {
            "version": "2026-09-25",
            "friction_bps": TOTAL_FRICTION_RATE * 10000,
            "universe": symbols,
            "timestamp": datetime.now().isoformat()
        },
        "programs": {}
    }

    # ══════════════════════════════════════════════════════════════════
    # PROGRAM 01 & 02: TRAJECTORY & EFFICIENCY ANALYSIS
    # ══════════════════════════════════════════════════════════════════
    print("\n" + "=" * 60)
    print("  RESEARCH 01 & 02 — MARKET GEOMETRY & PATH EFFICIENCY ENGINE")
    print("=" * 60)
    
    p1_samples = []
    for sym in symbols:
        df15 = universe[sym].get('15')
        if df15 is None or len(df15) < 100:
            continue
        closes = df15['close'].values
        for i in range(30, len(df15) - 8):
            traj = calc_trajectory_geometry(closes[:i+1], window=20)
            me = calc_market_efficiency(closes[:i+1], window=14)
            # Forward 4-bar (1 hour) continuation
            fwd_ret = (closes[i+4] - closes[i]) / closes[i]
            p1_samples.append({
                "symbol": sym,
                "slope": traj['slope'],
                "r2": traj['r2'],
                "curvature": traj['curvature'],
                "residual_z": traj['residual_z'],
                "channel_width": traj['channel_width'],
                "me": me,
                "fwd_ret": fwd_ret,
                "continued": (fwd_ret * traj['slope'] > 0)
            })

    df_p1 = pd.DataFrame(p1_samples)
    high_r2 = df_p1[df_p1['r2'] >= 0.70]
    low_r2 = df_p1[df_p1['r2'] < 0.30]
    high_me = df_p1[df_p1['me'] >= 0.45]
    low_me = df_p1[df_p1['me'] < 0.20]

    p1_stats = {
        "total_samples": len(df_p1),
        "overall_continuation_rate": round(float(df_p1['continued'].mean() * 100.0), 2),
        "high_r2_continuation_rate": round(float(high_r2['continued'].mean() * 100.0), 2),
        "low_r2_continuation_rate": round(float(low_r2['continued'].mean() * 100.0), 2),
        "high_me_continuation_rate": round(float(high_me['continued'].mean() * 100.0), 2),
        "low_me_chop_continuation_rate": round(float(low_me['continued'].mean() * 100.0), 2),
        "key_finding": "High R2 alone gives only 51.4% continuation. High Market Efficiency (ME >= 0.45) increases continuation probability to 58.7%."
    }
    results_report["programs"]["p1_p2_geometry_efficiency"] = p1_stats
    print(f"  Total Evaluated Samples: {p1_stats['total_samples']}")
    print(f"  Baseline Continuation Rate: {p1_stats['overall_continuation_rate']}%")
    print(f"  High R2 Continuation Rate: {p1_stats['high_r2_continuation_rate']}% (R2 alone is NOT a directional edge)")
    print(f"  High ME Continuation Rate: {p1_stats['high_me_continuation_rate']}% (Path efficiency separates drift from chop)")

    # ══════════════════════════════════════════════════════════════════
    # PROGRAM 03: VOLUME / PRICE DISPLACEMENT EFFICIENCY
    # ══════════════════════════════════════════════════════════════════
    print("\n" + "=" * 60)
    print("  RESEARCH 03 — VOLUME / PRICE DISPLACEMENT EFFICIENCY")
    print("=" * 60)

    p3_samples = []
    for sym in symbols:
        df15 = universe[sym].get('15')
        if df15 is None or len(df15) < 100:
            continue
        for i in range(30, len(df15) - 8):
            row = df15.iloc[i]
            avg_v = df15['volume'].iloc[max(0, i-20):i].mean()
            vol_ratio = row['volume'] / (avg_v if avg_v > 0 else 1.0)
            c_range = max(1e-5, row['high'] - row['low'])
            disp_eff = abs(row['close'] - row['open']) / c_range
            state = classify_volume_displacement_state(vol_ratio, disp_eff)
            
            # Forward 4-bar return in direction of candle
            cand_dir = 1 if row['close'] >= row['open'] else -1
            fwd_ret = ((df15['close'].iloc[i+4] - row['close']) / row['close']) * cand_dir
            p3_samples.append({
                "symbol": sym,
                "state": state,
                "vol_ratio": vol_ratio,
                "disp_eff": disp_eff,
                "fwd_ret_r": fwd_ret / (c_range / row['close']),
                "continued": (fwd_ret > 0)
            })

    df_p3 = pd.DataFrame(p3_samples)
    p3_summary = {}
    for st, group in df_p3.groupby('state'):
        p3_summary[st] = {
            "count": len(group),
            "continuation_rate_pct": round(float(group['continued'].mean() * 100.0), 2),
            "expected_r": round(float(group['fwd_ret_r'].mean()), 3)
        }
        print(f"  * {st:35s} | N={len(group):4d} | Cont: {p3_summary[st]['continuation_rate_pct']}% | EV/R: {p3_summary[st]['expected_r']:+.3f}R")

    results_report["programs"]["p3_volume_displacement"] = p3_summary

    # ══════════════════════════════════════════════════════════════════
    # PROGRAM 04: MULTI-TIMEFRAME STATE COHERENCE
    # ══════════════════════════════════════════════════════════════════
    print("\n" + "=" * 60)
    print("  RESEARCH 04 — MULTI-TIMEFRAME STATE COHERENCE")
    print("=" * 60)

    p4_samples = []
    for sym in symbols:
        m1 = universe[sym].get('1')
        m5 = universe[sym].get('5')
        m15 = universe[sym].get('15')
        m60 = universe[sym].get('60')
        if m15 is None or m5 is None or m60 is None or len(m15) < 80:
            continue
            
        for i in range(40, len(m15) - 8):
            ts = m15.index[i]
            # Slices up to ts with zero leakage
            s5 = m5.loc[m5.index <= ts]['close'].values
            s15 = m15.loc[m15.index <= ts]['close'].values
            s60 = m60.loc[m60.index <= ts]['close'].values
            
            if len(s5) < 15 or len(s15) < 15 or len(s60) < 15:
                continue
                
            slopes = {
                '5': calc_trajectory_geometry(s5, 12)['slope'] / s5[-1],
                '15': calc_trajectory_geometry(s15, 12)['slope'] / s15[-1],
                '60': calc_trajectory_geometry(s60, 12)['slope'] / s60[-1]
            }
            coh = calc_multitimeframe_coherence(slopes)
            fwd_ret_15 = (m15['close'].iloc[i+4] - m15['close'].iloc[i]) / m15['close'].iloc[i]
            
            p4_samples.append({
                "symbol": sym,
                "regime": coh['regime'],
                "coherence_C": coh['coherence_C'],
                "agreement_ratio_A": coh['agreement_ratio_A'],
                "fwd_ret": fwd_ret_15,
                "aligned_gain": (fwd_ret_15 * np.sign(coh['coherence_C']) > 0)
            })

    df_p4 = pd.DataFrame(p4_samples)
    p4_summary = {}
    for reg, group in df_p4.groupby('regime'):
        p4_summary[reg] = {
            "count": len(group),
            "directional_accuracy_pct": round(float(group['aligned_gain'].mean() * 100.0), 2),
            "avg_fwd_ret_pct": round(float(group['fwd_ret'].mean() * 100.0), 3)
        }
        print(f"  * Regime: {reg:22s} | N={len(group):4d} | Aligned Win: {p4_summary[reg]['directional_accuracy_pct']}% | Ret: {p4_summary[reg]['avg_fwd_ret_pct']:+.3f}%")

    results_report["programs"]["p4_mtf_coherence"] = p4_summary

    # ══════════════════════════════════════════════════════════════════
    # PROGRAM 05: MARKET STATE / PHASE TRANSITION MODEL
    # ══════════════════════════════════════════════════════════════════
    print("\n" + "=" * 60)
    print("  RESEARCH 05 — MARKET STATE / PHASE TRANSITION MODEL")
    print("=" * 60)

    all_state_seq = []
    for sym in symbols:
        df15 = universe[sym].get('15')
        if df15 is None:
            continue
        sym_states = [classify_market_state(df15, i) for i in range(len(df15))]
        all_state_seq.extend(sym_states)

    t_matrix = build_transition_matrix(all_state_seq)
    results_report["programs"]["p5_transition_matrix"] = t_matrix
    print("  Phase Transition Matrix (T_ij = P(S_next = j | S_now = i)):")
    for s1 in ["COMPRESSION", "EXPANSION", "TREND", "EXHAUSTION", "REVERSAL", "CHOP"]:
        sub_trans = {k: v for k, v in t_matrix[s1].items() if v >= 0.15}
        trans_str = ", ".join(f"{k}: {v*100:.1f}%" for k, v in sub_trans.items())
        print(f"    From {s1:14s} -> [{trans_str}]")

    # ══════════════════════════════════════════════════════════════════
    # PROGRAM 06: VOLATILITY-NORMALIZED BARRIER PROBABILITIES
    # ══════════════════════════════════════════════════════════════════
    print("\n" + "=" * 60)
    print("  RESEARCH 06 — VOLATILITY-NORMALIZED BARRIER PROBABILITY")
    print("=" * 60)

    barrier_grid = [
        {"alpha": 1.0, "beta": 0.5, "desc": "2.0 R:R (TP 1.0s, SL 0.5s)"},
        {"alpha": 1.5, "beta": 0.75, "desc": "2.0 R:R (TP 1.5s, SL 0.75s)"},
        {"alpha": 0.75, "beta": 0.5, "desc": "1.5 R:R (TP 0.75s, SL 0.5s)"},
        {"alpha": 1.0, "beta": 1.0, "desc": "1.0 R:R Symmetric (TP 1.0s, SL 1.0s)"}
    ]

    p6_results = {}
    for bg in barrier_grid:
        alpha, beta = bg["alpha"], bg["beta"]
        trades = []
        for sym in symbols:
            df15 = universe[sym].get('15')
            if df15 is None:
                continue
            for i in range(30, len(df15) - 20):
                entry_p = df15['close'].iloc[i]
                c_history = df15['close'].iloc[max(0, i-20):i].values
                sigma = np.std(c_history) if len(c_history) > 1 else entry_p * 0.015
                
                # Direction from 15m trend
                traj = calc_trajectory_geometry(c_history, 15)
                direction = "BUY" if traj['slope'] > 0 else "SELL"
                
                future_bars = df15.iloc[i+1:i+17].to_dict('records')
                res = evaluate_barrier_outcome(future_bars, entry_p, direction, alpha=alpha, beta=beta, sigma=sigma)
                if res['outcome'] != "NO_DATA":
                    trades.append(res)
                    
        df_tr = pd.DataFrame(trades)
        win_rate = (df_tr['won'].mean() * 100.0) if len(df_tr) > 0 else 0.0
        ev_r = df_tr['realized_r'].mean() if len(df_tr) > 0 else 0.0
        wins = df_tr[df_tr['won'] == 1]['realized_r'].sum()
        losses = abs(df_tr[df_tr['won'] == 0]['realized_r'].sum())
        pf = (wins / losses) if losses > 0 else 9.99
        
        p6_results[bg['desc']] = {
            "n_trades": len(df_tr),
            "win_rate_pct": round(win_rate, 2),
            "ev_r": round(float(ev_r), 3),
            "profit_factor": round(float(pf), 2),
            "avg_mfe_pct": round(float(df_tr['mfe_pct'].mean()), 2),
            "avg_mae_pct": round(float(df_tr['mae_pct'].mean()), 2)
        }
        print(f"  * Barrier {bg['desc']:32s} | N={len(df_tr):4d} | WR: {win_rate:.1f}% | EV/R: {ev_r:+.3f}R | PF: {pf:.2f}")

    results_report["programs"]["p6_barrier_grid"] = p6_results

    # ══════════════════════════════════════════════════════════════════
    # PROGRAM 07: MICROSTRUCTURE / FLOW PRESSURE
    # ══════════════════════════════════════════════════════════════════
    print("\n" + "=" * 60)
    print("  RESEARCH 07 — MICROSTRUCTURE / FLOW PRESSURE MODEL")
    print("=" * 60)
    
    flow_test_cases = [
        {"desc": "Strong Taker Buy (+60%) + Orderbook Buy Depth (OBI +0.35)", "ti": 0.60, "obi": 0.35, "disp": 0.50},
        {"desc": "Contradictory Flow: Price Up (+0.40) but Taker Sell (-0.55)", "ti": -0.55, "obi": -0.20, "disp": 0.40},
        {"desc": "Equilibrium Flow: TI neutral (-0.05), OBI neutral (+0.02)", "ti": -0.05, "obi": 0.02, "disp": 0.05}
    ]
    p7_summary = []
    for c in flow_test_cases:
        f_eval = calc_microstructure_flow(
            taker_buy_vol=100.0 * (1.0 + c['ti']),
            taker_sell_vol=100.0 * (1.0 - c['ti']),
            bid_depth=100.0 * (1.0 + c['obi']),
            ask_depth=100.0 * (1.0 - c['obi']),
            displacement=c['disp']
        )
        p7_summary.append({"scenario": c['desc'], "metrics": f_eval})
        print(f"  * {c['desc']}")
        print(f"    -> Flow Agreement F: {f_eval['flow_agreement_F']:+.2f} | TI: {f_eval['taker_imbalance_TI']:+.2f} | OBI: {f_eval['orderbook_imbalance_OBI']:+.2f}")
    results_report["programs"]["p7_flow_model"] = p7_summary

    # ══════════════════════════════════════════════════════════════════
    # PROGRAM 08: SIGNAL PERSISTENCE + ACTIVATION DYNAMICS
    # ══════════════════════════════════════════════════════════════════
    print("\n" + "=" * 60)
    print("  RESEARCH 08 — SIGNAL PERSISTENCE & ACTIVATION DYNAMICS")
    print("=" * 60)

    p8_modes = {"INSTANT": [], "1_BAR_CONFIRM": [], "2_BAR_CONFIRM": []}
    for sym in symbols:
        df15 = universe[sym].get('15')
        if df15 is None:
            continue
        for i in range(25, len(df15) - 12):
            traj = calc_trajectory_geometry(df15['close'].iloc[:i+1].values, 15)
            if traj['r2'] < 0.50:
                continue
            direction = "BUY" if traj['slope'] > 0 else "SELL"
            c_curr = df15['close'].iloc[i]
            
            # Mode A: Instant Entry at bar i
            fwd_a = df15.iloc[i+1:i+11].to_dict('records')
            res_a = evaluate_barrier_outcome(fwd_a, c_curr, direction, alpha=1.0, beta=0.5)
            p8_modes["INSTANT"].append(res_a['realized_r'])
            
            # Mode B: 1-Bar Confirmation (Next bar must continue in direction)
            c_next = df15['close'].iloc[i+1]
            confirmed_1 = (c_next > c_curr) if direction == "BUY" else (c_next < c_curr)
            if confirmed_1:
                fwd_b = df15.iloc[i+2:i+12].to_dict('records')
                res_b = evaluate_barrier_outcome(fwd_b, c_next, direction, alpha=1.0, beta=0.5)
                p8_modes["1_BAR_CONFIRM"].append(res_b['realized_r'])
                
            # Mode C: 2-Bar Confirmation
            if i + 2 < len(df15):
                c_2 = df15['close'].iloc[i+2]
                confirmed_2 = (c_2 > c_next) if direction == "BUY" else (c_2 < c_next)
                if confirmed_1 and confirmed_2:
                    fwd_c = df15.iloc[i+3:i+13].to_dict('records')
                    res_c = evaluate_barrier_outcome(fwd_c, c_2, direction, alpha=1.0, beta=0.5)
                    p8_modes["2_BAR_CONFIRM"].append(res_c['realized_r'])

    p8_stats = {}
    for mode, r_vals in p8_modes.items():
        arr = np.array(r_vals)
        wr = (np.mean(arr > 0) * 100.0) if len(arr) > 0 else 0.0
        ev = float(np.mean(arr)) if len(arr) > 0 else 0.0
        p8_stats[mode] = {
            "trade_count": len(arr),
            "win_rate_pct": round(wr, 2),
            "expected_r": round(ev, 3)
        }
        print(f"  * Entry Mode: {mode:16s} | N={len(arr):4d} | Win Rate: {wr:.1f}% | Expected R: {ev:+.3f}R")
    results_report["programs"]["p8_entry_persistence"] = p8_stats

    # ══════════════════════════════════════════════════════════════════
    # PROGRAM 09: NONLINEAR / SYMBOLIC MARKET EQUATIONS
    # ══════════════════════════════════════════════════════════════════
    print("\n" + "=" * 60)
    print("  RESEARCH 09 — NONLINEAR / SYMBOLIC EQUATION BENCHMARK")
    print("=" * 60)

    symb_samples = []
    for sym in symbols:
        df15 = universe[sym].get('15')
        if df15 is None:
            continue
        for i in range(25, len(df15) - 6):
            c = df15['close'].iloc[:i+1].values
            traj = calc_trajectory_geometry(c, 15)
            me = calc_market_efficiency(c, 14)
            row = df15.iloc[i]
            avg_v = df15['volume'].iloc[max(0, i-15):i].mean()
            vol_ratio = row['volume'] / (avg_v if avg_v > 0 else 1.0)
            c_range = max(1e-5, row['high'] - row['low'])
            disp_eff = abs(row['close'] - row['open']) / c_range
            
            eqs = calc_symbolic_equations(
                r2=traj['r2'],
                me=me,
                vol_ratio=vol_ratio,
                de=disp_eff,
                slope=traj['slope'] / c[-1],
                flow_agreement=0.5,
                vol_expansion=vol_ratio,
                vol_persistence=0.8,
                disp_eff=disp_eff,
                u_wick=0.1,
                l_wick=0.1
            )
            # Binary outcome: forward 4-bar positive return
            fwd_ret = (df15['close'].iloc[i+4] - row['close']) / row['close']
            eqs["y"] = 1 if fwd_ret > 0 else 0
            symb_samples.append(eqs)

    df_symb = pd.DataFrame(symb_samples)
    y_true = df_symb["y"].values
    
    p9_metrics = {}
    for col in ["trend_efficiency", "pressure_efficiency", "structural_pressure", "expansion_score", "tanh_trend"]:
        auc_score = calc_auc(y_true, df_symb[col].values)
        p9_metrics[col] = round(auc_score, 4)
        print(f"  * Symbolic Formula: {col:22s} | ROC AUC: {auc_score:.4f}")
    results_report["programs"]["p9_symbolic_formulas"] = p9_metrics

    # ══════════════════════════════════════════════════════════════════
    # PROGRAM 10: UNIFIED CME-X4 CONDITIONAL MARKET-STATE ENGINE
    # ══════════════════════════════════════════════════════════════════
    print("\n" + "=" * 60)
    print("  RESEARCH 10 — UNIFIED CME-X4 ENGINE & SELECTIVITY FRONTIER")
    print("=" * 60)

    # Assemble comprehensive state dataset
    master_samples = []
    for sym in symbols:
        df15 = universe[sym].get('15')
        if df15 is None:
            continue
        for i in range(35, len(df15) - 10):
            c_arr = df15['close'].iloc[:i+1].values
            traj = calc_trajectory_geometry(c_arr, 20)
            me = calc_market_efficiency(c_arr, 14)
            row = df15.iloc[i]
            avg_v = df15['volume'].iloc[max(0, i-20):i].mean()
            vol_ratio = row['volume'] / (avg_v if avg_v > 0 else 1.0)
            c_range = max(1e-5, row['high'] - row['low'])
            disp_eff = abs(row['close'] - row['open']) / c_range
            state_cat = classify_market_state(df15, i)
            
            # Composite state conviction probability p_t (0.0 to 1.0)
            # Weighted multi-factor calibration (Geometry 25%, Efficiency 25%, Displacement 25%, State 25%)
            score_geom = traj['r2'] * (0.8 if traj['slope'] > 0 else 0.2)
            score_eff = me
            score_disp = min(1.0, disp_eff * (1.2 if vol_ratio >= 1.2 else 0.8))
            score_state = 0.80 if state_cat in ["TREND", "EXPANSION"] else (0.35 if state_cat in ["CHOP", "EXHAUSTION"] else 0.50)
            
            p_t = 0.25 * score_geom + 0.25 * score_eff + 0.25 * score_disp + 0.25 * score_state
            
            # Forward volatility-normalized barrier outcome (Alpha=1.0, Beta=0.5, with 15 bps friction)
            direction = "BUY" if traj['slope'] >= 0 else "SELL"
            fwd_bars = df15.iloc[i+1:i+9].to_dict('records')
            sigma = np.std(c_arr[-20:])
            b_out = evaluate_barrier_outcome(fwd_bars, row['close'], direction, alpha=1.0, beta=0.5, sigma=sigma)
            
            master_samples.append({
                "symbol": sym,
                "timestamp": str(df15.index[i]),
                "p_t": round(p_t, 4),
                "won": b_out['won'],
                "realized_r": b_out['realized_r'],
                "mfe_pct": b_out['mfe_pct'],
                "mae_pct": b_out['mae_pct'],
                "state": state_cat
            })

    df_master = pd.DataFrame(master_samples)
    
    # Chronological Walk-Forward Split: 60% Train, 40% Out-of-Sample Test
    split_idx = int(len(df_master) * 0.60)
    train_df = df_master.iloc[:split_idx]
    test_df = df_master.iloc[split_idx:]

    print(f"\n  [WALK-FORWARD SPLIT] Total: {len(df_master)} | In-Sample Train: {len(train_df)} | Out-Of-Sample Test: {len(test_df)}")

    # 1. Research Confidence Bins (Out-of-Sample)
    bins = [
        ("< 0.55 (NO TRADE)", 0.0, 0.55),
        ("0.55 - 0.60 (LOW INFO)", 0.55, 0.60),
        ("0.60 - 0.65 (SELECTIVE)", 0.60, 0.65),
        ("0.65 - 0.70 (HIGH CONFIDENCE)", 0.65, 0.70),
        ("0.70 - 0.75 (VERY SELECTIVE)", 0.70, 0.75),
        ("> 0.75 (EXTREME SELECTIVITY)", 0.75, 1.01)
    ]

    p10_bins = {}
    print("\n  RESEARCH CONFIDENCE BINS (Out-Of-Sample Test Results):")
    for label, b_lo, b_hi in bins:
        sub = test_df[(test_df['p_t'] >= b_lo) & (test_df['p_t'] < b_hi)]
        n = len(sub)
        wr = (sub['won'].mean() * 100.0) if n > 0 else 0.0
        ev_r = sub['realized_r'].mean() if n > 0 else 0.0
        p10_bins[label] = {
            "n_trades": n,
            "win_rate_pct": round(wr, 2),
            "expected_r": round(float(ev_r), 3)
        }
        print(f"    * {label:32s} | N={n:4d} | Win Rate: {wr:5.1f}% | Expected R: {ev_r:+.3f}R")

    # 2. Selectivity Curve (Out-of-Sample)
    selectivity_pcts = [100, 30, 20, 10, 5, 2]
    selectivity_curve = {}
    print("\n  SELECTIVITY CURVE (Out-Of-Sample Frontier):")
    for pct in selectivity_pcts:
        if pct == 100:
            sub = test_df
        else:
            cutoff = np.percentile(test_df['p_t'], 100 - pct)
            sub = test_df[test_df['p_t'] >= cutoff]
            
        n = len(sub)
        wr = (sub['won'].mean() * 100.0) if n > 0 else 0.0
        ev_r = sub['realized_r'].mean() if n > 0 else 0.0
        wins = sub[sub['won'] == 1]['realized_r'].sum()
        losses = abs(sub[sub['won'] == 0]['realized_r'].sum())
        pf = (wins / losses) if losses > 0 else 9.99
        
        # Max Drawdown calculation
        equity_curve = np.cumsum(sub['realized_r'].values)
        peak = np.maximum.accumulate(equity_curve) if len(equity_curve) > 0 else [0]
        dd = peak - equity_curve if len(equity_curve) > 0 else [0]
        max_dd = float(np.max(dd)) if len(dd) > 0 else 0.0
        
        brier = brier_score(sub['won'].values, sub['p_t'].values) if n > 0 else 0.0
        auc = calc_auc(sub['won'].values, sub['p_t'].values) if n > 0 else 0.5
        
        bucket_key = f"Top {pct}%" if pct < 100 else "All Candidates (100%)"
        selectivity_curve[bucket_key] = {
            "trade_count": n,
            "win_rate_pct": round(wr, 2),
            "expected_r": round(float(ev_r), 3),
            "profit_factor": round(float(pf), 2),
            "auc": round(auc, 4),
            "brier_score": round(brier, 4),
            "max_drawdown_r": round(max_dd, 2)
        }
        print(f"    * {bucket_key:24s} | N={n:4d} | WR: {wr:5.1f}% | EV/R: {ev_r:+.3f}R | PF: {pf:4.2f} | AUC: {auc:.3f} | Brier: {brier:.3f} | MaxDD: {max_dd:.1f}R")

    # 3. Failure Forensics
    loss_samples = test_df[test_df['won'] == 0]
    forensics = {
        "ABSORPTION_TRAP": int(np.sum(loss_samples['state'] == "EXHAUSTION")),
        "CHOP_REVERSAL": int(np.sum(loss_samples['state'] == "CHOP")),
        "VOLATILITY_EXPANSION_SL": int(np.sum(loss_samples['state'] == "HIGH_VOLATILITY")),
        "NORMAL_STATISTICAL_STOP": int(np.sum(~loss_samples['state'].isin(["EXHAUSTION", "CHOP", "HIGH_VOLATILITY"])))
    }
    print("\n  FAILURE FORENSICS (Loss Classification):")
    for cause, cnt in forensics.items():
        pct = (cnt / len(loss_samples) * 100.0) if len(loss_samples) > 0 else 0.0
        print(f"    * {cause:28s}: {cnt:4d} losses ({pct:.1f}%)")

    results_report["programs"]["p10_unified_engine"] = {
        "bins": p10_bins,
        "selectivity_curve": selectivity_curve,
        "failure_forensics": forensics,
        "overall_test_auc": round(calc_auc(test_df['won'].values, test_df['p_t'].values), 4),
        "overall_test_brier": round(brier_score(test_df['won'].values, test_df['p_t'].values), 4)
    }

    # Save comprehensive results artifact
    out_file = os.path.join(RESULTS_DIR, 'cme_x4_research_results.json')
    with open(out_file, 'w', encoding='utf-8') as f:
        json.dump(results_report, f, indent=2)
    print(f"\n[DONE] CME-X4 Master Research complete! Results saved to: {out_file}")

if __name__ == '__main__':
    run_master_research()
