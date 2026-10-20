"""Setup tracker: paste a list of breakout setups (Coin,Side,Reference USD,Entry,Stop,TP1,TP2,RR TP1,RR TP2,Status) and see where each one stands.

Nothing runs in the background. The pasted list is stored (Supabase app_state, or a file); when the page asks (GET /api/setups) each coin's candles
since the paste are downloaded once and every row is judged from them. Statuses:
  WAITING            the entry has not been touched yet
  ENTERED            the entry was touched, neither TP1 nor the stop yet
  TP1 HIT            TP1 reached (TP2 and the stop not yet)
  TP2 HIT            TP2 reached
  STOPPED            the stop was hit before TP1
  STOPPED AFTER TP1  TP1 was reached, then the stop was hit before TP2
  EXPIRED            never entered within the expiry time
The entries are breakout entries: a long enters when price rises to the entry, a short when it falls to it. A candle that touches both the stop and
a target counts as the stop (the worse case). R is before fees. Two plans are scored side by side: "exit all at TP1" and "exit all at TP2"."""
import csv
import io
import re
import threading
import time

from . import sbgz, sbgz_auto

KEY = "setup_tracker"
EXPIRE_MS = 24 * 3_600_000
MAX_ROWS = 200
_lock = threading.Lock()
_cache = {}                                                # coin -> (fetched ms, interval, candles)
CACHE_MS = 45_000


def _now_ms():
    return int(time.time() * 1000)


def _num(x):
    try:
        v = float(str(x).strip().replace(",", ""))
        return v if v == v and v > 0 else None
    except ValueError:
        return None


HEADERS = {"coin": "coin", "symbol": "coin", "pair": "coin", "side": "side", "direction": "side", "referenceusd": "ref", "reference": "ref", "ref": "ref", "entry": "entry",
           "stop": "stop", "stoploss": "stop", "sl": "stop", "tp1": "tp1", "tp2": "tp2", "rrtp1": "rr1", "rrtp2": "rr2", "rr1": "rr1", "rr2": "rr2",
           "invalidation": "inval", "invalid": "inval", "invalidationprice": "inval", "status": "status"}
OLD_ORDER = ("coin", "side", "ref", "entry", "stop", "tp1", "tp2", "rr1", "rr2")        # a list without a header: Coin,Side,Reference,Entry,Stop,TP1,TP2,RR1,RR2


def parse(text):
    """Rows of the pasted text (CSV or tab separated). The columns are found by the header names (Coin, Side, Entry, Stop, TP1, TP2, RR_TP1, RR_TP2,
    Invalidation, Reference USD, Status ...: any order, any of them missing except coin / side / entry / stop / TP1); a list without a header is read as
    Coin,Side,Reference,Entry,Stop,TP1,TP2,RR1,RR2. Returns (rows, errors). A row: coin, side, ref, entry, stop, tp1, tp2, rr1, rr2, inval."""
    text = (text or "").strip()
    if not text: return [], ["nothing was pasted"]
    dialect = "excel-tab" if "\t" in text.splitlines()[0] else "excel"
    rd = csv.reader(io.StringIO(text), dialect=dialect)
    rows, errs = [], []; cols = dict(zip(OLD_ORDER, range(len(OLD_ORDER))))
    for i, line in enumerate(rd, 1):
        cells = [c.strip() for c in line]
        if not any(cells): continue
        if cells[0].lower() in ("coin", "symbol", "pair") or any(re.sub(r"[^a-z0-9]", "", c.lower()) == "entry" for c in cells) and _num(cells[0]) is None:
            found = {HEADERS[k]: j for j, c in enumerate(cells) for k in [re.sub(r"[^a-z0-9]", "", c.lower())] if k in HEADERS}
            if {"coin", "side", "entry", "stop", "tp1"} <= set(found): cols = found
            else: errs.append(f"line {i}: the header needs Coin, Side, Entry, Stop and TP1 columns")
            continue
        get = lambda name: cells[cols[name]] if name in cols and cols[name] < len(cells) else ""
        if len(cells) < 6: errs.append(f"line {i}: needs at least Coin,Side,Entry,Stop,TP1"); continue
        coin = re.sub(r"[^A-Z0-9]", "", get("coin").upper())
        coin = coin[:-4] if coin.endswith("USDT") else coin
        side = {"long": "LONG", "buy": "LONG", "short": "SHORT", "sell": "SHORT"}.get(get("side").lower())
        entry, stop, tp1, tp2 = (_num(get(k)) for k in ("entry", "stop", "tp1", "tp2"))
        if not coin or not side or not entry or not stop or not tp1: errs.append(f"line {i}: could not read {get('coin') or '?'} {get('side')}"); continue
        sd = 1 if side == "LONG" else -1
        if not (sd * (stop - entry) < 0 and sd * (tp1 - entry) > 0 and (tp2 is None or sd * (tp2 - entry) > 0)):
            errs.append(f"line {i}: {coin} {side}: stop / targets are on the wrong side of the entry"); continue
        inval = _num(get("inval"))
        if inval is not None and sd * (inval - stop) >= 0: inval = None                    # it must lie beyond the stop, on the stop's side
        rows.append(dict(coin=coin, side=side, ref=_num(get("ref")), entry=entry, stop=stop, tp1=tp1, tp2=tp2, rr1=_num(get("rr1")), rr2=_num(get("rr2")), inval=inval))
    if len(rows) > MAX_ROWS: rows, errs = rows[:MAX_ROWS], errs + [f"only the first {MAX_ROWS} rows were kept"]
    return rows, errs


def save(text):
    rows, errs = parse(text)
    if not rows: return dict(ok=False, error="; ".join(errs) or "no setups found")
    doc = dict(created=_now_ms(), rows=rows, expire_ms=EXPIRE_MS)
    if not sbgz_auto._write_key(KEY, doc): return dict(ok=False, error="could not save the list; try again")
    with _lock: _cache.clear()
    return dict(ok=True, saved=len(rows), skipped=errs, created=doc["created"], rows=rows)


def clear():
    ok = sbgz_auto._write_key(KEY, None)
    return dict(ok=bool(ok))


def judge(r, candles, created, now, expire_ms=None):
    """(INVALIDATED: price reached the row's invalidation level before the entry.) Status of one setup from candles (dicts with start ms, high, low) that start at or after the paste time. Returns dict(status, ...)."""
    sd = 1 if r["side"] == "LONG" else -1; risk = abs(r["entry"] - r["stop"])
    tp2 = r.get("tp2"); out = dict(status="WAITING", inval_t=None, entered=None, tp1_t=None, tp2_t=None, stop_t=None, best_r=None, R1=None, R2=None, now_r=None)
    entered = False; best = 0.0
    for c in candles:
        hi, lo = c["high"], c["low"]
        if not entered:
            if (hi >= r["entry"]) if sd > 0 else (lo <= r["entry"]):
                entered = True; out["entered"] = c["start"]; out["status"] = "ENTERED"
            elif r.get("inval") and ((lo <= r["inval"]) if sd > 0 else (hi >= r["inval"])):
                out.update(status="INVALIDATED", inval_t=c["start"]); break                # price went beyond the invalidation level without entering: the setup is void
            else:
                continue
        fav = (hi - r["entry"]) if sd > 0 else (r["entry"] - lo)
        best = max(best, fav / risk)
        stopped = (lo <= r["stop"]) if sd > 0 else (hi >= r["stop"])
        t1 = (hi >= r["tp1"]) if sd > 0 else (lo <= r["tp1"])
        t2 = tp2 is not None and ((hi >= tp2) if sd > 0 else (lo <= tp2))
        if out["tp1_t"] is None:
            if stopped: out.update(status="STOPPED", stop_t=c["start"], R1=-1.0, R2=-1.0); break
            if t1:
                out.update(status="TP1 HIT", tp1_t=c["start"], R1=round(abs(r["tp1"] - r["entry"]) / risk, 2))
                if t2: out.update(status="TP2 HIT", tp2_t=c["start"], R2=round(abs(tp2 - r["entry"]) / risk, 2)); break
        else:
            if stopped: out.update(status="STOPPED AFTER TP1", stop_t=c["start"], R2=-1.0); break
            if t2: out.update(status="TP2 HIT", tp2_t=c["start"], R2=round(abs(tp2 - r["entry"]) / risk, 2)); break
    if entered: out["best_r"] = round(best, 2)
    if out["status"] == "WAITING" and now - created > (expire_ms or EXPIRE_MS): out["status"] = "EXPIRED"
    return out


TAKER = 0.00055                                            # Bybit linear perpetual, standard account: market orders (the breakout entry, a stop)
MAKER = 0.0002                                             # limit orders (a take-profit)
HIST_KEY = "setup_history"
HIST_MAX = 4000
_hist_lock = threading.Lock()
_hist_seen = {}                                            # key -> the last archived row (skips writes when nothing changed)


def fee_r(r, win):
    """Round-trip fees of one setup in R: entry at the market when the breakout triggers (taker), exit at a limit take-profit (maker) when it
    wins or at the stop (taker) when it loses, on a notional of about the entry price. 0.5% stops: about 0.15R for a win, 0.22R for a loss."""
    risk = abs(r["entry"] - r["stop"])
    return (TAKER + (MAKER if win else TAKER)) * r["entry"] / risk if risk > 0 else 0.0


def _archive(created, rows):
    """Keep every setup's results (with their times) after the list is replaced, so the profit calendar goes back further than the current list."""
    keep = ("coin", "side", "entry", "stop", "tp1", "tp2", "entered", "tp1_t", "tp2_t", "stop_t", "R1", "R2", "fee1", "fee2", "status")
    new = {}
    for x in rows:
        if x.get("R1") is None and x.get("R2") is None: continue
        k = f"{created}|{x['coin']}|{x['side']}"
        row = dict({f: x.get(f) for f in keep}, created=created)
        if _hist_seen.get(k) != row: new[k] = row
    if not new: return
    with _hist_lock:
        doc, ok = sbgz_auto._read_key(HIST_KEY)
        if not ok: return
        hist = dict((doc or {}).get("rows") or {}); hist.update(new)
        if len(hist) > HIST_MAX:
            for k in sorted(hist, key=lambda k: hist[k].get("created") or 0)[:len(hist) - HIST_MAX]: del hist[k]
        if sbgz_auto._write_key(HIST_KEY, dict(rows=hist)): _hist_seen.update(new)


def calendar(plan=2, risk=0.0, fees=True, tz=0.0, now=None):
    """Profit by day over every archived setup result (GET /api/setups/calendar). plan 1: every setup exits all at TP1, plan 2: all at TP2.
    A setup counts on the day its result was decided (the TP time, or the stop time). R after fees when fees; dollars = R x risk per trade."""
    status(now)                                            # brings the archive up to date with the current list
    doc, ok = sbgz_auto._read_key(HIST_KEY)
    if not ok: return dict(ok=False, error="the setup history could not be read; try again in a minute")
    r_key, f_key = ("R1", "fee1") if plan == 1 else ("R2", "fee2")
    days = {}
    for h in ((doc or {}).get("rows") or {}).values():
        r = h.get(r_key)
        if r is None: continue
        t = (h.get("tp1_t") if plan == 1 else h.get("tp2_t")) if r > 0 else h.get("stop_t")
        if not t: continue
        net = r - (h.get(f_key) or 0.0) if fees else r
        day = time.strftime("%Y-%m-%d", time.gmtime(t / 1000 + tz * 3600))
        d = days.setdefault(day, dict(date=day, n=0, wins=0, losses=0, r=0.0, trades=[]))
        d["n"] += 1; d["wins" if r > 0 else "losses"] += 1; d["r"] += net
        d["trades"].append(dict(coin=h["coin"], side=h["side"], r=round(net, 2), t=t, result="TP" + str(plan) if r > 0 else "STOP"))
    out = sorted(days.values(), key=lambda d: d["date"], reverse=True)
    for d in out:
        d["r"] = round(d["r"], 2); d["usd"] = round(d["r"] * risk, 2) if risk else None; d["trades"].sort(key=lambda x: -x["t"])
    tot_r = round(sum(d["r"] for d in out), 2)
    return dict(ok=True, plan=plan, fees=bool(fees), risk=risk, days=out, total=dict(n=sum(d["n"] for d in out), wins=sum(d["wins"] for d in out), losses=sum(d["losses"] for d in out),
                r=tot_r, usd=round(tot_r * risk, 2) if risk else None, best=max((d["r"] for d in out), default=None), worst=min((d["r"] for d in out), default=None)))


def _interval_for(age_ms):
    """The finest candles that cover the time since the paste: 1m up to ~16h, 5m up to ~3.4 days, 15m beyond."""
    if age_ms <= 15 * 3_600_000: return "1", 60_000
    if age_ms <= 82 * 3_600_000: return "5", 300_000
    return "15", 900_000


def _candles(coin, created, now):
    iv, step = _interval_for(now - created)
    hit = _cache.get(coin)
    if hit and hit[1] == iv and now - hit[0] < CACHE_MS: return hit[2]
    bars = min(3000, (now - created) // step + 5)
    cs = []
    for attempt in range(3):                               # a download can fail on its own (timeout, rate limit): try again before giving up
        cs = sbgz.fetch_candles(coin + "USDT", iv, int(bars), timeout=8)
        if cs: break
        time.sleep(0.4 * (attempt + 1))
    if cs: _cache[coin] = (now, iv, cs)
    elif hit: return hit[2]                                # still nothing: the last candles we had are better than none
    return cs


def status(now=None):
    """GET /api/setups: the saved list with every setup judged now."""
    now = now or _now_ms()
    doc, ok = sbgz_auto._read_key(KEY)
    if not ok: return dict(ok=False, error="the setup store could not be read; try again in a minute")
    if not doc or not doc.get("rows"): return dict(ok=True, empty=True)
    created = int(doc["created"]); rows = doc["rows"]; expire = int(doc.get("expire_ms") or EXPIRE_MS)
    from concurrent.futures import ThreadPoolExecutor
    coins = sorted({r["coin"] for r in rows})
    with ThreadPoolExecutor(8) as ex: got = dict(zip(coins, ex.map(lambda c: _candles(c, created, now), coins)))
    out = []; hist = None
    for r in rows:
        cs = [c for c in (got.get(r["coin"]) or []) if c["start"] >= created // 60_000 * 60_000]
        if not got.get(r["coin"]):
            if hist is None: hist = ((sbgz_auto._read_key(HIST_KEY)[0] or {}).get("rows") or {})
            h = hist.get(f"{created}|{r['coin']}|{r['side']}")                                  # no candles right now: a result already archived still stands
            if h: out.append(dict(r, **{k: h.get(k) for k in ("status", "entered", "tp1_t", "tp2_t", "stop_t", "R1", "R2")}, best_r=None, now_r=None, stale=True)); continue
            out.append(dict(r, status="NO DATA", entered=None, tp1_t=None, tp2_t=None, stop_t=None, best_r=None, R1=None, R2=None, now_r=None)); continue
        j = judge(r, cs, created, now, expire)
        last = got[r["coin"]][-1]["close"]
        sd = 1 if r["side"] == "LONG" else -1
        j["price"] = last; j["dist_pct"] = round((last - r["entry"]) / r["entry"] * 100, 3)
        if j["status"] in ("ENTERED", "TP1 HIT"): j["now_r"] = round(sd * (last - r["entry"]) / abs(r["entry"] - r["stop"]), 2)
        out.append(dict(r, **j))
    for x in out:                                          # fees in R: the plan's win exits at a limit TP (maker), a loss at the stop (taker)
        x["fee1"] = fee_r(x, x["R1"] > 0) if x.get("R1") is not None else None
        x["fee2"] = fee_r(x, x["R2"] > 0) if x.get("R2") is not None else None
    done = [x for x in out if x["R1"] is not None]
    done2 = [x for x in out if x["R2"] is not None]
    _archive(created, out)
    summ = dict(n=len(out), invalidated=sum(1 for x in out if x["status"] == "INVALIDATED"), no_data=sum(1 for x in out if x["status"] == "NO DATA"), waiting=sum(1 for x in out if x["status"] == "WAITING"), entered=sum(1 for x in out if x["entered"]),
                open=sum(1 for x in out if x["status"] in ("ENTERED", "TP1 HIT")), expired=sum(1 for x in out if x["status"] == "EXPIRED"),
                stopped=sum(1 for x in out if x["status"].startswith("STOPPED")), tp1=sum(1 for x in out if x["tp1_t"]), tp2=sum(1 for x in out if x["tp2_t"]),
                trades=sum(1 for x in out if x["entered"]), wins=sum(1 for x in out if x["tp1_t"]), losses=sum(1 for x in out if x["status"] == "STOPPED"),
                tp1_hit=sum(1 for x in out if x["tp1_t"]), unrealized_r=round(sum(x["now_r"] for x in out if x.get("now_r") is not None), 2),
                r_tp1_plan=round(sum(x["R1"] for x in done), 2), r_tp2_plan=round(sum(x["R2"] for x in done2), 2), decided1=len(done), decided2=len(done2),
                fees_tp1_r=round(sum(x["fee1"] for x in done), 2), fees_tp2_r=round(sum(x["fee2"] for x in done2), 2),
                r_tp1_net=round(sum(x["R1"] - x["fee1"] for x in done), 2), r_tp2_net=round(sum(x["R2"] - x["fee2"] for x in done2), 2))
    return dict(ok=True, created=created, expire_h=round(expire / 3_600_000, 2), generated=now, summary=summ, rows=out)


def expiry_ms():
    doc, ok = sbgz_auto._read_key(KEY)
    return int((doc or {}).get("expire_ms") or EXPIRE_MS) if ok else EXPIRE_MS


def set_expiry(hours):
    """Change how long a setup may wait for its entry (hours, 1 to 720). Returns dict(ok)."""
    doc, ok = sbgz_auto._read_key(KEY)
    if not ok or not doc: return dict(ok=False, error="no list is tracked")
    if not 0 < hours <= 720: return dict(ok=False, error="hours must be between 1 and 720")
    doc["expire_ms"] = int(hours * 3_600_000)
    with _lock: _cache.clear()
    return dict(ok=bool(sbgz_auto._write_key(KEY, doc)))
