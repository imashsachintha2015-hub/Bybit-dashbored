#!/usr/bin/env python3
"""
CHAMPIONSHIP DUAL-REGIME AUTONOMOUS DAEMON
=========================================
Runs the Combined Championship System (Bullish v2.0 + Bearish v4.1)
in continuous 24/7 background mode:
  1. Checks BTC 15m relative to 200 EMA to determine Regime:
     - BULL REGIME: BTC > 200 EMA -> Evaluates Bullish Championship v2.0
     - BEAR REGIME: BTC < 200 EMA -> Evaluates Bearish Championship v4.1
  2. Enforces All 4 Golden Vetos on Bull and All 5 Vetos on Bear
  3. Manages Active Multi-Pair Positions:
     - 50% cash bank at TP1 (1.0R / 1.20R)
     - Immediate Stop Loss lock to Breakeven (+0.05R buffer)
     - 50% macro runner at TP2 (2.00R)
  4. Telemetry saved to scratch/championship_live_state.json
"""

import os
import sys
import json
import time
import urllib.request
from datetime import datetime, timezone

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT_DIR)

from backend_lib.championship_engine import ChampionshipDualRegimeEngine
from backend_lib import auto_trade_state

BASE_URL = os.environ.get("BYBIT_BASE_URL", "https://api.bybit.com")
LIVE_STATE_FILE = os.path.join(ROOT_DIR, "scratch", "championship_live_state.json")
LOG_FILE = os.path.join(ROOT_DIR, "scratch", "championship_daemon.log")

def log(msg):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    line = f"[CHAMPIONSHIP {ts}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass

def _http_get(path, retries=2):
    req = urllib.request.Request(f"https://api.bybit.com{path}", headers={"User-Agent": "MASIS-CHAMPIONSHIP/5.0"})
    for i in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=8) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception:
            if i == retries: return {}
            time.sleep(0.3)
    return {}

def fetch_klines(sym, interval="15", limit=200):
    raw = _http_get(f"/v5/market/kline?category=linear&symbol={sym}&interval={interval}&limit={limit}")
    bars = []
    for b in reversed(raw.get("result", {}).get("list", [])):
        bars.append({
            "start": int(b[0]),
            "open": float(b[1]),
            "high": float(b[2]),
            "low": float(b[3]),
            "close": float(b[4]),
            "volume": float(b[5])
        })
    return bars

def run_championship_cycle(engine):
    state = auto_trade_state.load()
    mode = state.get("strategyMode", "standard")
    is_championship = (mode == "championship") or state.get("championshipMode", False)

    # 1. Fetch BTC 15m Klines
    btc_bars = fetch_klines("BTCUSDT", interval="15", limit=205)
    if not btc_bars or len(btc_bars) < 200:
        log("Insufficient BTC 15m kline history to compute 200 EMA regime.")
        return

    regime, btc_c, btc_200 = engine.evaluate_regime(btc_bars)
    dist_200 = (btc_c - btc_200) / btc_200 * 100.0

    telemetry = {
        "timestamp": int(time.time()),
        "mode_active": is_championship,
        "strategy_mode": mode,
        "btc_price": btc_c,
        "btc_200_ema": round(btc_200, 2),
        "btc_dist_200_pct": round(dist_200, 2),
        "macro_regime": regime,
        "active_engine": f"{'BULLISH v2.0 (Longs Only)' if regime == 'BULL' else 'BEARISH v4.1 (Shorts Only)'}",
        "signals_detected": [],
        "last_updated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    }

    log(f"Macro Regime Scan: BTC ${btc_c:.1f} vs 200 EMA ${btc_200:.1f} ({dist_200:+.2f}%) -> {regime} REGIME ACTIVE")

    # 2. Scan universe
    symbols = [
        "ETHUSDT", "SOLUSDT", "XRPUSDT", "LINKUSDT", "SEIUSDT",
        "AVAXUSDT", "DOGEUSDT", "BNBUSDT", "DOTUSDT", "POLUSDT",
        "LTCUSDT", "NEARUSDT", "SUIUSDT", "ARBUSDT", "OPUSDT", "INJUSDT"
    ]

    for sym in symbols:
        try:
            bars = fetch_klines(sym, interval="15", limit=205)
            if not bars or len(bars) < 200: continue
            cand = engine.scan_candidate(sym, bars, btc_bars)
            if cand:
                telemetry["signals_detected"].append(cand)
                log(f"⭐ CHAMPIONSHIP SIGNAL: {sym} {cand['direction']} | Setup: {cand['archetype']} | Entry: {cand['entry_price']} | SL: {cand['stop_loss']} | TP1: {cand['tp1']} | TP2: {cand['tp2']}")
        except Exception as e:
            continue

    with open(LIVE_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(telemetry, f, indent=2)

    log(f"Scan cycle complete. Discovered {len(telemetry['signals_detected'])} Championship setups. Telemetry saved.")

def main():
    log("=" * 70)
    log("CHAMPIONSHIP DUAL-REGIME AUTONOMOUS DAEMON INITIALIZING")
    log("Macro Routing: BTC > 200 EMA -> Bull v2.0 | BTC < 200 EMA -> Bear v4.1")
    log("=" * 70)

    engine = ChampionshipDualRegimeEngine()
    once = "--once" in sys.argv

    while True:
        try:
            run_championship_cycle(engine)
        except Exception as e:
            log(f"Error in championship cycle: {e}")

        if once:
            break
        time.sleep(30)

if __name__ == "__main__":
    main()
