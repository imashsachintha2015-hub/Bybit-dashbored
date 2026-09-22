"""
Historical Market Pattern Archaeologist & Super-Trade Learner Agent (DIV-13)
REBUILT — Incremental Learning Architecture

Design:
  FIRST RUN:  Deep-scans 6 months of history per symbol (paginated API).
              Stores ALL discovered super-trades in SQLite — never deleted.

  EVERY 30m:  Fetches only NEW candles since last scan timestamp per symbol.
              APPENDS new discoveries — DB grows permanently.
              Never wipes previous knowledge.

  DB Schema:  Tracks last_scanned_ts per symbol so it knows exactly where
              to continue from each cycle.
"""

import os
import sys
import json
import time
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

DB_PATH           = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'market_knowledge.db')
SUPER_TRADES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'historical_super_trades.json')
ARCHAEOLOGIST_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'archaeologist.log')

SL_TZ = timezone(timedelta(hours=5, minutes=30))

# Full 24-coin universe — matches scanner
ARCHAEOLOGY_UNIVERSE = [
    'BTCUSDT', 'ETHUSDT', 'BNBUSDT',
    'SOLUSDT', 'AVAXUSDT', 'NEARUSDT', 'SUIUSDT', 'APTUSDT', 'TIAUSDT', 'SEIUSDT',
    'LINKUSDT', 'INJUSDT', 'JUPUSDT', 'STRKUSDT',
    'OPUSDT', 'ARBUSDT', 'MATICUSDT',
    'XRPUSDT', 'ADAUSDT', 'DOGEUSDT',
    'WIFUSDT', 'PEPEUSDT', 'BONKUSDT', 'FLOKIUSDT'
]

# How far back to deep-scan on first boot
SIX_MONTHS_MS = 6 * 30 * 24 * 60 * 60 * 1000

# Detection thresholds
MIN_MOVE_LARGE_CAP = 0.80   # BTC/ETH — 0.8% qualifies (catches 87247->86469 type moves)
MIN_MOVE_ALTCOIN   = 3.2    # Altcoins — 3.2%+ to qualify as super-trade
MIN_VOL_EXPANSION  = 1.35   # Volume must spike vs baseline

LARGE_CAPS = {'BTCUSDT', 'ETHUSDT', 'BNBUSDT'}


def log(msg):
    ts = datetime.now(SL_TZ).strftime("%Y-%m-%d %H:%M:%S")
    safe = msg.encode('ascii', errors='replace').decode('ascii')
    out = f"[{ts} SLST] [DIV-13] {safe}"
    print(out, flush=True)
    try:
        with open(ARCHAEOLOGIST_LOG, 'a', encoding='utf-8') as f:
            f.write(f"[{ts} SLST] {msg}\n")
    except Exception:
        pass


# ── Database Setup ─────────────────────────────────────────────────────────

def init_db():
    conn = sqlite3.connect(DB_PATH)
    with conn:
        cur = conn.cursor()
        # Super-trades: accumulates forever, never deleted
        cur.execute("""
            CREATE TABLE IF NOT EXISTS historical_super_trades (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol            TEXT NOT NULL,
                direction         TEXT NOT NULL,
                setup_name        TEXT,
                horizon           TEXT,
                start_time        TEXT NOT NULL,
                start_price       REAL NOT NULL,
                extreme_price     REAL NOT NULL,
                price_change_pct  REAL NOT NULL,
                roe_10x_pct       REAL NOT NULL,
                vol_expansion     REAL,
                pre_move_rsi      REAL,
                pre_trend         TEXT,
                root_cause        TEXT,
                candle_ts_ms      INTEGER,
                discovered_at     INTEGER NOT NULL,
                UNIQUE(symbol, direction, candle_ts_ms, horizon)
            )
        """)
        # Per-symbol scan progress — remembers where we left off
        cur.execute("""
            CREATE TABLE IF NOT EXISTS scan_progress (
                symbol           TEXT PRIMARY KEY,
                last_scanned_ts  INTEGER NOT NULL,
                total_found      INTEGER DEFAULT 0,
                last_updated     INTEGER NOT NULL
            )
        """)
        # Anti-pattern rules
        cur.execute("""
            CREATE TABLE IF NOT EXISTS anti_pattern_rules (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                rule_id           TEXT UNIQUE,
                title             TEXT NOT NULL,
                direction         TEXT NOT NULL,
                condition_trigger TEXT NOT NULL,
                action            TEXT NOT NULL,
                rationale         TEXT NOT NULL,
                sample_events     TEXT,
                is_active         INTEGER DEFAULT 1,
                created_at        INTEGER NOT NULL
            )
        """)
        # Missed trade journal
        cur.execute("""
            CREATE TABLE IF NOT EXISTS missed_trade_journal (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol         TEXT NOT NULL,
                direction      TEXT NOT NULL,
                start_price    REAL,
                end_price      REAL,
                move_pct       REAL,
                roe_10x_pct    REAL,
                reason_missed  TEXT,
                candle_ts_ms   INTEGER,
                logged_at      INTEGER NOT NULL
            )
        """)
    conn.close()


def get_last_scanned_ts(symbol):
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("SELECT last_scanned_ts FROM scan_progress WHERE symbol = ?", (symbol,))
        row = cur.fetchone()
        conn.close()
        return row[0] if row else None
    except Exception:
        return None


def update_scan_progress(symbol, scanned_up_to_ts, new_finds):
    try:
        conn = sqlite3.connect(DB_PATH)
        with conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO scan_progress (symbol, last_scanned_ts, total_found, last_updated)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(symbol) DO UPDATE SET
                    last_scanned_ts = MAX(last_scanned_ts, excluded.last_scanned_ts),
                    total_found     = total_found + excluded.total_found,
                    last_updated    = excluded.last_updated
            """, (symbol, scanned_up_to_ts, new_finds, int(time.time() * 1000)))
        conn.close()
    except Exception as e:
        log(f"Progress update error for {symbol}: {e}")


def save_super_trade(trade):
    """INSERT OR IGNORE — never overwrites, just grows the DB."""
    try:
        conn = sqlite3.connect(DB_PATH)
        with conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT OR IGNORE INTO historical_super_trades (
                    symbol, direction, setup_name, horizon, start_time,
                    start_price, extreme_price, price_change_pct, roe_10x_pct,
                    vol_expansion, pre_move_rsi, pre_trend, root_cause,
                    candle_ts_ms, discovered_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                trade['symbol'], trade['direction'], trade['setup_name'], trade['horizon'],
                trade['start_time'], trade['start_price'], trade['extreme_price'],
                trade['move_pct'], trade['roe_10x_pct'], trade.get('vol_expansion', 0),
                trade.get('pre_move_rsi', 50), trade.get('pre_trend', 'UNKNOWN'),
                trade.get('root_cause', ''), trade.get('candle_ts_ms', 0),
                int(time.time() * 1000)
            ))
        conn.close()
        return True
    except Exception as e:
        log(f"DB save error: {e}")
        return False


def log_missed_trade(symbol, direction, start_price, end_price, move_pct, candle_ts_ms, reason):
    try:
        conn = sqlite3.connect(DB_PATH)
        with conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO missed_trade_journal
                (symbol, direction, start_price, end_price, move_pct, roe_10x_pct,
                 reason_missed, candle_ts_ms, logged_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (symbol, direction, start_price, end_price, move_pct,
                  round(abs(move_pct) * 10, 1), reason, candle_ts_ms,
                  int(time.time() * 1000)))
        conn.close()
    except Exception:
        pass


# ── API Fetcher ────────────────────────────────────────────────────────────

def fetch_klines_page(symbol, interval_min, limit=200, end_time_ms=None):
    url = (f"https://api-demo.bybit.com/v5/market/kline"
           f"?category=linear&symbol={symbol}&interval={interval_min}&limit={limit}")
    if end_time_ms:
        url += f"&end={end_time_ms}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "MASIS-Archaeologist/2.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read().decode())
            raw = data.get("result", {}).get("list", [])
            if not raw:
                return [], None
            candles = []
            for row in reversed(raw):
                candles.append({
                    "ts":    int(row[0]),
                    "open":  float(row[1]),
                    "high":  float(row[2]),
                    "low":   float(row[3]),
                    "close": float(row[4]),
                    "vol":   float(row[5])
                })
            oldest_ts = candles[0]['ts'] if candles else None
            return candles, oldest_ts
    except Exception as e:
        log(f"  API error {symbol}: {e}")
        return [], None


def fetch_full_history(symbol, interval_min, from_ts_ms, to_ts_ms):
    """
    Paginates backwards from to_ts_ms down to from_ts_ms.
    This is how we go 6 months back — page by page.
    Returns all candles oldest-first.
    """
    all_candles = []
    cursor_ts = to_ts_ms
    pages = 0

    while cursor_ts > from_ts_ms and pages < 100:
        page, oldest_ts = fetch_klines_page(symbol, interval_min, limit=200, end_time_ms=cursor_ts)
        if not page or oldest_ts is None:
            break
        in_range = [c for c in page if c['ts'] >= from_ts_ms]
        all_candles = in_range + all_candles
        pages += 1
        if oldest_ts >= cursor_ts:
            break
        cursor_ts = oldest_ts - 1
        time.sleep(0.12)

    return all_candles


# ── Pattern Detection ──────────────────────────────────────────────────────

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
    ag = sum(gains[:period]) / period
    al = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        ag = (ag * (period - 1) + gains[i]) / period
        al = (al * (period - 1) + losses[i]) / period
    return 100.0 if al == 0 else 100.0 - (100.0 / (1.0 + ag / al))


def detect_super_moves(symbol, candles_1h):
    """Fast O(n) pattern detector using incremental EMA/RSI pre-compute."""
    n = len(candles_1h)
    if n < 30:
        return []
    is_large = symbol in LARGE_CAPS
    min_move = MIN_MOVE_LARGE_CAP if is_large else MIN_MOVE_ALTCOIN
    found = {}
    opens  = [c['open']  for c in candles_1h]
    highs  = [c['high']  for c in candles_1h]
    lows   = [c['low']   for c in candles_1h]
    closes = [c['close'] for c in candles_1h]
    vols   = [c['vol']   for c in candles_1h]
    tss    = [c['ts']    for c in candles_1h]
    k20 = 2.0 / 21.0
    k50 = 2.0 / 51.0
    ema20 = sum(closes[:20]) / 20.0
    ema50 = sum(closes[:min(50, n)]) / min(50, n)
    P = 14
    diffs_seed = [closes[i] - closes[i-1] for i in range(1, 16)]
    avg_g = sum(max(0, d) for d in diffs_seed) / P
    avg_l = sum(max(0, -d) for d in diffs_seed) / P
    pre_avg_vol = [1.0] * n
    pre_rsi     = [50.0] * n
    pre_bull    = [True] * n
    vol_sum     = sum(vols[:15])
    for i in range(20, n):
        if i >= 15:
            vol_sum = vol_sum - vols[i - 15] + vols[i - 1]
        pre_avg_vol[i] = vol_sum / 15.0
        diff = closes[i] - closes[i - 1]
        avg_g = (avg_g * (P - 1) + max(0.0, diff)) / P
        avg_l = (avg_l * (P - 1) + max(0.0, -diff)) / P
        pre_rsi[i] = 100.0 - (100.0 / (1.0 + avg_g / avg_l)) if avg_l > 0 else 100.0
        c = closes[i]
        ema20 = c * k20 + ema20 * (1 - k20)
        ema50 = c * k50 + ema50 * (1 - k50)
        pre_bull[i] = ema20 >= ema50
    windows = [2, 4, 6, 8, 12] if is_large else [4, 6, 8, 12]
    for window_len in windows:
        for i in range(20, n - window_len):
            p_start   = opens[i]
            p_high    = max(highs[i:i + window_len])
            p_low     = min(lows[i:i + window_len])
            max_w_vol = max(vols[i:i + window_len])
            avg_pv    = pre_avg_vol[i]
            vol_exp   = round(max_w_vol / max(1.0, avg_pv), 2)
            if vol_exp < MIN_VOL_EXPANSION:
                continue
            rsi   = pre_rsi[i]
            trend = "BULL" if pre_bull[i] else "BEAR"
            ts    = tss[i]
            st    = datetime.fromtimestamp(ts / 1000, tz=SL_TZ).strftime("%Y-%m-%d %H:%M")
            drop_pct = ((p_low - p_start) / p_start) * 100.0
            if drop_pct <= -min_move:
                key = f"{symbol}_SELL_{ts}_{window_len}h"
                if key not in found or abs(drop_pct) > abs(found[key]['move_pct']):
                    found[key] = {"symbol":symbol,"direction":"SELL","setup_name":"WATERFALL_BREAKDOWN_SHORT" if abs(drop_pct)>=3.0 else "BTC_FLUSH_SHORT","horizon":f"{window_len}h","start_time":st,"start_price":p_start,"extreme_price":p_low,"move_pct":round(drop_pct,3),"roe_10x_pct":round(abs(drop_pct)*10,1),"vol_expansion":vol_exp,"pre_move_rsi":round(rsi,1),"pre_trend":trend,"root_cause":f"Breakdown +{vol_exp}x vol RSI{rsi:.1f} {drop_pct:.2f}% in {window_len}h","candle_ts_ms":ts}
            pump_pct = ((p_high - p_start) / p_start) * 100.0
            if pump_pct >= min_move:
                key = f"{symbol}_BUY_{ts}_{window_len}h"
                if key not in found or abs(pump_pct) > abs(found[key]['move_pct']):
                    found[key] = {"symbol":symbol,"direction":"BUY","setup_name":"SQUEEZE_BREAKOUT_LONG" if pump_pct>=3.0 else "BTC_SURGE_LONG","horizon":f"{window_len}h","start_time":st,"start_price":p_start,"extreme_price":p_high,"move_pct":round(pump_pct,3),"roe_10x_pct":round(pump_pct*10,1),"vol_expansion":vol_exp,"pre_move_rsi":round(rsi,1),"pre_trend":trend,"root_cause":f"Breakout +{vol_exp}x vol surge RSI{rsi:.1f} +{pump_pct:.2f}% in {window_len}h","candle_ts_ms":ts}
    return list(found.values())


ANTI_PATTERN_RULES = [
    {
        "rule_id": "ANTI_01_NO_BUY_AT_RESISTANCE",
        "title": "Do NOT Buy Top of Range Into Bearish 1h Trend",
        "direction": "BUY",
        "condition_trigger": "Price within 0.85% of 24h resistance AND (1h EMA20 < EMA50 OR 15m upper wick >= 25%)",
        "action": "HARD_VETO_LONG",
        "rationale": "Buying resistance in downtrend = #1 cause of catastrophic losses. Smart money sells into FOMO.",
        "sample_events": "XRP @ 1.554 before -3.48% waterfall. AVAX @ 11.41 before -4.41% plunge.",
        "is_active": True
    },
    {
        "rule_id": "ANTI_02_NO_SELL_AT_MAJOR_SUPPORT",
        "title": "Do NOT Short Oversold Support Floors",
        "direction": "SELL",
        "condition_trigger": "Price within 0.85% of 24h support AND 15m lower wick >= 25% AND RSI_15m <= 36",
        "action": "HARD_VETO_SHORT",
        "rationale": "Shorting multi-touch support when RSI depleted = guaranteed mean-reversion squeeze.",
        "sample_events": "Selling bottoms right before +4% relief bounces.",
        "is_active": True
    },
    {
        "rule_id": "ANTI_03_NO_COUNTER_BTC_FLUSH",
        "title": "Never Go Long When Bitcoin Is Flushing",
        "direction": "BUY",
        "condition_trigger": "BTC 5m change < -0.15% OR BTC 1h change < -0.60%",
        "action": "HARD_VETO_LONG",
        "rationale": "Altcoins have 1.5x-3.0x beta to BTC. BTC flush = altcoin margin cascade.",
        "sample_events": "Altcoin longs stopped out while Bitcoin flashed red.",
        "is_active": True
    },
    {
        "rule_id": "ANTI_04_RUNNER_PRESERVATION",
        "title": "Do NOT Exit Super-Trade Setups for Micro-Scalp Pennies",
        "direction": "BOTH",
        "condition_trigger": "Setup classified HTF_SWING or SUPER_RUNNER with vol_expansion >= 1.5x",
        "action": "ENFORCE_RUNNER_TRAIL",
        "rationale": "Closing runner at +0.2% destroys expectancy. Use structural trailing stops.",
        "sample_events": "AVAX closed for +$0.01 profit before -$0.49 dump.",
        "is_active": True
    },
    {
        "rule_id": "ANTI_05_RETEST_CONFIRMATION",
        "title": "Require Breakdown Retest Before Entering Short Runners",
        "direction": "SELL",
        "condition_trigger": "Short runner: confirmed 15m close below breakdown + volume >= 1.3x baseline",
        "action": "REQUIRE_CONFIRMATION",
        "rationale": "Premature shorts before level breaks risk fakeout traps.",
        "sample_events": "XRP 1.5588 retest + upper wick rejection into 1.5045 drop.",
        "is_active": True
    }
]

def save_anti_rules():
    now_ts = int(time.time() * 1000)
    try:
        conn = sqlite3.connect(DB_PATH)
        with conn:
            cur = conn.cursor()
            for r in ANTI_PATTERN_RULES:
                cur.execute("""
                    INSERT OR REPLACE INTO anti_pattern_rules
                    (rule_id, title, direction, condition_trigger, action, rationale, sample_events, is_active, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (r['rule_id'], r['title'], r['direction'], r['condition_trigger'],
                      r['action'], r['rationale'], r['sample_events'], 1, now_ts))
        conn.close()
    except Exception as e:
        log(f"Anti-rule save error: {e}")


# ── Main Archaeologist ─────────────────────────────────────────────────────

class HistoricalPatternArchaeologist:
    def __init__(self):
        init_db()
        save_anti_rules()
        log("DIV-13 initialized. Incremental architecture ready.")

    def scan_symbol_incremental(self, symbol):
        """
        First run  → fetches 6 months of history.
        Every 30m  → fetches only new candles since last scan.
        APPENDS to DB — never deletes anything.
        """
        now_ms   = int(time.time() * 1000)
        last_ts  = get_last_scanned_ts(symbol)
        is_first = last_ts is None

        if is_first:
            from_ts = now_ms - SIX_MONTHS_MS
            log(f"  [{symbol}] FIRST RUN — deep scanning 6 months...")
        else:
            from_ts = last_ts - (2 * 60 * 60 * 1000)  # 2h overlap buffer
            hrs_back = round((now_ms - from_ts) / 3600000, 1)
            log(f"  [{symbol}] Incremental — last {hrs_back}h")

        candles = fetch_full_history(symbol, '60', from_ts, now_ms)
        if not candles:
            log(f"  [{symbol}] No candles returned")
            return 0

        log(f"  [{symbol}] {len(candles)} candles fetched — scanning...")
        moves = detect_super_moves(symbol, candles)

        # On incremental: only process moves from new candles
        if not is_first and last_ts:
            moves = [m for m in moves if m.get('candle_ts_ms', 0) > last_ts - (12 * 3600 * 1000)]

        saved = 0
        for move in moves:
            if save_super_trade(move):
                saved += 1
                # Also log as a missed trade for review
                log_missed_trade(
                    symbol, move['direction'], move['start_price'], move['extreme_price'],
                    move['move_pct'], move.get('candle_ts_ms', 0),
                    f"DIV-13 discovery: {move['setup_name']}"
                )

        newest_ts = max(c['ts'] for c in candles) if candles else now_ms
        update_scan_progress(symbol, newest_ts, saved)

        if moves:
            top = sorted(moves, key=lambda x: abs(x['move_pct']), reverse=True)[0]
            log(f"  [{symbol}] +{saved} saved | Best: {top['direction']} "
                f"{top['move_pct']:+.2f}% ({top['roe_10x_pct']}% ROE) @ {top['start_time']}")
        else:
            log(f"  [{symbol}] No qualifying moves in this window")

        return saved

    def run_archaeology_cycle(self):
        log("=" * 70)
        log("  DIV-13 ARCHAEOLOGY CYCLE — INCREMENTAL HISTORY LEARNING")
        log(f"  {len(ARCHAEOLOGY_UNIVERSE)} coins | DB grows with every cycle")
        log("=" * 70)

        total_new = 0
        for sym in ARCHAEOLOGY_UNIVERSE:
            try:
                total_new += self.scan_symbol_incremental(sym)
                time.sleep(0.5)
            except Exception as e:
                log(f"  [{sym}] Error: {e}")

        # Summary from DB
        try:
            conn = sqlite3.connect(DB_PATH)
            cur  = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM historical_super_trades")
            total_db = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM missed_trade_journal")
            total_missed = cur.fetchone()[0]
            cur.execute("""
                SELECT symbol, direction, price_change_pct, roe_10x_pct, start_time
                FROM historical_super_trades
                ORDER BY ABS(price_change_pct) DESC LIMIT 5
            """)
            top5 = cur.fetchall()
            conn.close()
            log("=" * 70)
            log(f"  DONE — +{total_new} new | DB total: {total_db} super-trades | {total_missed} missed-trades logged")
            log(f"  TOP 5 ALL-TIME MOVES IN DB:")
            for r in top5:
                log(f"    {r[0]} {r[1]} {r[2]:+.2f}% ({r[3]}% ROE) @ {r[4]}")
            log("=" * 70)
        except Exception as e:
            log(f"Stats error: {e}")

        self._save_summary_json()

    def _save_summary_json(self):
        try:
            conn = sqlite3.connect(DB_PATH)
            cur  = conn.cursor()
            cur.execute("""
                SELECT symbol, direction, setup_name, price_change_pct, roe_10x_pct,
                       start_time, start_price, extreme_price, vol_expansion, pre_move_rsi
                FROM historical_super_trades
                ORDER BY ABS(price_change_pct) DESC LIMIT 50
            """)
            rows = cur.fetchall()
            cur.execute("SELECT COUNT(*) FROM historical_super_trades")
            total = cur.fetchone()[0]
            cur.execute("SELECT symbol, last_scanned_ts, total_found FROM scan_progress")
            progress = {r[0]: {"last_ts": r[1], "found": r[2]} for r in cur.fetchall()}
            conn.close()

            payload = {
                "last_updated": datetime.now(SL_TZ).strftime("%Y-%m-%d %H:%M:%S SLST"),
                "total_super_trades_in_db": total,
                "scan_progress": progress,
                "anti_rules": ANTI_PATTERN_RULES,
                "super_trades": [
                    {"symbol": r[0], "direction": r[1], "setup_name": r[2],
                     "move_pct": r[3], "roe_10x_pct": r[4], "start_time": r[5],
                     "start_price": r[6], "extreme_price": r[7],
                     "vol_expansion": r[8], "pre_move_rsi": r[9]}
                    for r in rows
                ]
            }
            with open(SUPER_TRADES_FILE, 'w', encoding='utf-8') as f:
                json.dump(payload, f, indent=2)
            try:
                from backend_lib.supabase_client import supabase_kv_set
                supabase_kv_set("historical_super_trades", payload)
            except Exception:
                pass
        except Exception as e:
            log(f"JSON save error: {e}")

    def check_anti_pattern_veto(self, candidate, btc_macro=None, sr_levels=None):
        direction = candidate.get('direction', 'BUY')
        sym       = candidate.get('symbol', '')
        u_wick    = candidate.get('upper_wick_pct', 0)
        l_wick    = candidate.get('lower_wick_pct', 0)
        trend_1h  = candidate.get('trend_1h', 'NEUTRAL')

        if btc_macro and direction == 'BUY':
            btc_chg = btc_macro.get('btc_chg_5m', 0)
            if btc_chg < -0.15 or not btc_macro.get('alt_long_allowed', True):
                return True, "ANTI_03_NO_COUNTER_BTC_FLUSH", f"BTC flushing ({btc_chg:+.2f}% 5m)."

        if direction == 'BUY' and sr_levels:
            runway = sr_levels.get('runway_to_res_pct', 99)
            if runway <= 0.85 and (trend_1h == 'BEAR' or u_wick >= 25.0):
                return True, "ANTI_01_NO_BUY_AT_RESISTANCE", f"{sym}: {runway:.2f}% to resistance, {u_wick:.0f}% wick."

        if direction == 'SELL' and sr_levels:
            dist = sr_levels.get('dist_to_sup_pct', 99)
            if dist <= 0.85 and (trend_1h == 'BULL' or l_wick >= 25.0):
                return True, "ANTI_02_NO_SELL_AT_MAJOR_SUPPORT", f"{sym}: {dist:.2f}% to support, {l_wick:.0f}% wick."

        return False, None, "Clean — passes all anti-pattern rules."


# ── Singleton & Daemon ─────────────────────────────────────────────────────

archaeologist = HistoricalPatternArchaeologist()

def run_archaeologist_daemon():
    """
    First cycle: full 6-month deep scan per symbol (takes a few minutes).
    Every subsequent cycle (30 min): incremental new-data-only scan.
    DB grows permanently — never wiped.
    """
    log("Starting DIV-13 Daemon — FIRST CYCLE = 6-month deep scan")
    while True:
        try:
            archaeologist.run_archaeology_cycle()
        except Exception as e:
            log(f"Daemon cycle exception: {e}")
        log("Next incremental scan in 30 minutes...")
        time.sleep(1800)

if __name__ == "__main__":
    run_archaeologist_daemon()
