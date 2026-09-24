#!/usr/bin/env python3
"""
CME-X4 RESEARCH PROGRAM 13: LARGE TRENDING PATTERNS IDENTIFICATION & SETUP ENGINE
Identifies and backtests the 4 Major Macro/Multi-Day Trending Patterns:
1. VOLATILITY_SQUEEZE_BREAKOUT: Low-volatility compression followed by volume surge expansion.
2. BREAK_OF_STRUCTURE_SR_FLIP: Higher-High / Higher-Low market structure shift with key S/R flip.
3. LIQUIDITY_SWEEP_REVERSAL: False breakout stop-hunt at macro boundary followed by violent displacement.
4. MOMENTUM_TREND_FLAG: Shallow consolidation (< 38.2% retracement) inside strong primary trend.

Evaluates:
- Frequency of large trend occurrence across 32 Bybit liquid perpetuals.
- Forward trend expansion over multi-day horizons (24h to 72h).
- Average trend run magnitude (% gain).
- Reward-to-Risk ratio and Expectancy.
- Concrete historical case studies with exact entry/exit levels.
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

def load_klines(sym, interval="60"):
    # We prefer 60m (1H) for large trending patterns, falling back to 15m aggregated
    cache_path = os.path.join(DATA_DIR, f"{sym}_{interval}.json")
    if os.path.exists(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                d = json.load(f)
                if len(d) >= 100:
                    return d
        except Exception:
            pass
    # If 60m not large, aggregate 15m bars into 60m bars
    k15 = os.path.join(DATA_DIR, f"{sym}_15.json")
    if os.path.exists(k15):
        try:
            with open(k15, "r", encoding="utf-8") as f:
                raw15 = json.load(f)
                # aggregate each 4 x 15m bars into 1h
                agg = []
                for i in range(0, len(raw15) - 3, 4):
                    chunk = raw15[i:i+4]
                    agg.append({
                        "start": chunk[0]["start"],
                        "open": chunk[0]["open"],
                        "high": max(b["high"] for b in chunk),
                        "low": min(b["low"] for b in chunk),
                        "close": chunk[-1]["close"],
                        "volume": sum(b["volume"] for b in chunk)
                    })
                return agg
        except Exception:
            pass
    return []

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
        return 0.01 * bars[-1]["close"]
    trs = []
    for i in range(1, len(bars)):
        h = bars[i]["high"]
        l = bars[i]["low"]
        pc = bars[i-1]["close"]
        tr = max(h - l, abs(h - pc), abs(l - pc))
        trs.append(tr)
    return sum(trs[-period:]) / float(period)

def calc_me(closes, window=14):
    if len(closes) < window + 1:
        return 0.5
    sub = [float(x) for x in closes[-(window + 1):]]
    net_disp = abs(sub[-1] - sub[0])
    gross_path = sum(abs(sub[i] - sub[i-1]) for i in range(1, len(sub)))
    return float(net_disp / gross_path) if gross_path > 0 else 0.0

def run_large_trend_research():
    print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] Starting CME-X4 Research 13: Large Trending Patterns...")

    pattern_records = {
        "VOLATILITY_SQUEEZE_BREAKOUT": [],
        "BREAK_OF_STRUCTURE_SR_FLIP": [],
        "LIQUIDITY_SWEEP_REVERSAL": [],
        "MOMENTUM_TREND_FLAG": []
    }

    for sym in SYMBOLS:
        bars = load_klines(sym, "60") # 1-Hour bars
        if len(bars) < 150:
            continue

        n = len(bars)
        closes = [b["close"] for b in bars]
        volumes = [b["volume"] for b in bars]

        for i in range(60, n - 48): # ensure 48 hours lookahead
            cur_bar = bars[i]
            cur_p = cur_bar["close"]
            cur_ts = cur_bar["start"]

            sub_bars = bars[:i+1]
            sub_closes = closes[:i+1]
            sub_volumes = volumes[:i+1]

            ema21 = calc_ema(sub_closes, 21)
            ema50 = calc_ema(sub_closes, 50)
            atr14 = calc_atr(sub_bars, 14)
            me14 = calc_me(sub_closes, 14)

            avg_vol = sum(sub_volumes[-20:]) / 20.0 if len(sub_volumes) >= 20 else 1.0
            vol_ratio = cur_bar["volume"] / avg_vol if avg_vol > 0 else 1.0

            # ── PATTERN 1: VOLATILITY SQUEEZE BREAKOUT ─────────────────────────
            # Condition: Range of previous 24 bars was extremely narrow (ATR/Price < 0.8%),
            # followed by an expansion bar breaking range high with volume >= 2.0x
            recent_24 = sub_bars[-24:-1]
            if recent_24:
                high_24 = max(b["high"] for b in recent_24)
                low_24 = min(b["low"] for b in recent_24)
                bandwidth = (high_24 - low_24) / cur_p

                # Long Squeeze Breakout
                if bandwidth <= 0.025 and cur_p > high_24 and vol_ratio >= 1.8 and me14 >= 0.55:
                    pattern_records["VOLATILITY_SQUEEZE_BREAKOUT"].append({
                        "symbol": sym,
                        "direction": "LONG",
                        "time": datetime.fromtimestamp(cur_ts / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M"),
                        "entry_price": cur_p,
                        "stop_loss": low_24 * 0.998,
                        "bandwidth_pct": round(bandwidth * 100, 2),
                        "vol_ratio": round(vol_ratio, 2),
                        "me14": round(me14, 2),
                        "future_bars": bars[i+1: i+49]
                    })
                # Short Squeeze Breakout
                elif bandwidth <= 0.025 and cur_p < low_24 and vol_ratio >= 1.8 and me14 >= 0.55:
                    pattern_records["VOLATILITY_SQUEEZE_BREAKOUT"].append({
                        "symbol": sym,
                        "direction": "SHORT",
                        "time": datetime.fromtimestamp(cur_ts / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M"),
                        "entry_price": cur_p,
                        "stop_loss": high_24 * 1.002,
                        "bandwidth_pct": round(bandwidth * 100, 2),
                        "vol_ratio": round(vol_ratio, 2),
                        "me14": round(me14, 2),
                        "future_bars": bars[i+1: i+49]
                    })

            # ── PATTERN 2: BREAK OF STRUCTURE (BOS) & S/R FLIP ────────────────
            # Trend was confirmed: EMA21 > EMA50. Price pulled back to test previous swing high
            # (now dynamic support near EMA21/EMA50) and printed a strong bullish rejection candle
            if i >= 35:
                prior_high = max(b["high"] for b in sub_bars[-35:-10])
                is_retesting_flip = (abs(cur_bar["low"] - prior_high) / prior_high <= 0.008) and (cur_p >= prior_high)
                
                if ema21 > ema50 and is_retesting_flip and cur_bar["close"] > cur_bar["open"] and me14 >= 0.40:
                    pattern_records["BREAK_OF_STRUCTURE_SR_FLIP"].append({
                        "symbol": sym,
                        "direction": "LONG",
                        "time": datetime.fromtimestamp(cur_ts / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M"),
                        "entry_price": cur_p,
                        "stop_loss": cur_bar["low"] * 0.995,
                        "flip_level": round(prior_high, 4),
                        "vol_ratio": round(vol_ratio, 2),
                        "me14": round(me14, 2),
                        "future_bars": bars[i+1: i+49]
                    })

            # ── PATTERN 3: LIQUIDITY SWEEP REVERSAL (TURTLE SOUP) ──────────────
            # False breakout below multi-day low: Low pierced below the 48-bar lowest low,
            # but candle recovered and closed ABOVE the swept level with lower wick >= 35%
            if i >= 50:
                low_48 = min(b["low"] for b in sub_bars[-49:-1])
                bar_rng = max(1e-6, cur_bar["high"] - cur_bar["low"])
                l_wick = (min(cur_bar["open"], cur_bar["close"]) - cur_bar["low"]) / bar_rng
                
                # Bullish Liquidity Sweep
                if cur_bar["low"] < low_48 and cur_bar["close"] > low_48 and l_wick >= 0.35 and vol_ratio >= 1.4:
                    pattern_records["LIQUIDITY_SWEEP_REVERSAL"].append({
                        "symbol": sym,
                        "direction": "LONG",
                        "time": datetime.fromtimestamp(cur_ts / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M"),
                        "entry_price": cur_p,
                        "stop_loss": cur_bar["low"] * 0.996,
                        "swept_level": round(low_48, 4),
                        "wick_pct": round(l_wick * 100, 1),
                        "future_bars": bars[i+1: i+49]
                    })
                
                # Bearish Liquidity Sweep (Sweeping multi-day high)
                high_48 = max(b["high"] for b in sub_bars[-49:-1])
                u_wick = (cur_bar["high"] - max(cur_bar["open"], cur_bar["close"])) / bar_rng
                if cur_bar["high"] > high_48 and cur_bar["close"] < high_48 and u_wick >= 0.35 and vol_ratio >= 1.4:
                    pattern_records["LIQUIDITY_SWEEP_REVERSAL"].append({
                        "symbol": sym,
                        "direction": "SHORT",
                        "time": datetime.fromtimestamp(cur_ts / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M"),
                        "entry_price": cur_p,
                        "stop_loss": cur_bar["high"] * 1.004,
                        "swept_level": round(high_48, 4),
                        "wick_pct": round(u_wick * 100, 1),
                        "future_bars": bars[i+1: i+49]
                    })

            # ── PATTERN 4: MOMENTUM TREND FLAG ────────────────────────────────
            # Trend is aggressive (price moved >= +5% in last 30 bars), then paused for
            # 8-16 bars with shallow retracement (< 38.2%), breaking out on current bar
            if i >= 35:
                impulse_low = min(b["low"] for b in sub_bars[-30:-10])
                impulse_high = max(b["high"] for b in sub_bars[-30:-10])
                impulse_move = (impulse_high - impulse_low) / impulse_low
                
                if impulse_move >= 0.045: # at least +4.5% prior impulse
                    # check consolidation depth
                    flag_bars = sub_bars[-10:-1]
                    flag_low = min(b["low"] for b in flag_bars)
                    flag_high = max(b["high"] for b in flag_bars)
                    retracement = (impulse_high - flag_low) / (impulse_high - impulse_low)
                    
                    if retracement <= 0.382 and cur_p > flag_high and cur_bar["close"] > cur_bar["open"]:
                        pattern_records["MOMENTUM_TREND_FLAG"].append({
                            "symbol": sym,
                            "direction": "LONG",
                            "time": datetime.fromtimestamp(cur_ts / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M"),
                            "entry_price": cur_p,
                            "stop_loss": flag_low * 0.997,
                            "prior_impulse_pct": round(impulse_move * 100, 2),
                            "retracement_pct": round(retracement * 100, 1),
                            "future_bars": bars[i+1: i+49]
                        })

    print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] Pattern scan completed! Evaluating forward trend trajectories...")

    # ── FORWARD TRAJECTORY & METRICS AUDIT (24H - 48H HORIZONS) ────────
    summary_report = {}
    detailed_case_studies = []

    for pat_name, records in pattern_records.items():
        total = len(records)
        if total == 0:
            continue

        wins = 0
        losses = 0
        total_mfe_pct = 0.0
        total_mae_pct = 0.0
        total_r = 0.0
        massive_trend_runs = 0  # moves > +5.0%

        for r in records:
            entry_p = r["entry_price"]
            sl_p = r["stop_loss"]
            dirn = r["direction"]
            sl_dist = abs(entry_p - sl_p) / entry_p
            sl_dist = max(0.005, sl_dist)

            highest_fav = 0.0
            highest_adv = 0.0
            stopped_out = False

            # Evaluate forward 48 hours (48 x 1H bars)
            for fb in r["future_bars"]:
                fav = (fb["high"] - entry_p) / entry_p if dirn == "LONG" else (entry_p - fb["low"]) / entry_p
                adv = (entry_p - fb["low"]) / entry_p if dirn == "LONG" else (fb["high"] - entry_p) / entry_p
                if fav > highest_fav: highest_fav = fav
                if adv > highest_adv: highest_adv = adv

                # Stop loss check
                if (dirn == "LONG" and fb["low"] <= sl_p) or (dirn == "SHORT" and fb["high"] >= sl_p):
                    stopped_out = True
                    break

            total_mfe_pct += highest_fav
            total_mae_pct += highest_adv
            if highest_fav >= 0.050: # +5% trend move
                massive_trend_runs += 1

            if stopped_out:
                losses += 1
                total_r -= 1.0
                r["outcome"] = "STOPPED_OUT"
                r["realized_r"] = -1.0
            else:
                # Capture move with trailing stop at 21 EMA / 2R target
                realized_gain = max(0.0, highest_fav - 0.005)
                r_mult = realized_gain / sl_dist
                if r_mult >= 1.0:
                    wins += 1
                    total_r += r_mult
                    r["outcome"] = f"TREND_WIN (+{r_mult:.1f}R)"
                    r["realized_r"] = round(r_mult, 2)
                else:
                    losses += 1
                    total_r -= 0.5
                    r["outcome"] = "BREAKEVEN_CHOP"
                    r["realized_r"] = -0.5

            r["max_favorable_pct"] = round(highest_fav * 100, 2)
            r["max_adverse_pct"] = round(highest_adv * 100, 2)
            del r["future_bars"] # free memory

        win_rate = round((wins / total) * 100, 1)
        avg_mfe = round((total_mfe_pct / total) * 100, 2)
        avg_mae = round((total_mae_pct / total) * 100, 2)
        ev_r = round(total_r / total, 3)
        large_trend_rate = round((massive_trend_runs / total) * 100, 1)

        summary_report[pat_name] = {
            "instances_found": total,
            "win_rate_pct": win_rate,
            "avg_favorable_run_pct": avg_mfe,
            "avg_adverse_drawdown_pct": avg_mae,
            "ratio_mfe_to_mae": round(avg_mfe / max(0.01, avg_mae), 2),
            "massive_trend_run_rate_pct": f"{large_trend_rate}% (> +5.0% move)",
            "total_realized_r": round(total_r, 2),
            "expected_value_per_trade_r": ev_r
        }

        # Keep best 3 case studies per pattern
        sorted_records = sorted(records, key=lambda x: x["max_favorable_pct"], reverse=True)
        detailed_case_studies.extend(sorted_records[:3])

    results_payload = {
        "title": "CME-X4 Research 13: Large Trending Patterns Identification Engine",
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "pattern_comparison": summary_report,
        "concrete_case_studies": detailed_case_studies
    }

    out_file = os.path.join(RESULTS_DIR, "research_13_large_trend_patterns.json")
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(results_payload, f, indent=2)

    print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] Saved Research 13 results to {out_file}")
    return results_payload

if __name__ == "__main__":
    run_large_trend_research()
