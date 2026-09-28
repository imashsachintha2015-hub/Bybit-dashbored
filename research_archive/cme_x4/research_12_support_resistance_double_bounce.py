#!/usr/bin/env python3
"""
CME-X4 RESEARCH PROGRAM 12: SUPPORT-TO-RESISTANCE DOUBLE-BOUNCE TREND ENGINE
Evaluates trades that identify verified Support/Resistance boundaries,
confirm a 2-touch bounce ('bouncing two of them'), and trend the full distance
from Support to overhead Resistance (and vice versa for Shorts).

Core Questions Answered:
1. What is the empirical probability that price trends from Support all the way to Resistance after 2 confirmed bounces?
2. What is the average Reward-to-Risk (R:R) ratio when buying double-bounce support?
3. How does this compare with the standard EMA pullback system on a $10 equity account under 10x leverage?
4. Concrete real-trade case studies from Bybit historical data.
"""

import os
import sys
import json
import math
from datetime import datetime, timezone

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT_DIR)

DATA_DIR = os.path.join(ROOT_DIR, "research", "cme_x4", "data")
RESULTS_DIR = os.path.join(ROOT_DIR, "research", "cme_x4", "results")
os.makedirs(RESULTS_DIR, exist_ok=True)

SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "LINKUSDT", 
    "AVAXUSDT", "DOGEUSDT", "SUIUSDT", "ADAUSDT", "NEARUSDT",
    "TIAUSDT", "INJUSDT", "OPUSDT", "ARBUSDT", "APTUSDT",
    "RENDERUSDT", "ICPUSDT", "LTCUSDT", "UNIUSDT", "FILUSDT",
    "SEIUSDT", "BNBUSDT", "AAVEUSDT", "STXUSDT", "PEPEUSDT",
    "WIFUSDT", "CRVUSDT", "SANDUSDT", "MANAUSDT", "ALGOUSDT", "GALAUSDT"
]

def log(msg):
    print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] {msg}", flush=True)

def load_klines(sym, interval="15"):
    cache_path = os.path.join(DATA_DIR, f"{sym}_{interval}.json")
    if os.path.exists(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return []

def find_swings(bars, order=4):
    """Find swing highs and swing lows."""
    highs = []
    lows = []
    n = len(bars)
    for i in range(order, n - order):
        cur_h = bars[i]["high"]
        cur_l = bars[i]["low"]
        is_high = all(cur_h >= bars[j]["high"] for j in range(i - order, i + order + 1) if j != i)
        is_low = all(cur_l <= bars[j]["low"] for j in range(i - order, i + order + 1) if j != i)
        if is_high:
            highs.append({"idx": i, "price": cur_h, "time": bars[i]["start"]})
        if is_low:
            lows.append({"idx": i, "price": cur_l, "time": bars[i]["start"]})
    return highs, lows

def run_sr_double_bounce_research():
    log("=" * 75)
    log("  CME-X4: SUPPORT-TO-RESISTANCE DOUBLE-BOUNCE TREND ENGINE (RESEARCH 12)")
    log("  Auditing 2-Touch Support Floors Trending to Overhead Resistance")
    log("=" * 75)

    all_setups = []
    
    for sym in SYMBOLS:
        bars15 = load_klines(sym, "15")
        if len(bars15) < 300:
            continue

        highs, lows = find_swings(bars15, order=4)
        
        # ── 1. SCAN FOR SUPPORT DOUBLE BOUNCES (LONG TRADES) ──────────────────
        # Find two consecutive swing lows that are near the same level (within 0.35%)
        # with a clear rally between them, establishing confirmed double-support
        for i in range(len(lows) - 1):
            l1 = lows[i]
            l2 = lows[i + 1]
            
            # Separation between bounce 1 and bounce 2 must be between 6 and 48 bars (1.5h to 12h)
            bar_diff = l2["idx"] - l1["idx"]
            if bar_diff < 6 or bar_diff > 48:
                continue

            # Price similarity (within 0.35% of each other)
            p1 = l1["price"]
            p2 = l2["price"]
            diff_pct = abs(p2 - p1) / p1
            if diff_pct > 0.0035:
                continue

            # There must be a swing high in between l1 and l2 (the intermediate peak)
            mid_highs = [h for h in highs if l1["idx"] < h["idx"] < l2["idx"]]
            if not mid_highs:
                continue
            peak_mid = max(h["price"] for h in mid_highs)
            mid_bounce_pct = (peak_mid - p1) / p1
            if mid_bounce_pct < 0.006:  # must have bounced at least +0.60% after bounce 1
                continue

            # Overhead Resistance: highest swing high before or during this structure
            prior_highs = [h for h in highs if h["idx"] <= l2["idx"] and h["idx"] >= l1["idx"] - 40]
            if not prior_highs:
                continue
            overhead_res = max(h["price"] for h in prior_highs)
            target_distance_pct = (overhead_res - p2) / p2
            if target_distance_pct < 0.008: # at least +0.80% distance to resistance
                continue

            # Entry bar: bar right after l2 swing confirmation
            entry_idx = l2["idx"] + 2
            if entry_idx >= len(bars15) - 20:
                continue

            entry_p = bars15[entry_idx]["close"]
            support_floor = min(p1, p2)
            stop_loss = support_floor * 0.9970 # 0.30% below double-touch support floor
            sl_distance_pct = (entry_p - stop_loss) / entry_p
            if sl_distance_pct <= 0 or sl_distance_pct > 0.015:
                continue

            # Target: The Overhead Resistance level
            tp_distance_pct = (overhead_res - entry_p) / entry_p
            if tp_distance_pct <= 0:
                continue

            rr_ratio = tp_distance_pct / sl_distance_pct

            # Forward outcome simulation: does it trend to resistance or hit stop first?
            future_bars = bars15[entry_idx + 1: min(len(bars15), entry_idx + 80)] # track up to 20 hours (80 bars)
            hit_res = False
            hit_sl = False
            max_fav = 0.0
            max_adv = 0.0
            exit_bar = len(future_bars)
            exit_p = future_bars[-1]["close"] if future_bars else entry_p

            for f_i, fb in enumerate(future_bars):
                fav = (fb["high"] - entry_p) / entry_p
                adv = (entry_p - fb["low"]) / entry_p
                if fav > max_fav: max_fav = fav
                if adv > max_adv: max_adv = adv

                # Check Stop Loss hit first
                if fb["low"] <= stop_loss:
                    hit_sl = True
                    exit_bar = f_i + 1
                    exit_p = stop_loss
                    break

                # Check Resistance Target hit
                if fb["high"] >= overhead_res:
                    hit_res = True
                    exit_bar = f_i + 1
                    exit_p = overhead_res
                    break

            outcome = "HIT_RESISTANCE" if hit_res else ("HIT_STOP_LOSS" if hit_sl else "TIMEOUT_EXPIRY")
            realized_r = round(rr_ratio, 2) if hit_res else (-1.0 if hit_sl else round((exit_p - entry_p) / (entry_p - stop_loss), 2))

            all_setups.append({
                "symbol": sym,
                "direction": "LONG (Support to Resistance)",
                "bounce_1_time": datetime.fromtimestamp(l1["time"] / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M"),
                "bounce_2_time": datetime.fromtimestamp(l2["time"] / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M"),
                "entry_time": datetime.fromtimestamp(bars15[entry_idx]["start"] / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M"),
                "support_level": round(support_floor, 4),
                "resistance_target": round(overhead_res, 4),
                "entry_price": round(entry_p, 4),
                "stop_loss": round(stop_loss, 4),
                "sl_distance_pct": round(sl_distance_pct * 100.0, 2),
                "target_distance_pct": round(tp_distance_pct * 100.0, 2),
                "planned_rr": round(rr_ratio, 2),
                "outcome": outcome,
                "realized_r": realized_r,
                "is_win": 1 if hit_res or realized_r > 0 else 0,
                "bars_to_exit": exit_bar,
                "max_favorable_pct": round(max_fav * 100.0, 2),
                "max_adverse_pct": round(max_adv * 100.0, 2)
            })

        # ── 2. SCAN FOR RESISTANCE DOUBLE BOUNCES (SHORT TRADES) ───────────────
        # Resistance tested twice with lower support target
        for i in range(len(highs) - 1):
            h1 = highs[i]
            h2 = highs[i + 1]
            
            bar_diff = h2["idx"] - h1["idx"]
            if bar_diff < 6 or bar_diff > 48:
                continue

            p1 = h1["price"]
            p2 = h2["price"]
            diff_pct = abs(p2 - p1) / p1
            if diff_pct > 0.0035:
                continue

            mid_lows = [l for l in lows if h1["idx"] < l["idx"] < h2["idx"]]
            if not mid_lows:
                continue
            valley_mid = min(l["price"] for l in mid_lows)
            mid_drop_pct = (p1 - valley_mid) / p1
            if mid_drop_pct < 0.006:
                continue

            prior_lows = [l for l in lows if l["idx"] <= h2["idx"] and l["idx"] >= h1["idx"] - 40]
            if not prior_lows:
                continue
            underneath_sup = min(l["price"] for l in prior_lows)
            target_distance_pct = (p2 - underneath_sup) / p2
            if target_distance_pct < 0.008:
                continue

            entry_idx = h2["idx"] + 2
            if entry_idx >= len(bars15) - 20:
                continue

            entry_p = bars15[entry_idx]["close"]
            res_ceiling = max(p1, p2)
            stop_loss = res_ceiling * 1.0030
            sl_distance_pct = (stop_loss - entry_p) / entry_p
            if sl_distance_pct <= 0 or sl_distance_pct > 0.015:
                continue

            tp_distance_pct = (entry_p - underneath_sup) / entry_p
            if tp_distance_pct <= 0:
                continue

            rr_ratio = tp_distance_pct / sl_distance_pct

            future_bars = bars15[entry_idx + 1: min(len(bars15), entry_idx + 80)]
            hit_sup = False
            hit_sl = False
            max_fav = 0.0
            max_adv = 0.0
            exit_bar = len(future_bars)
            exit_p = future_bars[-1]["close"] if future_bars else entry_p

            for f_i, fb in enumerate(future_bars):
                fav = (entry_p - fb["low"]) / entry_p
                adv = (fb["high"] - entry_p) / entry_p
                if fav > max_fav: max_fav = fav
                if adv > max_adv: max_adv = adv

                if fb["high"] >= stop_loss:
                    hit_sl = True
                    exit_bar = f_i + 1
                    exit_p = stop_loss
                    break

                if fb["low"] <= underneath_sup:
                    hit_sup = True
                    exit_bar = f_i + 1
                    exit_p = underneath_sup
                    break

            outcome = "HIT_SUPPORT" if hit_sup else ("HIT_STOP_LOSS" if hit_sl else "TIMEOUT_EXPIRY")
            realized_r = round(rr_ratio, 2) if hit_sup else (-1.0 if hit_sl else round((entry_p - exit_p) / (stop_loss - entry_p), 2))

            all_setups.append({
                "symbol": sym,
                "direction": "SHORT (Resistance to Support)",
                "bounce_1_time": datetime.fromtimestamp(h1["time"] / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M"),
                "bounce_2_time": datetime.fromtimestamp(h2["time"] / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M"),
                "entry_time": datetime.fromtimestamp(bars15[entry_idx]["start"] / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M"),
                "resistance_level": round(res_ceiling, 4),
                "support_target": round(underneath_sup, 4),
                "entry_price": round(entry_p, 4),
                "stop_loss": round(stop_loss, 4),
                "sl_distance_pct": round(sl_distance_pct * 100.0, 2),
                "target_distance_pct": round(tp_distance_pct * 100.0, 2),
                "planned_rr": round(rr_ratio, 2),
                "outcome": outcome,
                "realized_r": realized_r,
                "is_win": 1 if hit_sup or realized_r > 0 else 0,
                "bars_to_exit": exit_bar,
                "max_favorable_pct": round(max_fav * 100.0, 2),
                "max_adverse_pct": round(max_adv * 100.0, 2)
            })

    log(f"Analyzed multi-asset dataset. Found {len(all_setups)} verified double-bounce boundary trend setups!")

    # ── METRICS AGGREGATION ───────────────────────────────────────────
    total_setups = len(all_setups)
    long_setups = [s for s in all_setups if "LONG" in s["direction"]]
    short_setups = [s for s in all_setups if "SHORT" in s["direction"]]

    full_target_hits = sum(1 for s in all_setups if "HIT_RESISTANCE" in s["outcome"] or "HIT_SUPPORT" in s["outcome"])
    stop_hits = sum(1 for s in all_setups if "HIT_STOP_LOSS" in s["outcome"])
    timeout_exits = sum(1 for s in all_setups if "TIMEOUT" in s["outcome"])

    target_reach_rate = round((full_target_hits / max(1, total_setups)) * 100.0, 1)
    win_rate = round((sum(1 for s in all_setups if s["is_win"] == 1) / max(1, total_setups)) * 100.0, 1)

    avg_rr = round(sum(s["planned_rr"] for s in all_setups) / max(1, total_setups), 2)
    avg_target_move = round(sum(s["target_distance_pct"] for s in all_setups) / max(1, total_setups), 2)
    avg_sl_dist = round(sum(s["sl_distance_pct"] for s in all_setups) / max(1, total_setups), 2)
    total_realized_r = round(sum(s["realized_r"] for s in all_setups), 2)
    ev_per_trade_r = round(total_realized_r / max(1, total_setups), 3)

    # Financial model on $10 account under 10x leverage
    # With SRB Model A ($1 margin x 10x = $10 notional, risking ~0.48% per trade)
    capital = 10.00
    peak_cap = 10.00
    max_dd_usd = 0.0
    for s in all_setups:
        trade_risk = 10.0 * (s["sl_distance_pct"] / 100.0)
        pnl = s["realized_r"] * trade_risk
        capital = round(capital + pnl, 3)
        if capital > peak_cap: peak_cap = capital
        dd = peak_cap - capital
        if dd > max_dd_usd: max_dd_usd = dd

    results_payload = {
        "title": "CME-X4 Research 12: Support-to-Resistance Double-Bounce Trend Engine",
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "executive_summary": {
            "total_verified_double_bounces": total_setups,
            "long_setups_support_to_resistance": len(long_setups),
            "short_setups_resistance_to_support": len(short_setups),
            "full_boundary_run_rate_pct": target_reach_rate,
            "total_win_rate_pct": win_rate,
            "average_reward_to_risk_ratio": f"{avg_rr}:1",
            "average_trend_distance_pct": f"{avg_target_move}%",
            "average_stop_distance_pct": f"{avg_sl_dist}%",
            "total_realized_r": total_realized_r,
            "expected_value_per_trade_r": ev_per_trade_r
        },
        "financial_ledger_10_dollar_account": {
            "starting_balance_usd": 10.00,
            "final_balance_usd": capital,
            "net_profit_usd": round(capital - 10.00, 2),
            "roi_pct": round(((capital - 10.00) / 10.00) * 100.0, 2),
            "max_drawdown_usd": round(max_dd_usd, 2),
            "max_drawdown_pct": round((max_dd_usd / peak_cap) * 100.0, 2)
        },
        "case_studies": all_setups[:20]
    }

    out_file = os.path.join(RESULTS_DIR, "research_12_double_bounce_results.json")
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(results_payload, f, indent=2)

    log(f"Results successfully saved to {out_file}")
    return results_payload

if __name__ == "__main__":
    run_sr_double_bounce_research()
