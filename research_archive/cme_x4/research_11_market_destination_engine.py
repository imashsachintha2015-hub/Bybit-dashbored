#!/usr/bin/env python3
"""
CME-X4 RESEARCH PROGRAM 11: HIGHER-TIMEFRAME DIRECTION & DESTINATION ENGINE (HTF-DDE)
Version: 2026-09-25

Research-Only Module (Completely Isolated from Live Production & V4 Shadow Engine)
Investigates:
1. Probabilistic Market Mapping (1D Macro -> 4H Path -> 1H Destination)
2. Directional Strength Score H_t Calibration
3. Barrier Destination Model: P(U before D | X_t) at +/-1.0σ, +/-1.5σ, +/-2.0σ
4. Path Decomposition: Direct Expansion vs. Pullback-then-Continuation vs. Trend Failure
5. Expected Excursions: E[MFE_h | X_t] and E[MAE_h | X_t]
6. Cross-Sectional Pooling across 25+ liquid crypto assets
7. Hierarchical Bayesian Prior integration with 15m/5m value-zone entry
"""

import os
import sys
import json
import time
import math
import urllib.request
import urllib.error
from datetime import datetime, timezone

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT_DIR)

RESULTS_DIR = os.path.join(ROOT_DIR, "research", "cme_x4", "results")
CACHE_DIR = os.path.join(ROOT_DIR, "research", "cme_x4", "data", "htf_cache")
os.makedirs(RESULTS_DIR, exist_ok=True)
os.makedirs(CACHE_DIR, exist_ok=True)

SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT",
    "AVAXUSDT", "LINKUSDT", "SUIUSDT", "ADAUSDT", "NEARUSDT",
    "TIAUSDT", "INJUSDT", "OPUSDT", "ARBUSDT", "APTUSDT",
    "RENDERUSDT", "ICPUSDT", "LTCUSDT", "PEPEUSDT", "SHIBUSDT",
    "UNIUSDT", "FILUSDT", "ATOMUSDT", "XLMUSDT", "DOTUSDT"
]

def log(msg):
    print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] {msg}", flush=True)

def fetch_klines(symbol, interval, limit=300):
    cache_file = os.path.join(CACHE_DIR, f"{symbol}_{interval}_{limit}.json")
    if os.path.exists(cache_file):
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                if len(data) >= limit * 0.7:
                    return data
        except Exception:
            pass

    url = f"https://api.bybit.com/v5/market/kline?category=linear&symbol={symbol}&interval={interval}&limit={limit}"
    req = urllib.request.Request(url, headers={"User-Agent": "MASIS-HTF-Research/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            res = json.loads(r.read().decode("utf-8"))
            raw = res.get("result", {}).get("list", [])
            out = []
            for b in reversed(raw):
                out.append({
                    "start": int(b[0]),
                    "open": float(b[1]),
                    "high": float(b[2]),
                    "low": float(b[3]),
                    "close": float(b[4]),
                    "volume": float(b[5])
                })
            if out:
                with open(cache_file, "w", encoding="utf-8") as f:
                    json.dump(out, f)
            time.sleep(0.05)
            return out
    except Exception as e:
        log(f"Warning fetching {symbol} {interval}: {e}")
        return []

def calc_ema(arr, period):
    if len(arr) < period:
        return arr[-1] if arr else 0.0
    k = 2.0 / (period + 1)
    ema = sum(arr[:period]) / period
    for val in arr[period:]:
        ema = val * k + ema * (1.0 - k)
    return float(ema)

def calc_me(closes, window=14):
    if len(closes) < window + 1:
        return 0.5
    sub = [float(x) for x in closes[-(window + 1):]]
    net_disp = abs(sub[-1] - sub[0])
    gross_path = sum(abs(sub[i] - sub[i-1]) for i in range(1, len(sub)))
    return float(net_disp / gross_path) if gross_path > 0 else 0.0

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

def run_research():
    log("=" * 70)
    log("  CME-X4 PROGRAM 11: HIGHER-TIMEFRAME DIRECTION & DESTINATION RESEARCH")
    log("=" * 70)

    dataset = []  # All pooled evaluation instances across all assets
    per_coin_stats = {}

    log(f"Fetching multi-timeframe historical klines across {len(SYMBOLS)} crypto assets...")

    for sym in SYMBOLS:
        k1d = fetch_klines(sym, "D", limit=180)     # ~6 months of 1D
        k4h = fetch_klines(sym, "240", limit=300)   # ~50 days of 4H
        k1h = fetch_klines(sym, "60", limit=500)    # ~21 days of 1H

        if len(k1d) < 30 or len(k4h) < 50 or len(k1h) < 100:
            log(f"Skipping {sym}: insufficient kline history")
            continue

        c1d = [b["close"] for b in k1d]
        t1d = [b["start"] for b in k1d]

        coin_instances = 0

        # We step through 4H bars, leaving at least 12 forward bars (48 hours) for outcome evaluation
        for idx in range(35, len(k4h) - 12):
            cur_4h = k4h[idx]
            eval_time = cur_4h["start"]
            cur_p = cur_4h["close"]

            # 1. Align 1D: get 1D bars strictly prior to this 4H bar
            past_1d_idx = [i for i, t in enumerate(t1d) if t <= eval_time]
            if len(past_1d_idx) < 25:
                continue
            last_1d_i = past_1d_idx[-1]
            sub_1d_c = c1d[:last_1d_i + 1]

            # 1D Structure Features
            ema20_1d = calc_ema(sub_1d_c, 20)
            ema50_1d = calc_ema(sub_1d_c, 50)
            trend_1d = 1.0 if ema20_1d > ema50_1d else -1.0
            me_1d = calc_me(sub_1d_c, 14)
            s_1d = trend_1d * (0.6 + 0.4 * min(1.0, me_1d))

            # 2. 4H Structure & Momentum Features
            sub_4h = k4h[:idx + 1]
            sub_4h_c = [b["close"] for b in sub_4h]
            sub_4h_v = [b["volume"] for b in sub_4h]
            
            ema9_4h = calc_ema(sub_4h_c, 9)
            ema21_4h = calc_ema(sub_4h_c, 21)
            trend_4h = 1.0 if ema9_4h > ema21_4h else -1.0
            rsi_4h = calc_rsi(sub_4h_c, 14)
            mom_4h = (rsi_4h - 50.0) / 50.0  # [-1.0, +1.0]

            # Swing displacement
            recent_highs = [b["high"] for b in sub_4h[-10:]]
            recent_lows = [b["low"] for b in sub_4h[-10:]]
            swing_rng = max(1e-6, max(recent_highs) - min(recent_lows))
            pos_in_swing = (cur_p - min(recent_lows)) / swing_rng  # [0, 1]
            s_4h = trend_4h * 0.7 + (pos_in_swing - 0.5) * 0.6

            # Volatility & Volume
            atr_4h = calc_atr(sub_4h, 14)
            sigma_4h = atr_4h / cur_p if cur_p > 0 else 0.02
            avg_v4h = sum(sub_4h_v[-20:]) / 20.0 if len(sub_4h_v) >= 20 else sub_4h_v[-1]
            vol_ratio = cur_4h["volume"] / avg_v4h if avg_v4h > 0 else 1.0
            v_t = min(1.0, (vol_ratio - 1.0) / 2.0)

            # Chop Risk (C_t)
            me_4h = calc_me(sub_4h_c, 14)
            c_t = max(0.0, 1.0 - (me_4h / 0.35)) if me_4h < 0.35 else 0.0

            # 3. 1H Range Position
            past_1h = [b for b in k1h if b["start"] <= eval_time]
            if len(past_1h) >= 20:
                c1h = [b["close"] for b in past_1h[-20:]]
                h1h = max(b["high"] for b in past_1h[-20:])
                l1h = min(b["low"] for b in past_1h[-20:])
                rng1h = max(1e-6, h1h - l1h)
                s_1h = ((cur_p - l1h) / rng1h - 0.5) * 2.0  # [-1, +1]
            else:
                s_1h = 0.0

            # 4. Composite Directional Strength Score H_t
            # H_t = w1*S_1D + w2*S_4H + w3*S_1H + w4*M_t + w5*V_t - w6*C_t
            h_t = (0.28 * s_1d) + (0.32 * s_4h) + (0.15 * s_1h) + (0.15 * mom_4h) + (0.10 * v_t) - (0.20 * c_t)
            h_t = max(-1.0, min(1.0, h_t))

            # 5. Forward Outcome Tracking over next 12h (3 bars), 24h (6 bars), 48h (12 bars)
            forward_bars = k4h[idx + 1: idx + 13]
            if len(forward_bars) < 12:
                continue

            # Compute Max Favorable and Adverse Excursions in multiples of sigma
            # Long view
            max_p = max(b["high"] for b in forward_bars)
            min_p = min(b["low"] for b in forward_bars)
            end_p_12h = forward_bars[2]["close"]
            end_p_24h = forward_bars[5]["close"]
            end_p_48h = forward_bars[11]["close"]

            ret_12h = (end_p_12h - cur_p) / cur_p
            ret_24h = (end_p_24h - cur_p) / cur_p
            ret_48h = (end_p_48h - cur_p) / cur_p

            long_mfe_pct = (max_p - cur_p) / cur_p
            long_mae_pct = (cur_p - min_p) / cur_p
            long_mfe_sig = long_mfe_pct / sigma_4h if sigma_4h > 0 else 0.0
            long_mae_sig = long_mae_pct / sigma_4h if sigma_4h > 0 else 0.0

            # Barriers: +/-1.0σ, +/-1.5σ, +/-2.0σ
            # First-touch check: who got touched first?
            barriers = [1.0, 1.5, 2.0]
            first_touches = {}
            for k_barr in barriers:
                target_u = cur_p * (1.0 + k_barr * sigma_4h)
                target_d = cur_p * (1.0 - k_barr * sigma_4h)
                
                u_hit_bar = None
                d_hit_bar = None
                for b_i, fb in enumerate(forward_bars):
                    if u_hit_bar is None and fb["high"] >= target_u:
                        u_hit_bar = b_i
                    if d_hit_bar is None and fb["low"] <= target_d:
                        d_hit_bar = b_i

                if u_hit_bar is not None and (d_hit_bar is None or u_hit_bar < d_hit_bar):
                    first_touches[k_barr] = "UPPER_FIRST"
                elif d_hit_bar is not None and (u_hit_bar is None or d_hit_bar < u_hit_bar):
                    first_touches[k_barr] = "LOWER_FIRST"
                else:
                    first_touches[k_barr] = "NEITHER"

            # Path Classification for Bullish setups (where H_t > 0):
            # Path A (Direct Expansion): MFE >= 1.0σ with MAE < 0.3σ
            # Path B (Pullback then Continuation): MAE in [0.35σ, 0.8σ] then MFE >= 1.2σ
            # Path C (Thesis Failure): MAE >= 1.0σ before MFE reaches 0.4σ
            if long_mfe_sig >= 1.0 and long_mae_sig < 0.30:
                path_type = "DIRECT_EXPANSION"
            elif long_mae_sig >= 0.35 and long_mfe_sig >= 1.2:
                path_type = "PULLBACK_THEN_EXPANSION"
            elif long_mae_sig >= 1.0 and long_mfe_sig < 0.40:
                path_type = "FAILURE_COLLAPSE"
            else:
                path_type = "CHOP_RANGE"

            inst = {
                "symbol": sym,
                "timestamp": eval_time,
                "cur_price": cur_p,
                "s_1d": s_1d,
                "s_4h": s_4h,
                "s_1h": s_1h,
                "mom_4h": mom_4h,
                "chop_risk": c_t,
                "h_t": h_t,
                "sigma": sigma_4h,
                "ret_12h": ret_12h,
                "ret_24h": ret_24h,
                "ret_48h": ret_48h,
                "mfe_sig": long_mfe_sig,
                "mae_sig": long_mae_sig,
                "first_touches": first_touches,
                "path_type": path_type
            }
            dataset.append(inst)
            coin_instances += 1

        per_coin_stats[sym] = coin_instances

    log(f"Extracted {len(dataset)} multi-timeframe instances across {len(per_coin_stats)} coins.")

    # ── HYPOTHESIS TESTING & STATISTICAL ANALYSIS ─────────────────────────────
    # Divide into Directional Quintiles based on H_t:
    # Q1: Strong Short (H_t < -0.30)
    # Q2: Moderate Short (-0.30 <= H_t < -0.10)
    # Q3: Neutral / Chop (-0.10 <= H_t <= 0.10)
    # Q4: Moderate Long (0.10 < H_t <= 0.30)
    # Q5: Strong Long (H_t > 0.30)

    quintiles = {"STRONG_SHORT": [], "MOD_SHORT": [], "NEUTRAL": [], "MOD_LONG": [], "STRONG_LONG": []}
    for d in dataset:
        h = d["h_t"]
        if h < -0.30:
            quintiles["STRONG_SHORT"].append(d)
        elif h < -0.10:
            quintiles["MOD_SHORT"].append(d)
        elif h <= 0.10:
            quintiles["NEUTRAL"].append(d)
        elif h <= 0.30:
            quintiles["MOD_LONG"].append(d)
        else:
            quintiles["STRONG_LONG"].append(d)

    quintile_results = {}
    for q_name, items in quintiles.items():
        n = len(items)
        if n == 0:
            continue
        
        # 1. 24h Directional Win Rate P(Ret > 0)
        up_wins_24h = sum(1 for x in items if x["ret_24h"] > 0)
        down_wins_24h = sum(1 for x in items if x["ret_24h"] < 0)
        p_up_24h = round((up_wins_24h / n) * 100.0, 1)

        # 2. First-Barrier Hits (+/-1.5σ)
        upper_first = sum(1 for x in items if x["first_touches"].get(1.5) == "UPPER_FIRST")
        lower_first = sum(1 for x in items if x["first_touches"].get(1.5) == "LOWER_FIRST")
        p_upper_first = round((upper_first / n) * 100.0, 1)
        p_lower_first = round((lower_first / n) * 100.0, 1)

        # 3. Excursions
        avg_mfe = round(sum(x["mfe_sig"] for x in items) / n, 2)
        avg_mae = round(sum(x["mae_sig"] for x in items) / n, 2)
        mfe_mae_ratio = round(avg_mfe / avg_mae, 2) if avg_mae > 0 else 0.0

        # 4. Path Distributions
        direct_exp = sum(1 for x in items if x["path_type"] == "DIRECT_EXPANSION")
        pullback_cont = sum(1 for x in items if x["path_type"] == "PULLBACK_THEN_EXPANSION")
        failure = sum(1 for x in items if x["path_type"] == "FAILURE_COLLAPSE")
        chop = sum(1 for x in items if x["path_type"] == "CHOP_RANGE")

        quintile_results[q_name] = {
            "n": n,
            "pct_of_market": round((n / len(dataset)) * 100.0, 1),
            "p_up_24h": p_up_24h,
            "p_upper_barrier_1_5sig_first": p_upper_first,
            "p_lower_barrier_1_5sig_first": p_lower_first,
            "barrier_edge_ratio": round(p_upper_first / max(0.1, p_lower_first), 2),
            "avg_mfe_sig": avg_mfe,
            "avg_mae_sig": avg_mae,
            "mfe_mae_ratio": mfe_mae_ratio,
            "path_distribution": {
                "direct_expansion_pct": round((direct_exp / n) * 100.0, 1),
                "pullback_then_continuation_pct": round((pullback_cont / n) * 100.0, 1),
                "failure_collapse_pct": round((failure / n) * 100.0, 1),
                "chop_range_pct": round((chop / n) * 100.0, 1)
            }
        }

    # Cross-sectional breakdown: Majors vs. Rest of Universe
    majors_syms = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "LINKUSDT"]
    majors_items = [d for d in dataset if d["symbol"] in majors_syms]
    alts_items = [d for d in dataset if d["symbol"] not in majors_syms]

    def eval_group(items, name):
        strong_l = [x for x in items if x["h_t"] > 0.30]
        n_l = len(strong_l)
        if n_l == 0:
            return {}
        u_first = sum(1 for x in strong_l if x["first_touches"].get(1.5) == "UPPER_FIRST")
        l_first = sum(1 for x in strong_l if x["first_touches"].get(1.5) == "LOWER_FIRST")
        p_up = sum(1 for x in strong_l if x["ret_24h"] > 0) / n_l * 100.0
        return {
            "name": name,
            "total_instances": len(items),
            "strong_bullish_n": n_l,
            "p_up_24h": round(p_up, 1),
            "p_upper_barrier_1_5sig_first": round((u_first / n_l) * 100.0, 1),
            "p_lower_barrier_1_5sig_first": round((l_first / n_l) * 100.0, 1),
            "edge_ratio": round(u_first / max(1, l_first), 2)
        }

    cross_sectional = {
        "majors": eval_group(majors_items, "Top 5 Majors (BTC, ETH, SOL, XRP, LINK)"),
        "alts": eval_group(alts_items, "20 Cross-Sectional Altcoins")
    }

    # ── BAYESIAN PRIOR SIMULATION ─────────────────────────────────────────────
    # Compare raw 15m pullback setups vs. 15m pullback setups aligned with HTF Prior (H_t > +0.20)
    # If 15m entry occurs when H_t is strong, what is the resulting EV and Win Rate?
    strong_htf_instances = [d for d in dataset if d["h_t"] > 0.20]
    neutral_htf_instances = [d for d in dataset if abs(d["h_t"]) <= 0.20]
    opposing_htf_instances = [d for d in dataset if d["h_t"] < -0.20]

    def sim_harvest_strategy(items):
        if not items:
            return {}
        # Strategy: Buy, target +1.5σ (partial at +0.8σ, stop at -0.6σ)
        wins = 0
        total_r = 0.0
        for x in items:
            mfe = x["mfe_sig"]
            mae = x["mae_sig"]
            # Did it hit +0.8σ partial harvest before -0.6σ stop?
            if mfe >= 0.8 and mae < 0.6:
                # Protected stop locks +0.4σ, full target +1.5σ
                realized = 1.0 if mfe >= 1.5 else 0.5
                wins += 1
                total_r += realized
            elif mae >= 0.6:
                # Stopped out at -0.6σ (-1R)
                total_r -= 1.0
            else:
                total_r += (x["ret_24h"] / x["sigma"]) * 0.5
        n = len(items)
        return {
            "n": n,
            "win_rate": round((wins / n) * 100.0, 1),
            "ev_per_trade_r": round(total_r / n, 3),
            "total_r": round(total_r, 1)
        }

    bayesian_sim = {
        "aligned_with_htf_prior": sim_harvest_strategy(strong_htf_instances),
        "neutral_htf": sim_harvest_strategy(neutral_htf_instances),
        "opposing_htf": sim_harvest_strategy(opposing_htf_instances)
    }

    final_report_data = {
        "program": "CME-X4 Research Program 11: Higher-Timeframe Direction & Destination Engine",
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "total_instances_evaluated": len(dataset),
        "symbols_evaluated": len(per_coin_stats),
        "quintile_analysis": quintile_results,
        "cross_sectional": cross_sectional,
        "bayesian_prior_simulation": bayesian_sim
    }

    output_path = os.path.join(RESULTS_DIR, "research_11_destination_results.json")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(final_report_data, f, indent=2)

    log(f"Research Program 11 completed successfully! Results saved to {output_path}")
    return final_report_data

if __name__ == "__main__":
    run_research()
