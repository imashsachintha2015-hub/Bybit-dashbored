"""
Order Failure Intelligence Journal — MASIS V3

Every time an order is rejected by the broker, the failure is recorded here
with full context: broker error code, human category, market snapshot
(BTC regime, RSI, volume ratio, price), and attempted order parameters.

Before placing the NEXT order on the same symbol or in the same market
conditions, the executor calls get_failure_risk() which scans the failure
history and returns a structured risk report.
"""

import os
import json
import sqlite3
import time
from datetime import datetime, timezone, timedelta

DIRECTORY = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH   = os.path.join(DIRECTORY, "market_knowledge.db")
SL_TZ = timezone(timedelta(hours=5, minutes=30))

BYBIT_CODE_CATEGORIES = {
    110007: "INSUFFICIENT_MARGIN", 110008: "INSUFFICIENT_MARGIN", 110009: "INSUFFICIENT_MARGIN",
    110044: "INVALID_QTY", 110045: "INVALID_QTY", 110070: "INVALID_QTY",
    110071: "INVALID_QTY", 110072: "INVALID_QTY",
    110017: "PRICE_ERROR", 110018: "PRICE_ERROR", 110019: "PRICE_ERROR",
    110040: "LEVERAGE_ERROR", 110041: "LEVERAGE_ERROR", 110043: "LEVERAGE_ERROR",
    110014: "POSITION_LIMIT", 110015: "POSITION_LIMIT",
    110013: "REDUCE_ONLY_VIOLATION",
    110025: "POSITION_MODE_ERROR", 110026: "POSITION_MODE_ERROR",
    110001: "ORDER_NOT_FOUND", 110003: "ORDER_INVALID_STATE", 110006: "ORDER_INVALID_STATE",
    10006:  "RATE_LIMIT",
    10003:  "AUTH_ERROR", 10004: "AUTH_ERROR",
    10001:  "PARAM_ERROR", 10002: "PARAM_ERROR",
    -1:     "NETWORK_ERROR",
}

def categorize_failure(ret_code, ret_msg):
    cat = BYBIT_CODE_CATEGORIES.get(int(ret_code) if ret_code is not None else -1)
    if cat:
        return cat
    msg = (ret_msg or "").lower()
    if any(w in msg for w in ("margin", "balance", "equity", "insufficient")):
        return "INSUFFICIENT_MARGIN"
    if any(w in msg for w in ("qty", "quantity", "lot", "size", "step")):
        return "INVALID_QTY"
    if any(w in msg for w in ("price", "tick")):
        return "PRICE_ERROR"
    if "leverage" in msg:
        return "LEVERAGE_ERROR"
    if any(w in msg for w in ("rate limit", "too many")):
        return "RATE_LIMIT"
    if any(w in msg for w in ("auth", "api key", "signature", "sign")):
        return "AUTH_ERROR"
    if any(w in msg for w in ("timeout", "connection", "network", "reset")):
        return "NETWORK_ERROR"
    return "BROKER_REJECTION"

CATEGORY_ADVICE = {
    "INSUFFICIENT_MARGIN":  {"desc": "Not enough free margin",               "preflight": "verify_free_margin",     "next_action": "Check wallet equity before sizing. Reduce notional if equity < ."},
    "INVALID_QTY":          {"desc": "Qty violates lot-size or step",         "preflight": "verify_lot_size",        "next_action": "Recalculate qty with coin min_qty and step size before submitting."},
    "PRICE_ERROR":          {"desc": "Price tick violation or price moved",   "preflight": "verify_price_tick",      "next_action": "Re-fetch live price immediately before placing. Apply tick rounding."},
    "LEVERAGE_ERROR":       {"desc": "Leverage set failed",                   "preflight": "verify_leverage",        "next_action": "Call set_leverage() again just before place_order(). Treat 110043 as success."},
    "POSITION_LIMIT":       {"desc": "At max positions or notional limit",    "preflight": "check_open_positions",   "next_action": "Wait for an existing position to close."},
    "REDUCE_ONLY_VIOLATION":{"desc": "Tried to open in reduce-only mode",     "preflight": "check_position_mode",    "next_action": "Check position mode before sending non-reduceOnly orders."},
    "RATE_LIMIT":           {"desc": "Too many API requests",                 "preflight": None,                     "next_action": "Add 2s back-off. Reduce API call frequency."},
    "NETWORK_ERROR":        {"desc": "Connection timeout or reset",           "preflight": None,                     "next_action": "Retry with back-off. bybit_client already retries 3x."},
    "AUTH_ERROR":           {"desc": "API key invalid or signature mismatch", "preflight": "verify_credentials",     "next_action": "Check BYBIT_API_KEY / BYBIT_API_SECRET. Rotate if compromised."},
    "PARAM_ERROR":          {"desc": "Missing or malformed parameter",        "preflight": "validate_order_params",  "next_action": "Log full order body to inspect malformed fields."},
    "BROKER_REJECTION":     {"desc": "Unclassified broker rejection",         "preflight": None,                     "next_action": "Log full retMsg for manual inspection."},
    "ORDER_NOT_FOUND":      {"desc": "Order ID not found (race condition)",   "preflight": None,                     "next_action": "Check positions before assuming failure."},
    "ORDER_INVALID_STATE":  {"desc": "Order in wrong state",                  "preflight": None,                     "next_action": "Check order state before modifying."},
    "POSITION_MODE_ERROR":  {"desc": "Position mode conflict",                "preflight": "check_position_mode",    "next_action": "Switch position mode and retry."},
}

PATTERN_RISK_WEIGHTS = {
    ("INSUFFICIENT_MARGIN",    2,  1): 30,
    ("INSUFFICIENT_MARGIN",    2,  2): 60,
    ("INSUFFICIENT_MARGIN",    2,  3): 90,
    ("INVALID_QTY",            4,  2): 40,
    ("INVALID_QTY",            4,  3): 70,
    ("PRICE_ERROR",            1,  2): 35,
    ("LEVERAGE_ERROR",         4,  2): 50,
    ("RATE_LIMIT",             1,  3): 45,
    ("NETWORK_ERROR",          1,  3): 30,
    ("AUTH_ERROR",            24,  1): 100,
    ("BROKER_REJECTION",       4,  3): 40,
    ("POSITION_LIMIT",         4,  2): 55,
    ("REDUCE_ONLY_VIOLATION",  4,  2): 55,
}

RISK_LEVEL_THRESHOLDS = [
    ("LOW",      0,  30),
    ("MEDIUM",  30,  60),
    ("HIGH",    60,  90),
    ("CRITICAL",90, 999),
]
CONVICTION_PENALTY = {"LOW": 0, "MEDIUM": 5, "HIGH": 10, "CRITICAL": 999}


class OrderFailureJournal:
    def __init__(self, db_path=DB_PATH):
        self.db_path = db_path
        self._init_db()

    def _conn(self):
        conn = sqlite3.connect(self.db_path, timeout=15.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        try:
            with self._conn() as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS order_failures (
                        id                INTEGER PRIMARY KEY AUTOINCREMENT,
                        ts                INTEGER NOT NULL,
                        symbol            TEXT    NOT NULL,
                        direction         TEXT    NOT NULL,
                        setup             TEXT    DEFAULT '',
                        strategy_mode     TEXT    DEFAULT '',
                        score             INTEGER DEFAULT 0,
                        conviction_score  INTEGER DEFAULT 0,
                        failure_code      INTEGER DEFAULT -1,
                        failure_msg       TEXT    DEFAULT '',
                        failure_category  TEXT    NOT NULL,
                        price_attempted   REAL    DEFAULT 0.0,
                        qty_attempted     REAL    DEFAULT 0.0,
                        sl_attempted      REAL    DEFAULT 0.0,
                        tp_attempted      REAL    DEFAULT 0.0,
                        leverage          INTEGER DEFAULT 10,
                        btc_regime        TEXT    DEFAULT '',
                        btc_chg_5m        REAL    DEFAULT 0.0,
                        btc_chg_1h        REAL    DEFAULT 0.0,
                        rsi_5m            REAL    DEFAULT 50.0,
                        vol_ratio         REAL    DEFAULT 1.0,
                        resolved          INTEGER DEFAULT 0,
                        resolved_ts       INTEGER DEFAULT 0,
                        notes             TEXT    DEFAULT ''
                    )
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_of_symbol_ts ON order_failures (symbol, ts)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_of_category_ts ON order_failures (failure_category, ts)")
        except Exception as e:
            print(f"[OrderFailureJournal] DB init error: {e}")

    def record_failure(self, order_info, broker_response, market_snapshot=None):
        ms  = market_snapshot or {}
        bm  = ms.get("btc_macro") or ms
        ret_code = broker_response.get("retCode", -1)
        ret_msg  = broker_response.get("retMsg", "")
        category = categorize_failure(ret_code, ret_msg)
        advice   = CATEGORY_ADVICE.get(category, {})
        ts_str   = datetime.fromtimestamp(int(time.time()), SL_TZ).strftime("%H:%M:%S")
        print(
            f"[FAILURE JOURNAL] {order_info.get('symbol')} {order_info.get('direction')} @ {ts_str} | "
            f"Code {ret_code} ({category}): {ret_msg[:120]} | Advice: {advice.get('next_action','')}"
        )
        try:
            with self._conn() as conn:
                cur = conn.execute(
                    """INSERT INTO order_failures
                       (ts, symbol, direction, setup, strategy_mode, score, conviction_score,
                        failure_code, failure_msg, failure_category,
                        price_attempted, qty_attempted, sl_attempted, tp_attempted, leverage,
                        btc_regime, btc_chg_5m, btc_chg_1h, rsi_5m, vol_ratio)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        int(time.time()),
                        order_info.get("symbol",""), order_info.get("direction",""),
                        order_info.get("setup",""), order_info.get("strategy_mode",""),
                        order_info.get("score",0), order_info.get("conviction_score",0),
                        ret_code, ret_msg[:500], category,
                        order_info.get("price",0.0), order_info.get("qty",0.0),
                        order_info.get("sl",0.0), order_info.get("tp",0.0),
                        order_info.get("leverage",10),
                        bm.get("regime",""), bm.get("btc_chg_5m",0.0), bm.get("btc_chg_1h",0.0),
                        ms.get("rsi_5m",50.0), ms.get("vol_ratio",1.0),
                    )
                )
                return cur.lastrowid
        except Exception as e:
            print(f"[OrderFailureJournal] record_failure error: {e}")
            return -1

    def mark_resolved(self, symbol, direction, notes=""):
        now = int(time.time())
        try:
            with self._conn() as conn:
                conn.execute(
                    "UPDATE order_failures SET resolved=1, resolved_ts=?, notes=? "
                    "WHERE symbol=? AND direction=? AND ts>=? AND resolved=0",
                    (now, notes, symbol, direction, now - 8*3600)
                )
        except Exception as e:
            print(f"[OrderFailureJournal] mark_resolved error: {e}")

    def get_failure_risk(self, symbol, direction, setup, market_snapshot=None):
        ms  = market_snapshot or {}
        bm  = ms.get("btc_macro") or {}
        now = int(time.time())
        patterns, preflights, warnings = [], [], []
        total_risk = 0
        category_breakdown = {}

        try:
            with self._conn() as conn:
                # 1. Per-symbol failures by category in last 8h
                rows = conn.execute(
                    """SELECT failure_category, COUNT(*) as cnt, MAX(ts) as last_ts
                       FROM order_failures
                       WHERE symbol=? AND ts>=? AND resolved=0
                       GROUP BY failure_category ORDER BY cnt DESC""",
                    (symbol, now - 8*3600)
                ).fetchall()

                for row in rows:
                    cat  = row["failure_category"]
                    cnt  = row["cnt"]
                    mins = int((now - row["last_ts"]) / 60)
                    category_breakdown[cat] = {"count": cnt, "last_mins_ago": mins}
                    advice = CATEGORY_ADVICE.get(cat, {})
                    pf     = advice.get("preflight")
                    action = advice.get("next_action", "")
                    desc   = advice.get("desc", cat)

                    for (p_cat, p_win_h, p_min), weight in PATTERN_RISK_WEIGHTS.items():
                        if cat != p_cat:
                            continue
                        cnt_w = conn.execute(
                            "SELECT COUNT(*) FROM order_failures WHERE symbol=? AND failure_category=? AND ts>=? AND resolved=0",
                            (symbol, cat, now - p_win_h*3600)
                        ).fetchone()[0]
                        if cnt_w >= p_min:
                            total_risk = max(total_risk, weight)
                            p_str = f"{cat} x{cnt_w} in last {p_win_h}h (last {mins}m ago)"
                            if p_str not in patterns:
                                patterns.append(p_str)
                                warnings.append(f"[{cat}] {desc}. Seen {cnt_w}x in {p_win_h}h. Action: {action}")
                            if pf and pf not in preflights:
                                preflights.append(pf)

                # 2. Same setup across all symbols in last 6h
                if setup:
                    sf = conn.execute(
                        "SELECT COUNT(*) FROM order_failures WHERE setup=? AND ts>=? AND resolved=0",
                        (setup, now - 6*3600)
                    ).fetchone()[0]
                    if sf >= 3:
                        total_risk = max(total_risk, 40)
                        patterns.append(f"Setup '{setup}' failed {sf}x across coins in last 6h")
                        warnings.append(f"Setup {setup} has {sf} failures in 6h across all coins — market conditions may have shifted.")

                # 3. Direction failures in same BTC regime (last 12h)
                cur_regime = bm.get("regime", "")
                if cur_regime:
                    rf = conn.execute(
                        "SELECT COUNT(*) FROM order_failures WHERE btc_regime=? AND direction=? AND ts>=? AND resolved=0",
                        (cur_regime, direction, now - 12*3600)
                    ).fetchone()[0]
                    if rf >= 4:
                        total_risk = max(total_risk, 35)
                        patterns.append(f"{direction} failed {rf}x during BTC '{cur_regime}' in 12h")
                        warnings.append(f"{direction} orders have been failing in BTC regime '{cur_regime}'. Review macro alignment.")

                # 4. System stress: all failures in last 1h
                sf1h = conn.execute(
                    "SELECT COUNT(*) FROM order_failures WHERE ts>=? AND resolved=0",
                    (now - 3600,)
                ).fetchone()[0]
                if sf1h >= 5:
                    total_risk = max(total_risk, 50)
                    patterns.append(f"System stress: {sf1h} failures across all coins in last 1h")
                    warnings.append(f"System stress: {sf1h} order failures in 1h. Check API health and margin.")

                # 5. Auth errors — always CRITICAL
                ae = conn.execute(
                    "SELECT COUNT(*) FROM order_failures WHERE failure_category='AUTH_ERROR' AND ts>=?",
                    (now - 86400,)
                ).fetchone()[0]
                if ae >= 1:
                    total_risk = 100
                    patterns.append(f"AUTH_ERROR x{ae} in last 24h — credentials may be invalid!")
                    warnings.append("CRITICAL: AUTH_ERROR in failure history. Verify BYBIT_API_KEY / BYBIT_API_SECRET immediately.")
                    if "verify_credentials" not in preflights:
                        preflights.append("verify_credentials")

        except Exception as e:
            print(f"[OrderFailureJournal] get_failure_risk error: {e}")
            return {"has_risk": False, "risk_level": "LOW", "risk_score": 0,
                    "conviction_penalty": 0, "failure_patterns": [], "preflight_checks": [],
                    "warnings": [], "category_breakdown": {}}

        risk_level = "LOW"
        for lvl, lo, hi in RISK_LEVEL_THRESHOLDS:
            if lo <= total_risk < hi:
                risk_level = lvl
                break

        return {
            "has_risk":           total_risk > 0,
            "risk_level":         risk_level,
            "risk_score":         total_risk,
            "conviction_penalty": CONVICTION_PENALTY[risk_level],
            "failure_patterns":   patterns,
            "preflight_checks":   list(set(preflights)),
            "warnings":           warnings,
            "category_breakdown": category_breakdown,
        }

    def get_summary(self, symbol=None, last_n_hours=24):
        now = int(time.time())
        try:
            with self._conn() as conn:
                q = "SELECT * FROM order_failures WHERE ts>=?"
                args = [now - last_n_hours*3600]
                if symbol:
                    q += " AND symbol=?"
                    args.append(symbol)
                rows = conn.execute(q, args).fetchall()
                by_cat = {}
                by_sym = {}
                for r in rows:
                    by_cat[r["failure_category"]] = by_cat.get(r["failure_category"], 0) + 1
                    by_sym[r["symbol"]] = by_sym.get(r["symbol"], 0) + 1
                return {
                    "period_hours": last_n_hours,
                    "total_failures": len(rows),
                    "resolved": sum(1 for r in rows if r["resolved"]),
                    "unresolved": sum(1 for r in rows if not r["resolved"]),
                    "by_category": by_cat,
                    "by_symbol": by_sym,
                }
        except Exception as e:
            return {"error": str(e)}


failure_journal = OrderFailureJournal()
