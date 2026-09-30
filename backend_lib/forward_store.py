"""
Storage + scoreboard for the forward lab (record-only research running on Railway).

Everything here is paper: no orders are placed. The AMD-FVG table holds every setup the
frozen 1H rule produced after the lab started; the flow table holds per-minute order-flow
aggregates (taker buy/sell volume, liquidations, book depth, funding, open interest) that
candle data never contained.
"""
import os
import sqlite3
import time

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def data_dir():
    d = os.environ.get("DATA_DIR") or ("/data" if os.path.isdir("/data") else os.path.join(ROOT_DIR, "scratch"))
    os.makedirs(d, exist_ok=True)
    return d


DB_PATH = os.path.join(data_dir(), "forward_lab.db")

# Pass/fail rule declared before any forward data existed (see research_archive/forward_lab/README.md)
CRITERIA = {"min_closed_trades": 60, "min_avg_r": 0.10, "backtest_unseen_avg_r": 0.19}


def connect():
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("""CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT)""")
    con.execute("""CREATE TABLE IF NOT EXISTS amd_trades (
        key TEXT PRIMARY KEY, config TEXT, symbol TEXT, side TEXT, signal_t INTEGER,
        entry REAL, stop REAL, target REAL, risk_pct REAL, status TEXT, fill_t INTEGER,
        exit_t INTEGER, exit_reason TEXT, R REAL, first_seen INTEGER, updated INTEGER)""")
    con.execute("""CREATE TABLE IF NOT EXISTS flow_1m (
        symbol TEXT, minute_t INTEGER, buy_vol REAL, sell_vol REAL, buy_n INTEGER, sell_n INTEGER,
        liq_buy REAL, liq_sell REAL, bid_d05 REAL, ask_d05 REAL, bid_d10 REAL, ask_d10 REAL,
        bid1 REAL, ask1 REAL, funding REAL, oi REAL, mark REAL,
        PRIMARY KEY (symbol, minute_t))""")
    return con


def meta_get(con, k, default=None):
    r = con.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
    return r[0] if r else default


def meta_set(con, k, v):
    con.execute("INSERT INTO meta(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, str(v)))


def upsert_amd(con, symbol, rec, now_ms):
    key = f"{rec['config']}|{symbol}|{rec['side']}|{rec['signal_t']}"
    row = con.execute("SELECT status FROM amd_trades WHERE key=?", (key,)).fetchone()
    if row and row[0] in ("CLOSED", "CANCELLED", "EXPIRED"):
        return False                      # final outcomes are never revised
    con.execute("""INSERT INTO amd_trades(key,config,symbol,side,signal_t,entry,stop,target,risk_pct,status,
                   fill_t,exit_t,exit_reason,R,first_seen,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(key) DO UPDATE SET status=excluded.status, fill_t=excluded.fill_t,
                   exit_t=excluded.exit_t, exit_reason=excluded.exit_reason, R=excluded.R, updated=excluded.updated""",
                (key, rec["config"], symbol, rec["side"], rec["signal_t"], rec["entry"], rec["stop"], rec["target"],
                 rec["risk_pct"], rec["status"], rec["fill_t"], rec["exit_t"], rec["exit_reason"], rec["R"], now_ms, now_ms))
    return True


def _equity(trades, start=10.0, risk=0.02, maxpos=3):
    """$10 account, 2% risk per trade, max 3 concurrent, one per symbol (same rule as the backtests)."""
    eq = start; pk = start; dd = 0.0; openp = []; taken = 0; wins = 0
    for fill_t, sym, R, exit_t in sorted(trades):
        for p in sorted([p for p in openp if p[0] <= fill_t]):
            openp.remove(p); eq += p[1]; pk = max(pk, eq); dd = min(dd, eq / pk - 1)
        if len(openp) >= maxpos or any(p[2] == sym for p in openp):
            continue
        pnl = eq * risk * R
        openp.append((exit_t, pnl, sym)); taken += 1; wins += R > 0
    for p in sorted(openp):
        eq += p[1]; pk = max(pk, eq); dd = min(dd, eq / pk - 1)
    return round(eq, 2), taken, wins, round(dd * 100, 1)


def report():
    con = connect()
    try:
        start = meta_get(con, "start_t")
        out = {"record_only": True, "start_t": int(start) if start else None, "criteria": CRITERIA,
               "generated": int(time.time() * 1000), "amd": {}, "flow": {}}
        for cfg in ("AMD_PRIMARY", "AMD_NO_GATE"):
            rows = con.execute("SELECT symbol,status,R,fill_t,exit_t,side FROM amd_trades WHERE config=?", (cfg,)).fetchall()
            closed = [r for r in rows if r[1] == "CLOSED"]
            Rs = [r[2] for r in closed]
            eq, taken, wins, dd = _equity([(r[3], r[0], r[2], r[4]) for r in closed])
            n = len(Rs); avg = sum(Rs) / n if n else None
            verdict = "COLLECTING"
            if n >= CRITERIA["min_closed_trades"]:
                verdict = "PASS" if avg >= CRITERIA["min_avg_r"] else "FAIL"
            out["amd"][cfg] = {
                "signals": len(rows),
                "pending": sum(1 for r in rows if r[1] == "PENDING"),
                "open": sum(1 for r in rows if r[1] == "OPEN"),
                "cancelled_or_expired": sum(1 for r in rows if r[1] in ("CANCELLED", "EXPIRED")),
                "closed": n,
                "win_rate": round(sum(1 for x in Rs if x > 0) / n * 100, 1) if n else None,
                "avg_r": round(avg, 3) if avg is not None else None,
                "total_r": round(sum(Rs), 2),
                "equity_10usd": eq, "equity_trades_taken": taken, "equity_max_dd_pct": dd,
                "verdict": verdict,
            }
        fr = con.execute("SELECT COUNT(*), COUNT(DISTINCT symbol), MIN(minute_t), MAX(minute_t) FROM flow_1m").fetchone()
        out["flow"] = {"rows": fr[0], "symbols": fr[1], "first_minute": fr[2], "last_minute": fr[3]}
        return out
    finally:
        con.close()
