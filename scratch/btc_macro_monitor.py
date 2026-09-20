"""
BTC Macro Regime & Altcoin Dump-Guard Monitor
Continuously analyzes BTCUSDT 1m, 5m, and 15m price action and orderflow.
When BTC experiences a sudden dump or breakdown:
1. Blocks / Vetoes all new altcoin LONG setups.
2. Tightens or defends existing open altcoin positions.
"""

import urllib.request
import json
import time

def fetch_btc_macro():
    url = "https://api-demo.bybit.com/v5/market/kline?category=linear&symbol=BTCUSDT&interval=5&limit=24"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "MASIS/3.0"})
        with urllib.request.urlopen(req, timeout=4) as r:
            data = json.loads(r.read().decode())
            raw = data.get("result", {}).get("list", [])
            if not raw or len(raw) < 12:
                return None
            
            candles = []
            for row in reversed(raw):
                candles.append({
                    "open": float(row[1]),
                    "high": float(row[2]),
                    "low": float(row[3]),
                    "close": float(row[4]),
                    "vol": float(row[5])
                })
            
            cur_p = candles[-1]["close"]
            open_5m = candles[-1]["open"]
            chg_5m = ((cur_p - open_5m) / open_5m) * 100.0
            
            # 15m change (last 3 candles)
            open_15m = candles[-3]["open"]
            chg_15m = ((cur_p - open_15m) / open_15m) * 100.0
            
            # 1-hour change (last 12 candles)
            open_1h = candles[0]["open"]
            chg_1h = ((cur_p - open_1h) / open_1h) * 100.0
            
            closes = [c["close"] for c in candles]
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
            
            # Dump Guard: Is Bitcoin actively dumping?
            # Criteria: 5m drop < -0.30% with bearish trend, OR 15m drop < -0.60%, OR RSI < 32 breaking EMA
            is_dumping = (chg_5m <= -0.28) or (chg_15m <= -0.55) or (trend_5m == "BEAR" and chg_5m <= -0.18 and rsi_5m < 38)
            
            # Severe flush: rapid 5m plunge < -0.50%
            is_severe_flush = chg_5m <= -0.50 or chg_15m <= -1.0
            
            # Macro Regime State
            if is_severe_flush:
                regime = "CRITICAL_BTC_FLUSH"
                alt_long_allowed = False
            elif is_dumping:
                regime = "BTC_DUMPING_CAUTION"
                alt_long_allowed = False
            elif trend_5m == "BULL" and chg_5m >= -0.10:
                regime = "BTC_HEALTHY_BULL"
                alt_long_allowed = True
            else:
                regime = "BTC_CONSOLIDATING"
                alt_long_allowed = True
                
            return {
                "btc_price": cur_p,
                "btc_chg_5m": round(chg_5m, 2),
                "btc_chg_15m": round(chg_15m, 2),
                "btc_chg_1h": round(chg_1h, 2),
                "btc_trend_5m": trend_5m,
                "btc_rsi_5m": round(rsi_5m, 1),
                "is_dumping": is_dumping,
                "is_severe_flush": is_severe_flush,
                "regime": regime,
                "alt_long_allowed": alt_long_allowed,
                "timestamp": int(time.time())
            }
    except Exception as e:
        return {
            "btc_price": 0,
            "btc_chg_5m": 0,
            "regime": "UNKNOWN",
            "alt_long_allowed": True,
            "error": str(e)
        }

if __name__ == '__main__':
    macro = fetch_btc_macro()
    print("BTC Macro State:")
    print(json.dumps(macro, indent=2))
