"""
CHAMPIONSHIP DUAL-REGIME SYSTEM ENGINE
======================================
Unified production trading engine combining:
  1. BULLISH CHAMPIONSHIP v2.0 (BTC > 200 EMA):
     - Setup A: Bull Springboard Pullback
     - Veto 1: Blacklist ADA, APT, TAO, TIA, WLD
     - Veto 2: Lower Wick >= 38%
     - Veto 3: Dist to 200 EMA <= 10.0%, RSI <= 63.0
     - Veto 4: TP1 at 1.20R (50%) + BE lock, TP2 at 2.00R runner (50%)
  
  2. BEARISH CHAMPIONSHIP v4.1 (BTC < 200 EMA):
     - Archetype 1: 200 EMA + POC/PIC Exhaustion
     - Archetype 2: 50 EMA Volume Climax Exhaustion
     - Archetype 3: Bearish FVG 50% CE Rejection
     - Archetype 4: Structural SFP Liquidity Sweep
     - Veto 1: Blacklist ONDO, TIA, WLD
     - Veto 2: Veto if BTC is making 4-bar Higher Highs
     - Anti-Sweep Buffer: max(High * 1.0055, High + 0.60 ATR)
     - Two-Stage TP: TP1 at 1.00R (50%) + BE lock, TP2 at 2.00R runner (50%)
"""

import os
import sys
import json
import math
import time
from datetime import datetime, timezone

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BULL_BLACKLIST = {"ADAUSDT", "APTUSDT", "TAOUSDT", "TIAUSDT", "WLDUSDT"}
BEAR_BLACKLIST = {"ONDOUSDT", "TIAUSDT", "WLDUSDT"}
TOTAL_FRICTION_PCT = 0.0014 # 14 bps

def calc_ema(values, period):
    if len(values) < period:
        return [values[-1]] * len(values) if values else []
    multiplier = 2.0 / (period + 1.0)
    ema = [sum(values[:period]) / period]
    for v in values[period:]:
        ema.append((v - ema[-1]) * multiplier + ema[-1])
    return [ema[0]] * (period - 1) + ema

def calc_rsi(closes, period=14):
    n = len(closes)
    if n < period + 1:
        return [50.0] * n
    rsi = [50.0] * period
    gains = [max(0.0, closes[i] - closes[i - 1]) for i in range(1, period + 1)]
    losses = [max(0.0, closes[i - 1] - closes[i]) for i in range(1, period + 1)]
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period

    if avg_loss == 0:
        rsi.append(100.0)
    else:
        rs = avg_gain / avg_loss
        rsi.append(100.0 - (100.0 / (1.0 + rs)))

    for i in range(period + 1, n):
        chg = closes[i] - closes[i - 1]
        g = max(0.0, chg)
        l = max(0.0, -chg)
        avg_gain = (avg_gain * (period - 1) + g) / period
        avg_loss = (avg_loss * (period - 1) + l) / period
        if avg_loss == 0:
            rsi.append(100.0)
        else:
            rs = avg_gain / avg_loss
            rsi.append(100.0 - (100.0 / (1.0 + rs)))
    return rsi

def calc_atr(bars, period=14):
    trs = []
    for i in range(len(bars)):
        h = bars[i]["high"]
        l = bars[i]["low"]
        if i == 0:
            trs.append(h - l)
        else:
            prev_c = bars[i - 1]["close"]
            trs.append(max(h - l, abs(h - prev_c), abs(l - prev_c)))
    return calc_ema(trs, period)

def compute_vp(bars, num_bins=30):
    if not bars: return None
    min_p = min(b["low"] for b in bars)
    max_p = max(b["high"] for b in bars)
    if max_p <= min_p: return None
    bin_size = (max_p - min_p) / num_bins
    bins = [0.0] * num_bins
    for b in bars:
        mid = (b["high"] + b["low"]) / 2.0
        bin_idx = min(num_bins - 1, max(0, int((mid - min_p) / bin_size)))
        bins[bin_idx] += b["volume"]
    max_vol = max(bins)
    poc_idx = bins.index(max_vol)
    poc_price = min_p + (poc_idx + 0.5) * bin_size
    return {"poc": poc_price, "min": min_p, "max": max_p}

def find_active_bearish_fvgs(bars, cur_idx, lookback=24):
    fvgs = []
    start = max(2, cur_idx - lookback)
    for k in range(start, cur_idx):
        b0 = bars[k - 2]
        b2 = bars[k]
        if b2["high"] < b0["low"]:
            fvg_top = b0["low"]
            fvg_bot = b2["high"]
            mitigated = False
            for fwd in range(k + 1, cur_idx + 1):
                if bars[fwd]["high"] >= fvg_top:
                    mitigated = True
                    break
            if not mitigated:
                fvgs.append({
                    "top": fvg_top,
                    "bot": fvg_bot,
                    "ce": (fvg_top + fvg_bot) / 2.0,
                    "bar_idx": k - 1
                })
    return fvgs

class ChampionshipDualRegimeEngine:
    def __init__(self):
        self.bull_blacklist = BULL_BLACKLIST
        self.bear_blacklist = BEAR_BLACKLIST

    def evaluate_regime(self, btc_bars):
        """Returns 'BULL', 'BEAR', or 'NEUTRAL' based on BTC 15m relative to 200 EMA"""
        if len(btc_bars) < 200:
            return "NEUTRAL", 0.0, 0.0
        closes = [b["close"] for b in btc_bars]
        ema200 = calc_ema(closes, 200)
        cur_c = closes[-1]
        cur_200 = ema200[-1]
        dist_pct = (cur_c - cur_200) / cur_200 * 100.0
        regime = "BULL" if cur_c > cur_200 else "BEAR"
        return regime, cur_c, cur_200

    def scan_candidate(self, symbol, bars, btc_bars):
        """Scans a single symbol's 15m bars against Championship Dual-Regime criteria"""
        if len(bars) < 200 or len(btc_bars) < 200:
            return None

        regime, btc_c, btc_200 = self.evaluate_regime(btc_bars)
        closes = [b["close"] for b in bars]
        ema21 = calc_ema(closes, 21)
        ema50 = calc_ema(closes, 50)
        ema200 = calc_ema(closes, 200)
        rsi = calc_rsi(closes, 14)
        atr = calc_atr(bars, 14)

        i = len(bars) - 1
        b = bars[i]
        c, l, h, o = b["close"], b["low"], b["high"], b["open"]
        rng = max(1e-8, h - l)

        avg_v = sum(x["volume"] for x in bars[-16:-1]) / 15.0 if len(bars) >= 16 else 1.0
        rvol = b["volume"] / avg_v if avg_v > 0 else 1.0

        # ─────────────────────────────────────────────────────────────────────
        # 1. BULLISH CHAMPIONSHIP (BTC > 200 EMA)
        # ─────────────────────────────────────────────────────────────────────
        if regime == "BULL":
            if symbol in self.bull_blacklist:
                return None
            if not (c > ema200[i] and ema21[i] > ema50[i]):
                return None

            dist_200 = (c - ema200[i]) / ema200[i] * 100.0
            if not (1.50 <= dist_200 <= 10.0):
                return None
            if rsi[i] > 63.0:
                return None

            # Lower wick rejection >= 38%
            lower_wick = min(o, c) - l
            if (c - l) / rng < 0.55 or (lower_wick / rng) < 0.38:
                return None
            if b["volume"] < 1.25 * avg_v:
                return None
            if not (l <= ema21[i] * 1.002 and c > ema21[i]):
                return None

            stop = min(l * 0.9970, l - (0.45 * atr[i]))
            risk = c - stop
            if not (0.0035 <= risk / c <= 0.025):
                return None

            tp1 = c + (1.20 * risk)
            tp2 = c + (2.00 * risk)

            return {
                "symbol": symbol,
                "mode": "CHAMPIONSHIP_DUAL_REGIME",
                "regime": "BULL",
                "direction": "LONG",
                "archetype": "BULL: 21 EMA Springboard + 38% Wick",
                "entry_price": round(c, 6),
                "stop_loss": round(stop, 6),
                "tp1": round(tp1, 6),
                "tp2": round(tp2, 6),
                "tp1_r": 1.20,
                "tp2_r": 2.00,
                "risk_pct": round((risk / c) * 100, 2),
                "rsi": round(rsi[i], 1),
                "rvol": round(rvol, 2),
                "vetos_passed": ["NO_TOXIC_5", "WICK_GE_38", "DIST200_LE_10", "RSI_LE_63"]
            }

        # ─────────────────────────────────────────────────────────────────────
        # 2. BEARISH CHAMPIONSHIP (BTC < 200 EMA)
        # ─────────────────────────────────────────────────────────────────────
        elif regime == "BEAR":
            if symbol in self.bear_blacklist:
                return None

            # BTC Higher-High Veto
            btc_highs = [btc_bars[-k]["high"] for k in range(2, 6)]
            if btc_bars[-1]["high"] > max(btc_highs):
                return None

            # Altcoin Death Stack: Close < 200 and 21 < 50
            if not (c < ema200[i] and ema21[i] < ema50[i]):
                return None

            upper_wick = h - max(o, c)
            wick_ratio = upper_wick / rng

            # Archetype 1: 200 EMA + POC/PIC Exhaustion
            if (wick_ratio >= 0.35) and (rsi[i] >= 56.0) and (rvol >= 1.0) and (h >= ema200[i] * 0.996 and c < ema200[i]):
                vp = compute_vp(bars[-37:-1])
                poc = vp["poc"] if vp else None
                fvgs = find_active_bearish_fvgs(bars, i, lookback=24)
                has_fvg = any(f["bot"] <= ema200[i] <= f["top"] * 1.005 for f in fvgs)
                if (poc and h >= poc * 0.996 and c < poc) or has_fvg:
                    fvg_top = max([f["top"] for f in fvgs if f["bot"] <= ema200[i] <= f["top"] * 1.005], default=h)
                    stop = max(max(h, fvg_top) * 1.0060, h + 0.65 * atr[i])
                    risk = stop - c
                    if 0.0040 <= risk / c <= 0.035:
                        return self._format_bear_candidate(symbol, "BEAR: 200 EMA + POC/PIC Exhaustion", c, stop, risk, rsi[i], rvol)

            # Archetype 2: 50 EMA Volume Climax
            if (rvol >= 1.5) and (wick_ratio >= 0.36) and (rsi[i] >= 54.0) and (h >= ema50[i] * 0.998 and c < ema50[i]):
                vp = compute_vp(bars[-31:-1])
                poc = vp["poc"] if vp else None
                if (poc and c < poc) or (rsi[i] >= 58.0):
                    stop = max(h * 1.0060, h + 0.65 * atr[i])
                    risk = stop - c
                    if 0.0035 <= risk / c <= 0.030:
                        return self._format_bear_candidate(symbol, "BEAR: 50 EMA Volume Climax", c, stop, risk, rsi[i], rvol)

            # Archetype 3: Bearish FVG 50% CE Rejection
            fvgs = find_active_bearish_fvgs(bars, i, lookback=20)
            for fvg in fvgs:
                if fvg["bot"] <= c <= fvg["top"] and (h >= fvg["ce"]) and (wick_ratio >= 0.36) and (rsi[i] >= 53) and (rvol >= 1.0):
                    stop = max(fvg["top"] * 1.0055, h + 0.60 * atr[i])
                    risk = stop - c
                    if 0.0035 <= risk / c <= 0.030:
                        return self._format_bear_candidate(symbol, "BEAR: FVG 50% CE Rejection", c, stop, risk, rsi[i], rvol)

            # Archetype 4: Structural SFP Sweep
            local_high = max(bars[k]["high"] for k in range(max(0, i-32), i))
            if (h > local_high) and (c < local_high) and (wick_ratio >= 0.38) and (rsi[i] >= 54) and (rvol >= 1.05):
                stop = max(h * 1.0055, h + 0.60 * atr[i])
                risk = stop - c
                if 0.0035 <= risk / c <= 0.030:
                    return self._format_bear_candidate(symbol, "BEAR: Structural SFP Sweep", c, stop, risk, rsi[i], rvol)

        return None

    def _format_bear_candidate(self, symbol, archetype, c, stop, risk, rsi_val, rvol_val):
        tp1 = c - (1.00 * risk)
        tp2 = c - (2.00 * risk)
        return {
            "symbol": symbol,
            "mode": "CHAMPIONSHIP_DUAL_REGIME",
            "regime": "BEAR",
            "direction": "SHORT",
            "archetype": archetype,
            "entry_price": round(c, 6),
            "stop_loss": round(stop, 6),
            "tp1": round(tp1, 6),
            "tp2": round(tp2, 6),
            "tp1_r": 1.00,
            "tp2_r": 2.00,
            "risk_pct": round((risk / c) * 100, 2),
            "rsi": round(rsi_val, 1),
            "rvol": round(rvol_val, 2),
            "vetos_passed": ["NO_TOXIC_PUMPS", "BTC_HH_VETO", "DEATH_STACK", "ANTI_SWEEP_SL"]
        }
