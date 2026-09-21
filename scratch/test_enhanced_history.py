import os
import sys
import json
import urllib.request
import sqlite3

def fetch_klines(symbol, interval, limit=100):
    url = f"https://api-demo.bybit.com/v5/market/kline?category=linear&symbol={symbol}&interval={interval}&limit={limit}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "MASIS/3.0"})
        with urllib.request.urlopen(req, timeout=4) as r:
            data = json.loads(r.read().decode())
            raw = data.get("result", {}).get("list", [])
            candles = []
            for row in reversed(raw):
                candles.append({
                    "open": float(row[1]),
                    "high": float(row[2]),
                    "low": float(row[3]),
                    "close": float(row[4]),
                    "vol": float(row[5])
                })
            return candles
    except Exception as e:
        print("fetch_klines error:", e)
        return []

def analyze_price_level_historical_reaction(symbol, cur_price, tolerance_pct=0.6):
    k1h = fetch_klines(symbol, '60', 100) # Last 100 hours (~4 days)
    if not k1h or len(k1h) < 15:
        return {"summary": "Insufficient kline history."}

    zone_low = cur_price * (1.0 - (tolerance_pct / 100.0))
    zone_high = cur_price * (1.0 + (tolerance_pct / 100.0))

    # Identify distinct visits (cluster consecutive bars touching the zone)
    visits = []
    in_visit = False
    current_visit_bars = []
    
    for i, bar in enumerate(k1h[:-2]):
        touches_zone = (bar['low'] <= zone_high and bar['high'] >= zone_low)
        if touches_zone:
            if not in_visit:
                in_visit = True
                current_visit_bars = [i]
            else:
                current_visit_bars.append(i)
        else:
            if in_visit:
                # Visit concluded, evaluate reaction in subsequent 1-8 hours
                last_bar_idx = current_visit_bars[-1]
                future_bars = k1h[last_bar_idx+1:min(len(k1h), last_bar_idx+9)]
                if future_bars:
                    max_high_after = max(b['high'] for b in future_bars)
                    min_low_after = min(b['low'] for b in future_bars)
                    up_move_pct = ((max_high_after - cur_price) / cur_price) * 100.0
                    down_move_pct = ((min_low_after - cur_price) / cur_price) * 100.0
                    visits.append({
                        "visit_start": current_visit_bars[0],
                        "bars_in_zone": len(current_visit_bars),
                        "up_move_pct": round(up_move_pct, 2),
                        "down_move_pct": round(down_move_pct, 2)
                    })
                in_visit = False
                current_visit_bars = []
                
    if in_visit and current_visit_bars:
        last_bar_idx = current_visit_bars[-1]
        future_bars = k1h[last_bar_idx+1:min(len(k1h), last_bar_idx+9)]
        if future_bars:
            max_high_after = max(b['high'] for b in future_bars)
            min_low_after = min(b['low'] for b in future_bars)
            visits.append({
                "visit_start": current_visit_bars[0],
                "bars_in_zone": len(current_visit_bars),
                "up_move_pct": round(((max_high_after - cur_price) / cur_price) * 100.0, 2),
                "down_move_pct": round(((min_low_after - cur_price) / cur_price) * 100.0, 2)
            })

    if not visits:
        return {
            "tested_count": 0,
            "bounce_count": 0,
            "rejection_count": 0,
            "bounce_rate_pct": 50.0,
            "max_rally_pct": 0.0,
            "max_drop_pct": 0.0,
            "summary": f"Price {cur_price} is at an untested price level in the last 100 hours (all-time new local zone)."
        }

    tested_count = len(visits)
    bounces = [v for v in visits if v['up_move_pct'] >= 1.50]
    rejections = [v for v in visits if v['down_move_pct'] <= -1.50]
    
    bounce_count = len(bounces)
    rejection_count = len(rejections)
    bounce_rate = round((bounce_count / max(1, tested_count)) * 100.0, 1)
    
    max_rally = max(v['up_move_pct'] for v in visits)
    avg_rally = round(sum(v['up_move_pct'] for v in visits) / tested_count, 2)
    max_drop = min(v['down_move_pct'] for v in visits)
    avg_drop = round(sum(v['down_move_pct'] for v in visits) / tested_count, 2)
    
    if bounce_rate >= 60.0:
        reaction_nature = f"STRONG BUYER ACCUMULATION & DEMAND ZONE: {bounce_rate}% of visits launched rallies >= +1.5% (Tested {tested_count} times, max rally +{max_rally}%, avg rally +{avg_rally}%)."
    elif rejection_count > bounce_count:
        reaction_nature = f"SELLER OVERHANG / RESISTANCE ZONE: Rejections ({rejection_count}) dominate bounces ({bounce_count}) with max drop {max_drop}%."
    else:
        reaction_nature = f"CONSOLIDATION / EQUILIBRIUM ZONE: Tested {tested_count} times, max rally +{max_rally}%, max drop {max_drop}%."

    return {
        "tested_count": tested_count,
        "bounce_count": bounce_count,
        "rejection_count": rejection_count,
        "bounce_rate_pct": bounce_rate,
        "max_rally_pct": max_rally,
        "avg_rally_pct": avg_rally,
        "max_drop_pct": max_drop,
        "avg_drop_pct": avg_drop,
        "summary": reaction_nature
    }

# Test with LINKUSDT
price = 12.93
res = analyze_price_level_historical_reaction('LINKUSDT', price)
print("LINKUSDT @ 12.93 historical reaction:")
print(json.dumps(res, indent=2))
