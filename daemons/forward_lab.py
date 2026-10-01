#!/usr/bin/env python3
"""
FORWARD LAB -- record-only research daemon (no orders, ever).

1. AMD-FVG 1H forward test: every hour, re-scans closed 1H candles on 33 coins with the
   frozen rule in backend_lib/amd_fvg.py and records each setup's paper outcome.
   Only setups whose signal bar closed after the lab's start time are counted.
2. Order-flow recorder: per-minute taker buy/sell volume and liquidations (websocket),
   plus book depth, funding and open interest snapshots (REST) for the engine's symbols.

Results: GET /api/forward-lab on the Railway server (backend_lib/forward_store.report).
"""
import json
import os
import sys
import threading
import time
import urllib.request
from collections import defaultdict

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT_DIR)

from backend_lib import amd_fvg, forward_store  # noqa: E402

MARKET_URL = os.environ.get("MARKET_DATA_URL", "https://api.bybit.com")
WS_URL = os.environ.get("MARKET_WS_URL", "wss://stream.bybit.com/v5/public/linear")
AMD_SYMBOLS = [f"{c}USDT" for c in (
    "BTC ETH SOL XRP DOGE BNB ADA AVAX LINK DOT LTC NEAR APT SUI ARB OP ATOM FIL TRX BCH UNI AAVE "
    "INJ TIA SEI WLD ONDO HBAR ETC ICP STX POL CRV").split()]
FLOW_SYMBOLS = [s.strip() for s in os.environ.get(
    "FLOW_SYMBOLS", "BTCUSDT,ETHUSDT,SOLUSDT,XRPUSDT,LINKUSDT,SEIUSDT,AVAXUSDT,DOGEUSDT,BNBUSDT,"
                    "ADAUSDT,DOTUSDT,POLUSDT,LTCUSDT,NEARUSDT,APTUSDT").split(",") if s.strip()]
KLINES = 400


def log(msg):
    print(f"[FWD {time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime())}] {msg}", flush=True)


def http_json(path, base=None, retries=2):
    url = (base or MARKET_URL) + path
    for i in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "MASIS-ForwardLab/1.0"})
            with urllib.request.urlopen(req, timeout=10) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception:
            if i == retries:
                return {}
            time.sleep(0.5 + i)
    return {}


# ─── 1. AMD-FVG forward test ──────────────────────────────────────────────────
def closed_1h(sym, now_ms):
    raw = http_json(f"/v5/market/kline?category=linear&symbol={sym}&interval=60&limit={KLINES}")
    rows = raw.get("result", {}).get("list", []) or []
    bars = []
    for b in reversed(rows):
        t = int(b[0])
        if t + 3600000 > now_ms:        # drop the still-forming candle
            continue
        bars.append((t, float(b[1]), float(b[2]), float(b[3]), float(b[4]), float(b[5])))
    return bars


def amd_cycle(start_t):
    now_ms = int(time.time() * 1000)
    con = forward_store.connect()
    new = changed = scanned = 0
    try:
        for sym in AMD_SYMBOLS:
            bars = closed_1h(sym, now_ms)
            if len(bars) < 200:
                continue
            scanned += 1
            for side, inv in (("L", False), ("S", True)):
                A = amd_fvg.prep(bars, invert=inv)
                for P in amd_fvg.CONFIGS:
                    for rec in amd_fvg.scan(A, P, side=side, since_t=start_t):
                        existed = con.execute("SELECT 1 FROM amd_trades WHERE key=?",
                                              (f"{rec['config']}|{sym}|{rec['side']}|{rec['signal_t']}",)).fetchone()
                        if forward_store.upsert_amd(con, sym, rec, now_ms):
                            changed += 1
                            if not existed:
                                new += 1
                                log(f"AMD {rec['config']} {sym} {rec['side']} signal@{rec['signal_t']} "
                                    f"entry={rec['entry']:.6g} stop={rec['stop']:.6g} tp={rec['target']:.6g} "
                                    f"status={rec['status']}")
            time.sleep(0.15)
        con.commit()
    finally:
        con.close()
    log(f"AMD scan: {scanned}/{len(AMD_SYMBOLS)} symbols, {new} new setups, {changed} updates")


# ─── 2. Order-flow recorder ───────────────────────────────────────────────────
class FlowBook:
    """Per-minute aggregates keyed by (symbol, minute_start_ms)."""
    def __init__(self):
        self.lock = threading.Lock()
        self.agg = defaultdict(lambda: {"buy_vol": 0.0, "sell_vol": 0.0, "buy_n": 0, "sell_n": 0,
                                        "liq_buy": 0.0, "liq_sell": 0.0})

    def on_message(self, msg):
        topic = msg.get("topic", "")
        data = msg.get("data") or []
        if isinstance(data, dict):
            data = [data]
        with self.lock:
            for d in data:
                try:
                    sym = d.get("s") or topic.split(".")[-1]
                    minute = int(d["T"]) // 60000 * 60000
                    quote = float(d["v"]) * float(d["p"])
                    a = self.agg[(sym, minute)]
                    if topic.startswith("publicTrade"):
                        if d.get("S") == "Buy":
                            a["buy_vol"] += quote; a["buy_n"] += 1
                        else:
                            a["sell_vol"] += quote; a["sell_n"] += 1
                    elif topic.startswith("allLiquidation") or topic.startswith("liquidation"):
                        # S=Buy -> a LONG position was liquidated (forced sell); S=Sell -> short liquidated
                        if d.get("S") == "Buy":
                            a["liq_buy"] += quote
                        else:
                            a["liq_sell"] += quote
                except Exception:
                    continue

    def pop_before(self, minute_ms):
        with self.lock:
            done = {k: v for k, v in self.agg.items() if k[1] < minute_ms}
            for k in done:
                del self.agg[k]
        return done


def ws_thread(book):
    try:
        import websocket  # websocket-client
    except Exception as e:
        log(f"websocket-client unavailable ({e}); taker-flow/liquidation recording disabled")
        return
    args = [f"publicTrade.{s}" for s in FLOW_SYMBOLS] + [f"allLiquidation.{s}" for s in FLOW_SYMBOLS]
    while True:
        try:
            ws = websocket.create_connection(WS_URL, timeout=30)
            for k in range(0, len(args), 10):          # Bybit: max 10 args per subscribe request
                ws.send(json.dumps({"op": "subscribe", "args": args[k:k + 10]}))
            log(f"websocket connected, {len(args)} topics")
            last_ping = time.time()
            while True:
                if time.time() - last_ping > 20:
                    ws.send(json.dumps({"op": "ping"})); last_ping = time.time()
                try:
                    raw = ws.recv()
                except websocket.WebSocketTimeoutException:
                    continue
                if not raw:
                    raise ConnectionError("empty frame")
                msg = json.loads(raw)
                if "topic" in msg:
                    book.on_message(msg)
                elif msg.get("success") is False:
                    log(f"websocket subscribe error: {msg}")
        except Exception as e:
            log(f"websocket reconnect after error: {e}")
            time.sleep(5)


def depth(levels, mid, pct):
    lim = mid * pct
    return sum(float(p) * float(q) for p, q in levels if abs(float(p) - mid) <= lim)


def flow_snapshot(con, minute_ms):
    tk = http_json("/v5/market/tickers?category=linear").get("result", {}).get("list", []) or []
    tick = {x.get("symbol"): x for x in tk}
    for sym in FLOW_SYMBOLS:
        ob = http_json(f"/v5/market/orderbook?category=linear&symbol={sym}&limit=50").get("result", {})
        b, a = ob.get("b") or [], ob.get("a") or []
        x = tick.get(sym, {})
        vals = {}
        if b and a:
            bid1, ask1 = float(b[0][0]), float(a[0][0]); mid = (bid1 + ask1) / 2
            vals.update(bid1=bid1, ask1=ask1, bid_d05=depth(b, mid, 0.005), ask_d05=depth(a, mid, 0.005),
                        bid_d10=depth(b, mid, 0.010), ask_d10=depth(a, mid, 0.010))
        for src, dst in (("fundingRate", "funding"), ("openInterest", "oi"), ("markPrice", "mark")):
            try:
                vals[dst] = float(x[src])
            except Exception:
                pass
        if vals:
            cols = ",".join(vals); ph = ",".join("?" * len(vals))
            upd = ",".join(f"{k}=excluded.{k}" for k in vals)
            con.execute(f"INSERT INTO flow_1m(symbol,minute_t,{cols}) VALUES(?,?,{ph}) "
                        f"ON CONFLICT(symbol,minute_t) DO UPDATE SET {upd}", (sym, minute_ms, *vals.values()))


def flush_trades(con, done):
    for (sym, minute), a in done.items():
        con.execute("""INSERT INTO flow_1m(symbol,minute_t,buy_vol,sell_vol,buy_n,sell_n,liq_buy,liq_sell)
                       VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(symbol,minute_t) DO UPDATE SET
                       buy_vol=excluded.buy_vol, sell_vol=excluded.sell_vol, buy_n=excluded.buy_n,
                       sell_n=excluded.sell_n, liq_buy=excluded.liq_buy, liq_sell=excluded.liq_sell""",
                    (sym, minute, a["buy_vol"], a["sell_vol"], a["buy_n"], a["sell_n"], a["liq_buy"], a["liq_sell"]))


def main():
    con = forward_store.connect()
    start_t = forward_store.meta_get(con, "start_t")
    if start_t is None:
        start_t = int(time.time() * 1000)
        forward_store.meta_set(con, "start_t", start_t); con.commit()
    start_t = int(start_t)
    con.close()
    log(f"record-only forward lab started; counting AMD setups after {start_t}; db={forward_store.DB_PATH}")

    book = FlowBook()
    threading.Thread(target=ws_thread, args=(book,), daemon=True).start()

    last_minute = None; last_amd_hour = None
    while True:
        try:
            now = time.time(); minute_ms = int(now // 60) * 60000
            if minute_ms != last_minute:
                last_minute = minute_ms
                con = forward_store.connect()
                try:
                    flush_trades(con, book.pop_before(minute_ms))
                    flow_snapshot(con, minute_ms)
                    con.commit()
                finally:
                    con.close()
            hour = int(now // 3600)
            if hour != last_amd_hour and (now % 3600) >= 90:     # give the exchange 90s to finalise the candle
                last_amd_hour = hour
                amd_cycle(start_t)
        except Exception as e:
            log(f"cycle error: {e}")
        time.sleep(5)


if __name__ == "__main__":
    main()
