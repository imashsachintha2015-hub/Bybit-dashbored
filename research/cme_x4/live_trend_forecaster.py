#!/usr/bin/env python3
"""
CME-X4 REAL-TIME LIVE FORWARD TREND FORECASTER
Generates strictly forward-looking forecasts for live Bybit perpetual assets
WITHOUT ANY FUTURE DATA (uses only past and currently forming live candles).
"""

import urllib.request
import json
import time
from datetime import datetime, timezone

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "AVAXUSDT", "SUIUSDT"]

def fetch_klines(sym, interval="60", limit=60):
    url = f"https://api.bybit.com/v5/market/kline?category=linear&symbol={sym}&interval={interval}&limit={limit}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=6) as r:
        raw = json.loads(r.read().decode())["result"]["list"]
    bars = []
    for row in raw:
        bars.append({
            "start": int(row[0]),
            "open": float(row[1]),
            "high": float(row[2]),
            "low": float(row[3]),
            "close": float(row[4]),
            "volume": float(row[5])
        })
    bars.sort(key=lambda x: x["start"])
    return bars

def calc_ema(arr, period):
    if len(arr) < period:
        return arr[-1] if arr else 0.0
    k = 2.0 / (period + 1)
    ema = sum(arr[:period]) / period
    for val in arr[period:]:
        ema = val * k + ema * (1.0 - k)
    return float(ema)

def generate_live_forecasts():
    forecasts = []
    for sym in SYMBOLS:
        try:
            bars = fetch_klines(sym, interval="60", limit=60)
            if len(bars) < 30:
                continue
            
            cur_bar = bars[-1]
            cur_p = cur_bar["close"]
            closes = [b["close"] for b in bars]
            highs = [b["high"] for b in bars]
            lows = [b["low"] for b in bars]
            volumes = [b["volume"] for b in bars]
            
            ema9 = calc_ema(closes, 9)
            ema21 = calc_ema(closes, 21)
            ema50 = calc_ema(closes, 50)
            
            # 24-hour range
            high_24 = max(highs[-25:-1])
            low_24 = min(lows[-25:-1])
            bandwidth_24 = (high_24 - low_24) / cur_p
            
            # 48-hour range
            high_48 = max(highs[-49:-1]) if len(highs) >= 50 else high_24
            low_48 = min(lows[-49:-1]) if len(lows) >= 50 else low_24
            
            # Volume ratio
            avg_vol = sum(volumes[-20:-1]) / 19.0 if len(volumes) >= 20 else 1.0
            vol_ratio = cur_bar["volume"] / avg_vol if avg_vol > 0 else 1.0
            
            # Market Structure Trend
            if ema9 > ema21 > ema50:
                trend_regime = "STRONG_BULLISH"
            elif ema9 < ema21 < ema50:
                trend_regime = "STRONG_BEARISH"
            elif ema21 > ema50:
                trend_regime = "MODERATE_BULLISH"
            else:
                trend_regime = "CONSOLIDATION_NEUTRAL"
                
            # Squeeze assessment
            is_squeezed = (bandwidth_24 <= 0.028)
            
            # Target projections using Measured Moves and ATR
            atr14 = sum(max(highs[i]-lows[i], abs(highs[i]-closes[i-1])) for i in range(-14, 0)) / 14.0
            
            if trend_regime in ["STRONG_BULLISH", "MODERATE_BULLISH"]:
                direction = "BULLISH_CONTINUATION"
                target_4h = round(cur_p + (atr14 * 1.5), 4)
                target_24h = round(max(high_48 * 1.015, cur_p + (atr14 * 3.5)), 4)
                invalidation = round(ema21 * 0.995, 4)
                confidence = "62% (Trend Aligned)"
                pattern_ready = "BOS_RETEST" if cur_p >= high_24 * 0.995 else ("SQUEEZE_COILING" if is_squeezed else "EMA21_PULLBACK")
            elif trend_regime == "STRONG_BEARISH":
                direction = "BEARISH_CONTINUATION"
                target_4h = round(cur_p - (atr14 * 1.5), 4)
                target_24h = round(min(low_48 * 0.985, cur_p - (atr14 * 3.5)), 4)
                invalidation = round(ema21 * 1.005, 4)
                confidence = "58% (Trend Aligned)"
                pattern_ready = "SQUEEZE_COILING" if is_squeezed else "EMA21_PULLBACK"
            else:
                direction = "RANGE_EXPANSION_PENDING"
                target_4h = round(high_24 if cur_p < (high_24+low_24)/2 else low_24, 4)
                target_24h = round(high_48 if cur_p < (high_24+low_24)/2 else low_48, 4)
                invalidation = round(low_24 * 0.995, 4)
                confidence = "45% (Awaiting Squeeze Breakout)"
                pattern_ready = "VOLATILITY_SQUEEZE_BOX" if is_squeezed else "RANGE_BOUND"
                
            forecasts.append({
                "symbol": sym,
                "current_price": cur_p,
                "regime": trend_regime,
                "bandwidth_24h": round(bandwidth_24 * 100, 2),
                "is_compressed_squeeze": is_squeezed,
                "forecast_direction": direction,
                "target_4h": target_4h,
                "target_24h": target_24h,
                "invalidation_level": invalidation,
                "active_pattern": pattern_ready,
                "confidence": confidence
            })
        except Exception as e:
            print(f"Error {sym}: {e}")
            
    return forecasts

if __name__ == "__main__":
    fc = generate_live_forecasts()
    print(json.dumps(fc, indent=2))
