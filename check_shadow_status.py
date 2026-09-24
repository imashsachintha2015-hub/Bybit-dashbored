#!/usr/bin/env python3
"""
CME-X4 V4 SHADOW MODE GRADUATION AUDITOR
Checks if live shadow testing has fulfilled all 5 graduation criteria to exit Shadow Mode.
"""

import sys
import os
from datetime import datetime, timezone

# Ensure stdout handles UTF-8 on Windows
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT_DIR)

from backend_lib.cme_x4_shadow_db import shadow_db

def main():
    perf = shadow_db.get_shadow_performance()
    grad = perf.get("graduation", {})
    chk = grad.get("checklist", {})
    
    is_graduated = grad.get("is_graduated", False)
    progress = grad.get("overall_progress_pct", 0.0)
    met_count = grad.get("criteria_met_count", 0)
    
    print("\n" + "=" * 65)
    print("  CME-X4 FAILURE ENGINE V4 -- SHADOW MODE COMPLETION AUDIT")
    print("=" * 65)
    
    status_str = "COMPLETED (READY FOR PAPER TRADING)" if is_graduated else "COLLECTING LIVE SAMPLES"
    print(f"Overall Status   : {status_str}")
    print(f"Graduation Gates : {met_count} of 5 Gates Cleared")
    print(f"Sample Progress  : {progress:.1f}% Complete")
    
    # Progress bar with ASCII
    bar_width = 30
    filled = int(bar_width * (progress / 100.0))
    bar = "#" * filled + "-" * (bar_width - filled)
    print(f"Progress Bar     : [{bar}] {progress:.1f}%\n")
    
    print("-" * 65)
    print(f"{'CRITERIA / GATE':<30} | {'CURRENT':<12} | {'TARGET':<10} | {'STATUS'}")
    print("-" * 65)
    
    # Gate 1: Trades
    t_curr = chk.get('trades', {}).get('current', 0)
    t_tgt = chk.get('trades', {}).get('target', 100)
    t_pass = chk.get('trades', {}).get('passed', False)
    t_status = "[PASS]" if t_pass else f"PENDING ({t_curr}/{t_tgt})"
    print(f"{'1. Closed Simulated Trades':<30} | {t_curr:<12} | {t_tgt:<10} | {t_status}")
    
    # Gate 2: Candidates
    c_curr = chk.get('candidates', {}).get('current', 0)
    c_tgt = chk.get('candidates', {}).get('target', 500)
    c_pass = chk.get('candidates', {}).get('passed', False)
    c_status = "[PASS]" if c_pass else f"PENDING ({c_curr}/{c_tgt})"
    print(f"{'2. Screened Candidates':<30} | {c_curr:<12} | {c_tgt:<10} | {c_status}")
    
    # Gate 3: Days
    d_curr = chk.get('days', {}).get('current', 0.0)
    d_tgt = chk.get('days', {}).get('target', 5.0)
    d_pass = chk.get('days', {}).get('passed', False)
    d_status = "[PASS]" if d_pass else f"PENDING ({d_curr:.2f}d)"
    print(f"{'3. Time Elapsed (Days)':<30} | {f'{d_curr:.2f} days':<12} | {f'{d_tgt:.1f} days':<10} | {d_status}")
    
    # Gate 4: Expected Net R
    ev_curr = chk.get('ev', {}).get('current', 0.0)
    ev_tgt = chk.get('ev', {}).get('target', 0.08)
    ev_pass = chk.get('ev', {}).get('passed', False)
    ev_status = "[PASS]" if ev_pass else ("ACCUMULATING" if t_curr < 20 else "[FAIL]")
    print(f"{'4. Expected Net R (EV)':<30} | {f'{ev_curr:+.3f}R':<12} | {f'>= {ev_tgt:+.3f}R':<10} | {ev_status}")
    
    # Gate 5: Win Rate
    wr_curr = chk.get('win_rate', {}).get('current', 0.0)
    wr_tgt = chk.get('win_rate', {}).get('target', 60.0)
    wr_pass = chk.get('win_rate', {}).get('passed', False)
    wr_status = "[PASS]" if wr_pass else ("ACCUMULATING" if t_curr < 20 else "[FAIL]")
    print(f"{'5. Win Rate':<30} | {f'{wr_curr:.1f}%':<12} | {f'>= {wr_tgt:.1f}%':<10} | {wr_status}")
    
    print("-" * 65)
    
    # Additional Observability Metrics
    print(f"\nAdditional Observability Metrics:")
    print(f"  * Veto Refusal Rate : {perf.get('total_rejection_rate_pct', 0)}% (Target: 70-88%)")
    print(f"  * Realized Slippage : {perf.get('average_slippage_bps', 4.0)} bps (Target: <= 6.0 bps)")
    print(f"  * Staged Harvests   : {perf.get('harvest_triggers', 0)} triggers (+0.40% / +0.25%)")
    print(f"  * Reversals Faded   : {perf.get('reversal_executions', 0)} triggers")
    print(f"  * Circuit Breakers  : {'HEALTHY (No trips)' if perf.get('circuit_breakers_active') else 'TRIPPED'}")
    
    print("\n" + "=" * 65)
    if is_graduated:
        print(">>> VERDICT: SHADOW MODE COMPLETED! <<<")
        print("All 5 statistical and operational gates are satisfied.")
        print("Action: Advance to Paper Execution with Virtual Capital.")
    else:
        print(">>> VERDICT: SHADOW MODE IN PROGRESS <<<")
        print("Continue running until all 5 gates turn [PASS].")
    print("=" * 65 + "\n")

if __name__ == "__main__":
    main()
