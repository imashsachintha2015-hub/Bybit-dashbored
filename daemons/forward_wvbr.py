#!/usr/bin/env python3
"""Blind PAPER forward test (no orders, ever) of two research candidates, with the rules FROZEN -- do not edit the constants while a test window is running.

Track A  WVBR  Weekly Value Breakout Retest (scratch/vp_wide.py variant week|vp|accT|n4|s1.0|t4.0, research notes: memory wvbr-weekly-value-breakout-retest)
    range     the last completed calendar week (Mon 00:00 - Sun 23:45 UTC), fixed-range volume profile, 24 rows, 70% value area -> VAH / VAL, width w
    trigger   during the next 7 days: 4 consecutive 15m closes above VAH (below VAL)
    entry     LIMIT buy at VAH (sell at VAL), valid 96 candles (24 h); fills only when price trades THROUGH it (gap through = fill at the open)
    stop      stop-market at entry -/+ max(1.0 w, 1% of price)
    target    limit at entry +/- max(4.0 w, 1.5% of price)
    time exit close at market 1344 candles (14 days) after the fill
    fees      maker 2 bps entry, maker 2 bps target, taker 8 bps stop / time exit (R after fees); one position per coin
    BTC gate  long only if BTC's last closed 4h close is above its 4h EMA-200 (span 200), short only if below.
    Every setup is paper-traded and tagged gate_ok, so ungated vs gated can be compared on the same trades (WVBR_all vs WVBR_gate).
Track B  SBGZ + BTC gate (separate log, separate statistics): every strong-break setup the SBGZ radar shows is logged once, before its outcome, with the BTC
    regime at that moment. Gate: LONG if BTC 4h close is >= +3% above its 4h EMA-200; SHORT if between -7% and +10% of it. Outcomes are logged hourly as sbgz_outcome
    (WIN / LOSS with R, NO FILL) from the SBGZ engine's own result, keyed like the setup.
Data: Bybit public klines (the research used Binance). Files (FORWARD_TEST_DIR, else /data/forward_test when /data exists, else scratch/forward_test): wvbr_log.jsonl, wvbr_state.json, sbgz_gate_log.jsonl.

Run:   python daemons/forward_wvbr.py            (every 15 minutes)      --once (one pass)      --report
"""
import json, os, sys, time, urllib.parse, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, ROOT)
OUT = os.environ.get("FORWARD_TEST_DIR") or ("/data/forward_test" if os.path.isdir("/data") else os.path.join(ROOT, "scratch", "forward_test"))   # Railway volume /data keeps the ledger
os.makedirs(OUT, exist_ok=True)
LOG_P, STATE_P, SBGZ_LOG_P = (os.path.join(OUT, n) for n in ("wvbr_log.jsonl", "wvbr_state.json", "sbgz_gate_log.jsonl"))
VERSION = "WVBR-v1"
M15, H4, DAY, WEEK = 900_000, 14_400_000, 86_400_000, 7 * 86_400_000
ROWS, VA_FRAC, N_ACC, ORDER_BARS, HOLD_BARS = 24, 0.70, 4, 96, 1344
STOP_W, TGT_W, MIN_STOP, MIN_TGT = 1.0, 4.0, 0.01, 0.015
FEE_IN, FEE_TP, FEE_STOP = 0.0002, 0.0002, 0.0008
EXP_R = {"gate": 0.23, "all": 0.13}              # research averages (aligned / all trades), only shown in the log
PUBLIC = "https://api.bybit.com"


# ---------------- pure logic (also used by tests/test_wvbr.py) ----------------
def profile_va(bars, rows=ROWS, va_frac=VA_FRAC):
    """VAH, VAL of the candles (dicts with h, l, v): same maths as scratch/frvp.build."""
    lo = min(b["l"] for b in bars); hi = max(b["h"] for b in bars)
    if hi <= lo: hi = lo * (1 + 1e-9) if lo else 1e-9
    step = (hi - lo) / rows; edges = [lo + step * i for i in range(rows + 1)]; vol = [0.0] * rows
    for b in bars:
        ov = [max(0.0, min(b["h"], edges[i + 1]) - max(b["l"], edges[i])) for i in range(rows)]; tot = sum(ov)
        if tot > 0:
            for i in range(rows): vol[i] += b["v"] * ov[i] / tot
        else: vol[min(max(int((b["h"] - lo) / step), 0), rows - 1)] += b["v"]
    total = sum(vol); mx = max(vol); cand = [i for i, x in enumerate(vol) if x >= mx * (1 - 1e-12)]; poc = min(cand, key=lambda i: abs(i - (rows - 1) / 2))
    up = dn = poc; acc = vol[poc]
    while acc < va_frac * total - 1e-12:
        a = sum(vol[up + 1:up + 3]); b_ = sum(vol[max(dn - 2, 0):dn])
        if up + 1 >= rows and dn <= 0: break
        if (a >= b_ and up + 1 < rows) or dn <= 0: acc += a; up = min(up + 2, rows - 1)
        else: acc += b_; dn = max(dn - 2, 0)
    return edges[up + 1], edges[dn]


def week_start(t): return ((t // DAY + 3) // 7 * 7 - 3) * DAY


def make_order(side, vah, val, arm_t):
    w = vah - val; E = vah if side == 1 else val
    S = E - side * max(STOP_W * w, MIN_STOP * E); T = E + side * max(TGT_W * w, MIN_TGT * E)
    return dict(side=side, E=E, S=S, T=T, w=w, arm_t=arm_t, expire_t=arm_t + ORDER_BARS * M15)


def r_after_fees(side, fp, xp, S, kind):
    risk = side * (fp - S); return side * (xp - fp) / risk - (FEE_IN + (FEE_TP if kind == "target" else FEE_STOP)) * fp / risk


def advance(cs, bar, emit):
    """Feed ONE closed candle {t,o,h,l,c} to a coin state (cs: levels, run, pending, pos). emit(event) logs. Order = engine order: fills / exits first, then triggers."""
    t, o, h, l, c = bar["t"], bar["o"], bar["h"], bar["l"], bar["c"]
    pos = cs.get("pos")
    if pos:                                                       # exits: stop first, target from the bar after the fill, time exit at the close
        pos["bars"] += 1; side = pos["side"]; risk = side * (pos["fp"] - pos["S"])
        fav = (h - pos["fp"]) if side == 1 else (pos["fp"] - l); adv = (pos["fp"] - l) if side == 1 else (h - pos["fp"])
        hit_s = l <= pos["S"] if side == 1 else h >= pos["S"]; hit_t = h >= pos["T"] if side == 1 else l <= pos["T"]
        out = None
        if hit_s: out = (min(o, pos["S"]) if side == 1 else max(o, pos["S"]), "stop")
        elif hit_t: out = (max(o, pos["T"]) if side == 1 else min(o, pos["T"]), "target")
        elif pos["bars"] >= HOLD_BARS: out = (c, "time")
        if not hit_s: pos["mfe"] = max(pos["mfe"], fav / risk)
        pos["mae"] = max(pos["mae"], adv / risk if not hit_s else 1.0)
        if out:
            R = r_after_fees(side, pos["fp"], out[0], pos["S"], out[1])
            emit({"type": "exit", "symbol": pos["symbol"], "side": side, "gate_ok": pos["gate_ok"], "entry": pos["fp"], "exit": out[0], "how": out[1], "R": round(R, 4),
                  "ret_pct": round(R * risk / pos["fp"] * 100, 3), "mfe_R": round(pos["mfe"], 3), "mae_R": round(min(pos["mae"], 1.0), 3), "bars_held": pos["bars"], "fill_t": pos["fill_t"], "exit_t": t})
            cs["pos"] = None
    keep = []
    for od in cs.get("pending", []):                              # fills: only candles after the arm candle, only while the order lives
        if t <= od["arm_t"]: keep.append(od); continue
        if t > od["expire_t"]: emit({"type": "expired", "symbol": cs["symbol"], "side": od["side"], "arm_t": od["arm_t"]}); continue
        side = od["side"]; through = l < od["E"] if side == 1 else h > od["E"]
        if not through: keep.append(od); continue
        if cs.get("pos"): emit({"type": "skipped_busy", "symbol": cs["symbol"], "side": side, "arm_t": od["arm_t"]}); continue
        fp = min(o, od["E"]) if side == 1 else max(o, od["E"])
        if side * (fp - od["S"]) <= 0: continue
        cs["pos"] = dict(od, symbol=cs["symbol"], fp=fp, fill_t=t, bars=0, mfe=0.0, mae=0.0)
        emit({"type": "fill", "symbol": cs["symbol"], "side": side, "gate_ok": od["gate_ok"], "limit": od["E"], "fill": fp, "stop": od["S"], "target": od["T"], "arm_t": od["arm_t"], "fill_t": t})
        if (l <= od["S"] if side == 1 else h >= od["S"]):          # stop on the fill candle itself
            R = r_after_fees(side, fp, od["S"], od["S"], "stop")
            emit({"type": "exit", "symbol": cs["symbol"], "side": side, "gate_ok": od["gate_ok"], "entry": fp, "exit": od["S"], "how": "stop", "R": round(R, 4),
                  "ret_pct": round(R * (side * (fp - od["S"])) / fp * 100, 3), "mfe_R": 0.0, "mae_R": 1.0, "bars_held": 0, "fill_t": t, "exit_t": t}); cs["pos"] = None
    cs["pending"] = keep
    lv = cs.get("levels")                                         # triggers: counted from the first candle of the week, levels of the previous week
    if lv and t >= lv["ws"] and t < lv["ws"] + WEEK:
        cs["up"] = cs.get("up", 0) + 1 if c > lv["vah"] else 0; cs["dn"] = cs.get("dn", 0) + 1 if c < lv["val"] else 0
        for side, n in ((1, cs["up"]), (-1, cs["dn"])):
            if n == N_ACC:
                od = make_order(side, lv["vah"], lv["val"], t); od["gate_ok"] = None; od["new"] = True; cs["pending"].append(od)
                emit({"type": "_armed", "order": od})


# ---------------- live plumbing ----------------
def get(path, **q):
    url = PUBLIC + path + "?" + urllib.parse.urlencode(q)
    for k in range(4):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "MASIS-wvbr"}), timeout=20) as r: return json.loads(r.read().decode())
        except Exception: time.sleep(1 + k)
    return {}


_off = {"ms": 0, "at": 0}


def server_now():
    if time.time() - _off["at"] > 600:
        try: _off["ms"] = int(get("/v5/market/time")["result"]["timeNano"]) // 1_000_000 - int(time.time() * 1000); _off["at"] = time.time()
        except Exception: pass
    return int(time.time() * 1000) + _off["ms"]


def klines(sym, interval, ms, now, start=None, end=None, limit=1000):
    q = dict(category="linear", symbol=sym, interval=interval, limit=limit)
    if start is not None: q["start"] = int(start)
    if end is not None: q["end"] = int(end)
    rows = get("/v5/market/kline", **q).get("result", {}).get("list", [])
    b = [{"t": int(r[0]), "o": float(r[1]), "h": float(r[2]), "l": float(r[3]), "c": float(r[4]), "v": float(r[5])} for r in rows]
    return sorted([x for x in b if x["t"] + ms <= now], key=lambda x: x["t"])


def btc_regime(now):
    """(distance of BTC's last closed 4h close from its 4h EMA-200, in %), None if unavailable."""
    b = klines("BTCUSDT", "240", H4, now, limit=1000)
    if len(b) < 250: return None
    k = 2 / 201; e = b[0]["c"]
    for x in b[1:]: e = x["c"] * k + e * (1 - k)
    return (b[-1]["c"] - e) / e * 100


def log(ev, path=None):
    ev = {"ts": server_now(), "version": VERSION, **ev}
    with open(path or LOG_P, "a", encoding="utf-8") as f: f.write(json.dumps(ev) + "\n")
    print(json.dumps(ev), flush=True)


def load():
    try: return json.load(open(STATE_P))
    except Exception: return {"start_t": None, "coins": {}}


def coins():
    try:
        from backend_lib import sbgz; return list(sbgz.RADAR_COINS)
    except Exception: return ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT", "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "LINKUSDT"]


def run_coin(st, sym, now, dist):
    cs = st["coins"].setdefault(sym, {"symbol": sym, "last_t": 0, "pending": [], "pos": None})
    ws_now = week_start(now)
    b = klines(sym, "15", M15, now, start=ws_now - 0, limit=1000) if True else []
    if not b: return
    lv = cs.get("levels")
    if not lv or lv["ws"] != ws_now:                                  # previous completed week -> levels (once per week and coin)
        pw = klines(sym, "15", M15, now, start=ws_now - WEEK, end=ws_now - 1, limit=1000)
        if len(pw) < 600: log({"type": "error", "symbol": sym, "msg": f"previous week has {len(pw)} candles"}); return
        vah, val = profile_va(pw); cs["levels"] = {"ws": ws_now, "vah": vah, "val": val}; cs["up"] = cs["dn"] = 0; cs["last_t"] = 0
        log({"type": "levels", "symbol": sym, "week_start": ws_now, "vah": vah, "val": val, "width_pct": round((vah - val) / vah * 100, 2)})
    start_t = st["start_t"]
    tk = None

    def emit(ev):
        nonlocal tk
        if ev["type"] == "_armed":
            od = ev["order"]
            if od["arm_t"] + M15 <= start_t: od["gate_ok"] = False; od["ignored"] = True; return          # armed before the test started: never traded
            gate = dist is not None and (od["side"] * dist > 0); od["gate_ok"] = gate
            if tk is None: tk = (get("/v5/market/tickers", category="linear", symbol=sym).get("result", {}).get("list") or [{}])[0]
            try: spread = (float(tk["ask1Price"]) - float(tk["bid1Price"])) / float(tk["bid1Price"]) * 100
            except Exception: spread = None
            log({"type": "signal", "symbol": sym, "side": od["side"], "direction": "LONG" if od["side"] == 1 else "SHORT", "entry": od["E"], "stop": od["S"], "target": od["T"],
                 "valid_until": od["expire_t"], "arm_t": od["arm_t"], "btc_dist_pct": None if dist is None else round(dist, 2), "btc_regime": "ALIGNED" if gate else "NOT ALIGNED",
                 "source": "WVBR weekly VAH/VAL", "spread_pct": None if spread is None else round(spread, 4), "expected_R": EXP_R["gate" if gate else "all"],
                 "decision": "TRADE" if gate else "NO TRADE (paper-traded for the ungated comparison)"})
        else: log(ev)

    for bar in b:
        if bar["t"] <= cs["last_t"] or bar["t"] < ws_now: continue
        advance(cs, bar, emit); cs["last_t"] = bar["t"]
        cs["pending"] = [o for o in cs["pending"] if not o.get("ignored")]


def sbgz_gate_log(st, now, dist):
    try:
        from backend_lib import sbgz; rows = sbgz.radar().get("rows", [])
    except Exception as e: log({"type": "error", "msg": "sbgz radar: " + str(e)[:120]}, SBGZ_LOG_P); return
    seen = st.setdefault("sbgz_seen", {})
    for r in rows:
        if r.get("kind") != "setup": continue
        key = f"{r['symbol']}|{r['interval']}|{r['side']}|{r.get('since')}"
        if key in seen: continue
        seen[key] = dict(symbol=r['symbol'], interval=r['interval'], side=r['side'], since=r.get('since'), first_seen=now, done=False)
        gate = None if dist is None else (dist >= 3 if r["side"] == "LONG" else -7 <= dist <= 10)
        log({"type": "sbgz_setup", "key": key, "symbol": r["symbol"], "interval": r["interval"], "direction": r["side"], "entry": r["entry"], "stop": r["stop"], "target": r["target"],
             "vol_ok": r.get("vol_ok"), "since": r.get("since"), "btc_dist_pct": None if dist is None else round(dist, 2), "sbgz_gate_ok": gate,
             "decision": "TRADE" if gate else "NO TRADE (logged for the comparison)"}, SBGZ_LOG_P)


def sbgz_outcomes(st):
    """Hourly: for every logged SBGZ setup without an outcome, look at the SBGZ engine's own result and log the outcome once (filled -> WIN/LOSS with R, or no fill)."""
    try: from backend_lib import sbgz
    except Exception: return
    for key, rec in list(st.get("sbgz_seen", {}).items()):
        if not isinstance(rec, dict) or rec.get("done") or rec.get("since") is None: continue
        try:
            r = sbgz.get(rec["symbol"], rec["interval"], 1000)
            if not r.get("ok"): continue
            times = r.get("times") or []; idx = {tt: i for i, tt in enumerate(times)}; arm = idx.get(rec["since"])
            if arm is None:
                if times and rec["since"] < times[0]: rec["done"] = True; log({"type": "sbgz_outcome", "key": key, "result": "unknown (scrolled out of the 1000-candle window)"}, SBGZ_LOG_P)
                continue
            tr = next((t for t in (r.get("trades") or []) + (r.get("open_trades") or []) if t.get("since_bar") == arm and t["side"] == rec["side"]), None)
            if tr and "result" in tr:
                rec["done"] = True; log({"type": "sbgz_outcome", "key": key, "result": "WIN" if tr["result"] == "TP" else "LOSS", "R": tr.get("R"), "fill_t": times[tr["fill_bar"]] if tr["fill_bar"] < len(times) else None,
                                          "exit_t": times[tr["exit_bar"]] if tr.get("exit_bar") is not None and tr["exit_bar"] < len(times) else None}, SBGZ_LOG_P)
            elif not tr and any(m.get("arm_bar") == arm and m["side"] == rec["side"] for m in (r.get("missed") or [])):
                rec["done"] = True; log({"type": "sbgz_outcome", "key": key, "result": "NO FILL"}, SBGZ_LOG_P)
        except Exception as e: log({"type": "error", "msg": "sbgz outcome: " + str(e)[:120]}, SBGZ_LOG_P)


def cycle(st):
    now = server_now()
    if st["start_t"] is None: st["start_t"] = (now // M15) * M15; log({"type": "start", "start_t": st["start_t"]})
    dist = btc_regime(now)
    for sym in coins():
        try: run_coin(st, sym, now, dist)
        except Exception as e: log({"type": "error", "symbol": sym, "what": type(e).__name__, "msg": str(e)[:160]})
    sbgz_gate_log(st, now, dist)
    if now - st.get("last_hour", 0) >= 3_600_000:                  # hourly proof of life + SBGZ outcomes
        st["last_hour"] = now; sbgz_outcomes(st)
        cs = st["coins"].values(); log({"type": "heartbeat", "btc_dist_pct": None if dist is None else round(dist, 2), "coins": len(st["coins"]), "pending": sum(len(c.get("pending", [])) for c in cs),
                                        "open": sum(1 for c in st["coins"].values() if c.get("pos")), "sbgz_logged": len(st.get("sbgz_seen", {}))})


def report():
    ev = [json.loads(l) for l in open(LOG_P)] if os.path.exists(LOG_P) else []
    sig = [e for e in ev if e["type"] == "signal"]; ex = [e for e in ev if e["type"] == "exit"]
    print(f"{VERSION}: {len(sig)} signals, {sum(e['type']=='fill' for e in ev)} fills, {len(ex)} closed, {sum(e['type']=='skipped_busy' for e in ev)} skipped (position open)")
    for nm, sel in (("WVBR_all (no gate)", ex), ("WVBR_gate (aligned)", [e for e in ex if e["gate_ok"]]), ("against the BTC gate", [e for e in ex if not e["gate_ok"]])):
        if not sel: print(f"   {nm:22s} no closed trades yet"); continue
        R = [e["R"] for e in sel]; rp = [e["ret_pct"] for e in sel]
        print(f"   {nm:22s} n={len(sel):3d} avg {sum(R)/len(R):+.3f}R  price {sum(rp)/len(rp):+.2f}%/trade  win {sum(r>0 for r in R)/len(R):.0%}  total {sum(R):+.1f}R  avg MFE {sum(e['mfe_R'] for e in sel)/len(sel):.2f}R MAE {sum(e['mae_R'] for e in sel)/len(sel):.2f}R"
              f"   (research: gate +0.23R, all +0.13R)")
    sb = [json.loads(l) for l in open(SBGZ_LOG_P)] if os.path.exists(SBGZ_LOG_P) else []
    s = [e for e in sb if e["type"] == "sbgz_setup"]; print(f"SBGZ gate log: {len(s)} setups logged, {sum(1 for e in s if e['sbgz_gate_ok'])} pass the BTC gate (outcomes are logged as sbgz_outcome)")


def summary():
    """the report as data (and text) for the dashboard server: counts, the three groups' statistics, and the ledger's last write"""
    import io, contextlib
    ev = [json.loads(l) for l in open(LOG_P)] if os.path.exists(LOG_P) else []
    sig = [e for e in ev if e["type"] == "signal"]; ex = [e for e in ev if e["type"] == "exit"]
    groups = {}
    for nm, sel in (("all", ex), ("gate_aligned", [e for e in ex if e["gate_ok"]]), ("against_gate", [e for e in ex if not e["gate_ok"]])):
        R = [e["R"] for e in sel]
        groups[nm] = dict(n=len(R), avg_r=(sum(R) / len(R)) if R else None, total_r=sum(R) if R else 0.0, win_rate=(sum(r > 0 for r in R) / len(R) * 100) if R else None)
    sb = [json.loads(l) for l in open(SBGZ_LOG_P)] if os.path.exists(SBGZ_LOG_P) else []
    setups = [e for e in sb if e["type"] == "sbgz_setup"]
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf): report()
    return dict(version=VERSION, ledger_dir=OUT, ledger_exists=os.path.exists(LOG_P), signals=len(sig), fills=sum(e["type"] == "fill" for e in ev), closed=len(ex),
                skipped_busy=sum(e["type"] == "skipped_busy" for e in ev), groups=groups, research=dict(gate_avg_r=0.23, all_avg_r=0.13),
                sbgz_gate=dict(setups=len(setups), pass_gate=sum(1 for e in setups if e.get("sbgz_gate_ok"))),
                last_event_t=(max((e.get("t") or 0) for e in ev) if ev else None), errors=sum(e["type"] == "error" for e in ev), text=buf.getvalue())


def main():
    if "--report" in sys.argv: return report()
    st = load()
    while True:
        try: cycle(st); json.dump(st, open(STATE_P, "w"))
        except Exception as e: log({"type": "error", "what": type(e).__name__, "msg": str(e)[:200]})
        if "--once" in sys.argv: return
        nxt = (server_now() // M15 + 1) * M15 + 60_000; time.sleep(max(30, (nxt - server_now()) / 1000))


if __name__ == "__main__":
    main()
