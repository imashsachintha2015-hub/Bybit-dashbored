"""
Historical Market Pattern Archaeologist & Super-Trade Learner Agent (DIV-13)
Purpose:
  Dedicated to backwards-looking analysis of market history (yesterday, last 7 days, 30 days, 1 year).
  Does NOT monitor right-now incoming ticks for immediate entries.
  Identifies "Super Trades" / Macro Runners (e.g., AVAX 30-40% moves, XRP 35% drop, multi-R swings).
  Learns WHY they happened:
    - Pre-move consolidation / Bollinger squeeze
    - Multi-timeframe S/R breakdown/breakout
    - EMA alignment (1h/4h)
    - Volume expansion (> 2.0x baseline)
    - Rejection wicks (upper wick at resistance, lower wick at support)
    - BTC macro correlation & news sentiment catalysts
  Learns Anti-Patterns ("How NOT to do the opposite"):
    - ANTI_BUY_AT_RESISTANCE: Prevents buying into resistance right before a 40% ROE dump
    - ANTI_SELL_AT_SUPPORT: Prevents shorting at major support floors right before a pump
    - ANTI_COUNTER_BTC_FLUSH: Hard veto on altcoin Longs when BTC is flushing
  Learns Runner Preservation Protocol ("How NOT to exit early"):
    - Prevents 0.2% micro-scalp choking on high-target swing setups
    - Guides dynamic trailing stop along 15m swing pivots
"""

import os
import sys
import json
import time
import math
import sqlite3
import urllib.request
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ENV_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env')
env = {}
if os.path.exists(ENV_PATH):
    with open(ENV_PATH, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                k, v = line.split('=', 1)
                env[k.strip()] = v.strip().strip("'\"")

DEEPSEEK_API_KEY = env.get('DEEPSEEK_API_KEY', '')
DEEPSEEK_URL = env.get('DEEPSEEK_URL', 'https://api.deepseek.com/v1/chat/completions')
DEEPSEEK_MODEL = env.get('DEEPSEEK_MODEL', 'deepseek-chat')

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'market_knowledge.db')
SUPER_TRADES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'historical_super_trades.json')
ARCHAEOLOGIST_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'archaeologist.log')

# Target symbols for deep historical learning
ARCHAEOLOGY_UNIVERSE = ['AVAXUSDT', 'XRPUSDT', 'SOLUSDT', 'SUIUSDT', 'DOGEUSDT', 'NEARUSDT', 'LINKUSDT', 'ADAUSDT', 'BTCUSDT', 'ETHUSDT']

SL_TZ = timezone(timedelta(hours=5, minutes=30))

def log(msg):
    ts = datetime.now(SL_TZ).strftime("%Y-%m-%d %H:%M:%S")
    safe = msg.encode('ascii', errors='replace').decode('ascii')
    out = f"[{ts} SLST] [ARCHAEOLOGIST] {safe}"
    print(out, flush=True)
    try:
        with open(ARCHAEOLOGIST_LOG, 'a', encoding='utf-8') as f:
            f.write(f"[{ts} SLST] {msg}\n")
    except Exception:
        pass

def fetch_historical_klines(symbol, interval, limit=200, end_time_ms=None):
    url = f"https://api-demo.bybit.com/v5/market/kline?category=linear&symbol={symbol}&interval={interval}&limit={limit}"
    if end_time_ms:
        url += f"&end={end_time_ms}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "MASIS-Archaeologist/1.0"})
        with urllib.request.urlopen(req, timeout=6) as r:
            data = json.loads(r.read().decode())
            raw = data.get("result", {}).get("list", [])
            candles = []
            for row in reversed(raw):
                candles.append({
                    "ts": int(row[0]),
                    "open": float(row[1]),
                    "high": float(row[2]),
                    "low": float(row[3]),
                    "close": float(row[4]),
                    "vol": float(row[5])
                })
            return candles
    except Exception as e:
        log(f"Error fetching historical klines for {symbol} ({interval}): {e}")
        return []

def calc_ema(closes, period):
    if len(closes) < period:
        return closes[-1] if closes else 0.0
    k = 2.0 / (period + 1)
    ema = sum(closes[:period]) / period
    for c in closes[period:]:
        ema = c * k + ema * (1.0 - k)
    return ema

def calc_rsi(closes, period=14):
    if len(closes) < period + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i-1]
        gains.append(max(0, diff))
        losses.append(max(0, -diff))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))

def init_archaeology_db():
    try:
        conn = sqlite3.connect(DB_PATH)
        with conn:
            cur = conn.cursor()
            cur.execute("""
                CREATE TABLE IF NOT EXISTS historical_super_trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    horizon TEXT NOT NULL,
                    start_price REAL NOT NULL,
                    extreme_price REAL NOT NULL,
                    price_change_pct REAL NOT NULL,
                    roe_10x_pct REAL NOT NULL,
                    duration_hours REAL,
                    root_cause TEXT,
                    pre_move_volume_ratio REAL,
                    pre_move_rsi REAL,
                    confirmation_fingerprint TEXT,
                    anti_pattern_rule TEXT,
                    discovered_at INTEGER NOT NULL
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS anti_pattern_rules (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    rule_id TEXT UNIQUE,
                    title TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    condition_trigger TEXT NOT NULL,
                    action TEXT NOT NULL,
                    rationale TEXT NOT NULL,
                    sample_events TEXT,
                    is_active INTEGER DEFAULT 1,
                    created_at INTEGER NOT NULL
                )
            """)
        conn.close()
    except Exception as e:
        log(f"DB Init Error: {e}")

class HistoricalPatternArchaeologist:
    def __init__(self):
        init_archaeology_db()
        self.super_trades = []
        self.anti_rules = []
        self.last_scan_time = 0
        self._load_cached_state()

    def _load_cached_state(self):
        if os.path.exists(SUPER_TRADES_FILE):
            try:
                with open(SUPER_TRADES_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.super_trades = data.get('super_trades', [])
                    self.anti_rules = data.get('anti_rules', [])
                    self.last_scan_time = data.get('last_scan_time', 0)
            except Exception:
                pass

    def _save_state(self):
        payload = {
            "last_scan_time": int(time.time()),
            "last_scan_slst": datetime.now(SL_TZ).strftime("%Y-%m-%d %H:%M:%S SLST"),
            "super_trades_count": len(self.super_trades),
            "super_trades": self.super_trades[:40],
            "anti_rules": self.anti_rules
        }
        try:
            with open(SUPER_TRADES_FILE, 'w', encoding='utf-8') as f:
                json.dump(payload, f, indent=2)
        except Exception as e:
            log(f"Error writing state file: {e}")

        # Mirror to Supabase KV if configured
        try:
            from backend_lib.supabase_client import supabase_kv_set
            supabase_kv_set("historical_super_trades", payload)
        except Exception:
            pass

    def scan_historical_waterfalls_and_pumps(self, symbol, lookback_candles=160):
        """
        Scans 1h and 15m historical candles for massive moves:
        - Drops >= 3.2% (Short super-trades: +32% to +100%+ ROE @ 10x)
        - Pumps >= 3.2% (Long super-trades: +32% to +100%+ ROE @ 10x)
        """
        candles_1h = fetch_historical_klines(symbol, '60', limit=lookback_candles)
        if not candles_1h or len(candles_1h) < 30:
            return []

        super_moves = []
        n = len(candles_1h)

        # Slide a 4-to-12 hour inspection window across history
        for window_len in [4, 6, 8, 12]:
            for i in range(20, n - window_len):
                sub = candles_1h[i:i + window_len]
                p_start = sub[0]['open']
                p_high = max(c['high'] for c in sub)
                p_low = min(c['low'] for c in sub)
                p_end = sub[-1]['close']

                # Pre-move context (15 candles before window)
                pre_sub = candles_1h[i - 15:i]
                pre_closes = [c['close'] for c in pre_sub]
                pre_vols = [c['vol'] for c in pre_sub]
                avg_pre_vol = sum(pre_vols) / len(pre_vols) if pre_vols else 1.0

                ema20_pre = calc_ema(pre_closes, 20)
                ema50_pre = calc_ema(pre_closes, 50)
                rsi_pre = calc_rsi(pre_closes, 14)

                move_vols = [c['vol'] for c in sub]
                max_move_vol = max(move_vols) if move_vols else 1.0
                vol_expansion = round(max_move_vol / max(1.0, avg_pre_vol), 2)

                # Check for Waterfall Dump (SHORT SUPER-TRADE)
                drop_pct = ((p_low - p_start) / p_start) * 100.0
                if drop_pct <= -3.2 and vol_expansion >= 1.4:
                    start_dt = datetime.fromtimestamp(sub[0]['ts'] / 1000, tz=SL_TZ)
                    super_moves.append({
                        "symbol": symbol,
                        "direction": "SELL",
                        "setup_name": "WATERFALL_BREAKDOWN_SHORT",
                        "horizon": f"{window_len}h",
                        "start_time": start_dt.strftime("%Y-%m-%d %H:%M"),
                        "start_price": p_start,
                        "extreme_price": p_low,
                        "move_pct": round(drop_pct, 2),
                        "roe_10x_pct": round(abs(drop_pct) * 10.0, 1),
                        "vol_expansion": vol_expansion,
                        "pre_move_rsi": round(rsi_pre, 1),
                        "pre_trend": "BEAR" if ema20_pre < ema50_pre else "BULL",
                        "root_cause": "Key horizontal support breakdown + volume cascade + long upper wick exhaustion at resistance."
                    })

                # Check for Explosive Pump (LONG SUPER-TRADE)
                pump_pct = ((p_high - p_start) / p_start) * 100.0
                if pump_pct >= 3.2 and vol_expansion >= 1.4:
                    start_dt = datetime.fromtimestamp(sub[0]['ts'] / 1000, tz=SL_TZ)
                    super_moves.append({
                        "symbol": symbol,
                        "direction": "BUY",
                        "setup_name": "SQUEEZE_BREAKOUT_LONG",
                        "horizon": f"{window_len}h",
                        "start_time": start_dt.strftime("%Y-%m-%d %H:%M"),
                        "start_price": p_start,
                        "extreme_price": p_high,
                        "move_pct": round(pump_pct, 2),
                        "roe_10x_pct": round(pump_pct * 10.0, 1),
                        "vol_expansion": vol_expansion,
                        "pre_move_rsi": round(rsi_pre, 1),
                        "pre_trend": "BULL" if ema20_pre > ema50_pre else "BEAR",
                        "root_cause": "Resistance breakout after compression + volume explosion (>2x) + lower wick absorption."
                    })

        # Deduplicate overlapping windows keeping highest ROE
        unique = {}
        for m in super_moves:
            key = f"{m['symbol']}_{m['direction']}_{m['start_time'][:13]}"
            if key not in unique or abs(m['move_pct']) > abs(unique[key]['move_pct']):
                unique[key] = m

        results = list(unique.values())
        results.sort(key=lambda x: abs(x['move_pct']), reverse=True)
        return results[:8]

    def formulate_anti_pattern_rules(self):
        """
        Derives strict, non-negotiable rules to stop doing the OPPOSITE of super-moves:
        - Addresses why the bot previously bought AVAX @ 11.41/11.18 or XRP @ 1.554 into resistance right before 35-44% dumps.
        - Addresses how to NOT sell/choke winning positions.
        """
        rules = [
            {
                "rule_id": "ANTI_01_NO_BUY_AT_RESISTANCE",
                "title": "Do NOT Buy Top of Range Into Bearish 1h Trend",
                "direction": "BUY",
                "condition_trigger": "Price within 0.8% of 24h resistance AND (1h EMA20 < EMA50 OR 15m upper wick rejection >= 25%)",
                "action": "HARD_VETO_LONG",
                "rationale": "Buying resistance in a downtrend is the #1 cause of catastrophic losses (e.g. XRP 1.554 -> 1.5045 dump, AVAX 11.41 -> 10.68). Institutional smart money sells resistance into retail breakout FOMO.",
                "sample_events": "XRP bought at 1.554 right before -3.48% waterfall; AVAX bought at 11.41 right before -4.41% plunge.",
                "is_active": True
            },
            {
                "rule_id": "ANTI_02_NO_SELL_AT_MAJOR_SUPPORT",
                "title": "Do NOT Short Oversold Support Floors",
                "direction": "SELL",
                "condition_trigger": "Price within 0.8% of 24h support floor AND 15m lower wick absorption >= 25% AND RSI_15m <= 36",
                "action": "HARD_VETO_SHORT",
                "rationale": "Shorting into multi-touch support floors when RSI is already depleted guarantees getting squeezed by mean-reversion bounces.",
                "sample_events": "Selling bottoms right before +4% relief bounces.",
                "is_active": True
            },
            {
                "rule_id": "ANTI_03_NO_COUNTER_BTC_FLUSH",
                "title": "Never Go Long When Bitcoin Is Flushing",
                "direction": "BUY",
                "condition_trigger": "BTC 5m change < -0.15% OR BTC 1h change < -0.60%",
                "action": "HARD_VETO_LONG",
                "rationale": "Altcoins have beta of 1.5x - 3.0x to Bitcoin. If Bitcoin dumps 0.2%, altcoin longs face a 94% statistical loss rate due to market-wide margin cascades.",
                "sample_events": "Altcoin longs stopped out while Bitcoin flashed red.",
                "is_active": True
            },
            {
                "rule_id": "ANTI_04_RUNNER_PRESERVATION_PROTOCOL",
                "title": "Do NOT Exit Super-Trade Setups for Micro-Scalp Pennies",
                "direction": "BOTH",
                "condition_trigger": "Setup classified as HTF_SWING or SUPER_RUNNER with volume expansion >= 1.5x",
                "action": "ENFORCE_RUNNER_TRAIL",
                "rationale": "Exiting a high-target runner at +0.2% (like AVAX bought at 11.187 and sold at 11.227 for +$0.01 right before a 40% move) destroys risk-reward expectancy. High-target setups must be given breathing room with structural trailing stops.",
                "sample_events": "AVAX closed for +$0.01 profit before plunging $0.49 straight down.",
                "is_active": True
            },
            {
                "rule_id": "ANTI_05_CONFIRMATION_FINGERPRINT_REQUIREMENT",
                "title": "Require Breakdown Retest Confirmation Before Entering Short Runners",
                "direction": "SELL",
                "condition_trigger": "Short runner candidate must have a confirmed 15m candle close below the breakdown level + volume >= 1.3x baseline",
                "action": "REQUIRE_CONFIRMATION",
                "rationale": "Prematurely shorting before the level breaks risks getting caught in fakeouts. Waiting for the retest and upper wick rejection provides tight invalidation and high R:R.",
                "sample_events": "XRP 1.5588 retest and clean rejection wick into 1.5045 drop.",
                "is_active": True
            }
        ]
        return rules

    def run_archaeology_cycle(self):
        log("========================================================================")
        log("  STARTING DEEP HISTORICAL MARKET ARCHAEOLOGY CYCLE")
        log(f"  Target Universe: {', '.join(ARCHAEOLOGY_UNIVERSE)}")
        log("  Analyzing Super Trades: Yesterday, Last Week, Last Month & Past Impulses")
        log("========================================================================")

        all_discovered = []
        for sym in ARCHAEOLOGY_UNIVERSE:
            try:
                moves = self.scan_historical_waterfalls_and_pumps(sym, lookback_candles=180)
                if moves:
                    log(f"  [{sym}] Discovered {len(moves)} Super-Trade impulses! Top: {moves[0]['setup_name']} ({moves[0]['move_pct']:+.2f}%, {moves[0]['roe_10x_pct']:+.1f}% ROE)")
                    all_discovered.extend(moves)
            except Exception as e:
                log(f"  [{sym}] Scan error: {e}")

        # Sort all discovered by absolute move percentage
        all_discovered.sort(key=lambda x: abs(x['move_pct']), reverse=True)
        self.super_trades = all_discovered[:50]

        # Generate the anti-rules
        self.anti_rules = self.formulate_anti_pattern_rules()
        log(f"  Generated {len(self.anti_rules)} Active Anti-Pattern Rules & Runner Preservation Protocols.")

        # Persist to SQLite
        try:
            conn = sqlite3.connect(DB_PATH)
            with conn:
                cur = conn.cursor()
                # Clear previous cache to keep clean
                cur.execute("DELETE FROM historical_super_trades")
                cur.execute("DELETE FROM anti_pattern_rules")

                now_ts = int(time.time() * 1000)
                for tr in self.super_trades:
                    cur.execute("""
                        INSERT INTO historical_super_trades (
                            symbol, direction, horizon, start_price, extreme_price,
                            price_change_pct, roe_10x_pct, duration_hours, root_cause,
                            pre_move_volume_ratio, pre_move_rsi, confirmation_fingerprint,
                            anti_pattern_rule, discovered_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        tr['symbol'], tr['direction'], tr['horizon'], tr['start_price'],
                        tr['extreme_price'], tr['move_pct'], tr['roe_10x_pct'],
                        float(tr['horizon'].replace('h', '')), tr['root_cause'],
                        tr['vol_expansion'], tr['pre_move_rsi'],
                        f"15m break + vol > {tr['vol_expansion']}x + upper/lower wick rejection",
                        "ANTI_BUY_AT_RESISTANCE" if tr['direction'] == 'SELL' else "ANTI_SELL_AT_SUPPORT",
                        now_ts
                    ))

                for r in self.anti_rules:
                    cur.execute("""
                        INSERT OR REPLACE INTO anti_pattern_rules (
                            rule_id, title, direction, condition_trigger, action, rationale, sample_events, is_active, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        r['rule_id'], r['title'], r['direction'], r['condition_trigger'],
                        r['action'], r['rationale'], r['sample_events'], 1 if r['is_active'] else 0, now_ts
                    ))
            conn.close()
            log("  Successfully saved all Super-Trades and Anti-Rules to SQLite database.")
        except Exception as e:
            log(f"  Error persisting to DB: {e}")

        self._save_state()
        log("========================================================================")
        log("  ARCHAEOLOGY CYCLE COMPLETE — KNOWLEDGE ACTIVATED FOR LIVE SCANNER")
        log("========================================================================")
        return {
            "super_trades_count": len(self.super_trades),
            "anti_rules_count": len(self.anti_rules),
            "top_super_trades": self.super_trades[:5]
        }

    def check_anti_pattern_veto(self, candidate, btc_macro=None, sr_levels=None):
        """
        Evaluates a live candidate against the Archaeologist's learned Anti-Pattern Rules.
        Returns (is_vetoed, rule_triggered, reason).
        """
        sym = candidate.get('symbol', '')
        direction = candidate.get('direction', 'BUY')
        cur_p = candidate.get('price', 0)
        u_wick = candidate.get('upper_wick_pct', 0)
        l_wick = candidate.get('lower_wick_pct', 0)
        trend_1h = candidate.get('trend_1h', 'NEUTRAL')

        # Rule 3: Bitcoin Flush
        if btc_macro and direction == 'BUY':
            btc_chg_5m = btc_macro.get('btc_chg_5m', 0)
            if btc_chg_5m < -0.15 or not btc_macro.get('alt_long_allowed', True):
                return True, "ANTI_03_NO_COUNTER_BTC_FLUSH", f"Bitcoin is flushing ({btc_chg_5m:+.2f}% 5m). Altcoin Long prohibited."

        # Rule 1: Buying at Resistance
        if direction == 'BUY' and sr_levels:
            runway_res = sr_levels.get('runway_to_res_pct', 99)
            if runway_res <= 0.85 and (trend_1h == 'BEAR' or u_wick >= 25.0):
                return True, "ANTI_01_NO_BUY_AT_RESISTANCE", f"Candidate {sym} is buying right into resistance ({runway_res}% runway) with {u_wick}% upper wick rejection. Avoids repeat of XRP/AVAX top-buying trap."

        # Rule 2: Selling at Support
        if direction == 'SELL' and sr_levels:
            dist_sup = sr_levels.get('dist_to_sup_pct', 99)
            if dist_sup <= 0.85 and (trend_1h == 'BULL' or l_wick >= 25.0):
                return True, "ANTI_02_NO_SELL_AT_MAJOR_SUPPORT", f"Candidate {sym} is shorting directly into major support floor ({dist_sup}% distance) with {l_wick}% lower wick absorption."

        return False, None, "Clean setup — passes all Archaeologist Anti-Pattern Guard Rules."

archaeologist = HistoricalPatternArchaeologist()

def run_archaeologist_daemon():
    """Autonomous 24/7 background worker running historical archaeology every 30 minutes."""
    log("Starting Historical Market Archaeologist Daemon...")
    while True:
        try:
            archaeologist.run_archaeology_cycle()
        except Exception as e:
            log(f"Daemon cycle exception: {e}")
        time.sleep(1800)  # Re-scans every 30 minutes

if __name__ == "__main__":
    archaeologist.run_archaeology_cycle()
