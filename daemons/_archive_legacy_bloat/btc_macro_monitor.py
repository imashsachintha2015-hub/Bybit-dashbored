"""
BTC Macro Regime & Altcoin Dump-Guard Monitor
Continuously analyzes BTCUSDT 1m, 5m, and 15m price action and orderflow.
When BTC experiences a sudden dump or breakdown:
1. Blocks / Vetoes all new altcoin LONG setups.
2. Tightens or defends existing open altcoin positions.
3. Provides sub-second detection of 1-minute flash drops.
"""

import urllib.request
import json
import time

def fetch_btc_macro():
    # Fetch 5m candles for trend, RSI, 15m and 1h context
    url_5m = "https://api-demo.bybit.com/v5/market/kline?category=linear&symbol=BTCUSDT&interval=5&limit=24"
    # Fetch 1m candles for instant flash-dump detection within 60 seconds
    url_1m = "https://api-demo.bybit.com/v5/market/kline?category=linear&symbol=BTCUSDT&interval=1&limit=5"
    
    try:
        req_5m = urllib.request.Request(url_5m, headers={"User-Agent": "MASIS/3.0"})
        with urllib.request.urlopen(req_5m, timeout=4) as r:
            data_5m = json.loads(r.read().decode())
            raw_5m = data_5m.get("result", {}).get("list", [])
            
        if not raw_5m or len(raw_5m) < 12:
            return None
        
        # 1m klines
        raw_1m = []
        try:
            req_1m = urllib.request.Request(url_1m, headers={"User-Agent": "MASIS/3.0"})
            with urllib.request.urlopen(req_1m, timeout=3) as r1:
                data_1m = json.loads(r1.read().decode())
                raw_1m = data_1m.get("result", {}).get("list", [])
        except Exception:
            raw_1m = []
        
        candles_5m = []
        for row in reversed(raw_5m):
            candles_5m.append({
                "open": float(row[1]),
                "high": float(row[2]),
                "low": float(row[3]),
                "close": float(row[4]),
                "vol": float(row[5])
            })
        
        cur_p = candles_5m[-1]["close"]
        open_5m = candles_5m[-1]["open"]
        chg_5m = ((cur_p - open_5m) / open_5m) * 100.0
        
        # 1-minute flash change
        chg_1m = 0.0
        vol_spike_1m = False
        if raw_1m and len(raw_1m) >= 2:
            cur_1m = raw_1m[0]
            op_1m = float(cur_1m[1])
            cl_1m = float(cur_1m[4])
            cur_p = cl_1m  # use latest 1m mark price
            chg_1m = ((cl_1m - op_1m) / op_1m) * 100.0
            
            # Check 1m volume spike
            v_cur = float(cur_1m[5])
            v_prev = float(raw_1m[1][5])
            if v_cur >= max(1.0, v_prev * 1.8):
                vol_spike_1m = True
        
        # 15m change (last 3 candles)
        open_15m = candles_5m[-3]["open"]
        chg_15m = ((cur_p - open_15m) / open_15m) * 100.0
        
        # 1-hour change (last 12 candles)
        open_1h = candles_5m[0]["open"]
        chg_1h = ((cur_p - open_1h) / open_1h) * 100.0
        
        closes = [c["close"] for c in candles_5m]
        # EMA9 vs EMA21
        k9 = 2 / 10
        k21 = 2 / 22
        ema9 = sum(closes[:9]) / 9
        for c in closes[9:]: ema9 = c * k9 + ema9 * (1 - k9)
        ema21 = sum(closes[:21]) / 21
        for c in closes[21:]: ema21 = c * k21 + ema21 * (1 - k21)
        
        trend_5m = "BULL" if ema9 > ema21 else "BEAR"
        
        # Calculate 5m RSI
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i-1]
            gains.append(max(0, diff))
            losses.append(max(0, -diff))
        avg_g = sum(gains[-14:]) / 14 if len(gains) >= 14 else 1.0
        avg_l = sum(losses[-14:]) / 14 if len(losses) >= 14 else 1.0
        rs = avg_g / (avg_l + 1e-9)
        rsi_5m = 100 - (100 / (1 + rs))
        
        # Severe Flush: sudden 1m plunge <= -0.35% OR 5m drop <= -0.50% OR 15m drop <= -1.0%
        is_severe_flush = (chg_1m <= -0.35) or (chg_5m <= -0.50) or (chg_15m <= -1.0)
        
        # Active Dumping: 1m drop <= -0.18% OR 5m drop <= -0.25% OR 15m drop <= -0.50% OR Bearish EMA + 5m drop <= -0.12%
        is_dumping = (
            is_severe_flush or
            (chg_1m <= -0.18) or
            (chg_5m <= -0.25) or
            (chg_15m <= -0.50) or
            (trend_5m == "BEAR" and chg_5m <= -0.12 and rsi_5m < 42)
        )

        # ── BTC Short Opportunity Detection ────────────────────────────────
        # Identifies when BTC itself is a SHORT candidate (e.g. 87247→86469 type moves)
        # Conditions: BTC falling + volume spike + BEAR trend alignment
        is_btc_short_opportunity = (
            (chg_5m <= -0.40 or chg_1m <= -0.25) and
            trend_5m == "BEAR" and
            rsi_5m < 55 and
            vol_spike_1m
        )
        btc_short_runway_pct = round(abs(chg_5m), 2) if is_btc_short_opportunity else 0.0

        # ── Range-Bound / Consolidation Detection ──────────────────────────
        # When BTC is barely moving (tight range), relax runway requirements for ALT setups
        btc_24_candles_range = max(c["high"] for c in candles_5m[-12:]) - min(c["low"] for c in candles_5m[-12:])
        btc_range_pct = (btc_24_candles_range / cur_p) * 100.0 if cur_p > 0 else 0.5
        is_btc_range_bound = (btc_range_pct < 0.60 and abs(chg_1h) < 0.35)  # < 0.6% range in 1h = tight consolidation
        
        # Macro Regime Classification
        if is_severe_flush:
            regime = "CRITICAL_BTC_FLUSH"
            alt_long_allowed = False
            defense_mode = "EMERGENCY_EXIT"
        elif is_dumping:
            regime = "BTC_DUMPING_CAUTION"
            alt_long_allowed = False
            defense_mode = "BANK_PROFITS"
        elif abs(chg_5m) >= 0.85:
            regime = "BTC_EXTREME_VOLATILITY"
            alt_long_allowed = False
            defense_mode = "TIGHTEN_STOPS"
        elif trend_5m == "BULL" and chg_5m >= -0.08 and chg_1m >= -0.10:
            regime = "BTC_HEALTHY_BULL"
            alt_long_allowed = True
            defense_mode = "ALLOW_EXPANSION"
        elif is_btc_range_bound:
            regime = "BTC_RANGE_CONSOLIDATING"
            alt_long_allowed = True
            defense_mode = "ALLOW_TIGHT_RANGE"
        else:
            regime = "BTC_CONSOLIDATING"
            alt_long_allowed = True
            defense_mode = "STANDARD_DEFENSE"
            
        return {
            "btc_price": round(cur_p, 1),
            "btc_chg_1m": round(chg_1m, 2),
            "btc_chg_5m": round(chg_5m, 2),
            "btc_chg_15m": round(chg_15m, 2),
            "btc_chg_1h": round(chg_1h, 2),
            "btc_trend_5m": trend_5m,
            "btc_rsi_5m": round(rsi_5m, 1),
            "vol_spike_1m": vol_spike_1m,
            "is_dumping": is_dumping,
            "is_severe_flush": is_severe_flush,
            "is_btc_short_opportunity": is_btc_short_opportunity,
            "btc_short_runway_pct": btc_short_runway_pct,
            "is_btc_range_bound": is_btc_range_bound,
            "btc_range_pct": round(btc_range_pct, 3),
            "regime": regime,
            "defense_mode": defense_mode,
            "alt_long_allowed": alt_long_allowed,
            "timestamp": int(time.time())
        }
    except Exception as e:
        return {
            "btc_price": 0,
            "btc_chg_1m": 0,
            "btc_chg_5m": 0,
            "regime": "UNKNOWN",
            "defense_mode": "STANDARD_DEFENSE",
            "alt_long_allowed": True,
            "error": str(e)
        }

if __name__ == '__main__':
    macro = fetch_btc_macro()
    print("BTC Macro State:")
    print(json.dumps(macro, indent=2))
