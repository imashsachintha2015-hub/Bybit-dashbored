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


def parse(text):
    """Rows of the pasted text (CSV with a header, or tab separated). Returns (rows, errors). A row: coin, side, ref, entry, stop, tp1, tp2, rr1, rr2."""
    text = (text or "").strip()
    if not text: return [], ["nothing was pasted"]
    dialect = "excel-tab" if "\t" in text.splitlines()[0] else "excel"
    rd = csv.reader(io.StringIO(text), dialect=dialect)
    rows, errs = [], []
    for i, line in enumerate(rd, 1):
        cells = [c.strip() for c in line]
        if not any(cells): continue
        if cells[0].lower() in ("coin", "symbol", "pair"): continue
        if len(cells) < 6: errs.append(f"line {i}: needs at least Coin,Side,Reference,Entry,Stop,TP1"); continue
        coin = re.sub(r"[^A-Z0-9]", "", cells[0].upper())
        coin = coin[:-4] if coin.endswith("USDT") else coin
        side = {"long": "LONG", "buy": "LONG", "short": "SHORT", "sell": "SHORT"}.get(cells[1].lower())
        ref, entry, stop, tp1 = (_num(cells[k]) for k in (2, 3, 4, 5))
        tp2 = _num(cells[6]) if len(cells) > 6 else None
        if not coin or not side or not entry or not stop or not tp1: errs.append(f"line {i}: could not read {cells[0] or '?'} {cells[1] if len(cells) > 1 else ''}"); continue
        sd = 1 if side == "LONG" else -1
        if not (sd * (stop - entry) < 0 and sd * (tp1 - entry) > 0 and (tp2 is None or sd * (tp2 - entry) > 0)):
            errs.append(f"line {i}: {coin} {side}: stop / targets are on the wrong side of the entry"); continue
        rows.append(dict(coin=coin, side=side, ref=ref, entry=entry, stop=stop, tp1=tp1, tp2=tp2,
                         rr1=_num(cells[7]) if len(cells) > 7 else None, rr2=_num(cells[8]) if len(cells) > 8 else None))
    if len(rows) > MAX_ROWS: rows, errs = rows[:MAX_ROWS], errs + [f"only the first {MAX_ROWS} rows were kept"]
    return rows, errs


def save(text):
    rows, errs = parse(text)
    if not rows: return dict(ok=False, error="; ".join(errs) or "no setups found")
    doc = dict(created=_now_ms(), rows=rows, expire_ms=EXPIRE_MS)
    if not sbgz_auto._write_key(KEY, doc): return dict(ok=False, error="could not save the list; try again")
    with _lock: _cache.clear()
    return dict(ok=True, saved=len(rows), skipped=errs)


def clear():
    ok = sbgz_auto._write_key(KEY, None)
    return dict(ok=bool(ok))


def judge(r, candles, created, now):
    """Status of one setup from candles (dicts with start ms, high, low) that start at or after the paste time. Returns dict(status, ...)."""
    sd = 1 if r["side"] == "LONG" else -1; risk = abs(r["entry"] - r["stop"])
    tp2 = r.get("tp2"); out = dict(status="WAITING", entered=None, tp1_t=None, tp2_t=None, stop_t=None, best_r=None, R1=None, R2=None, now_r=None)
    entered = False; best = 0.0
    for c in candles:
        hi, lo = c["high"], c["low"]
        if not entered:
            if (hi >= r["entry"]) if sd > 0 else (lo <= r["entry"]):
                entered = True; out["entered"] = c["start"]; out["status"] = "ENTERED"
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
    if out["status"] == "WAITING" and now - created > EXPIRE_MS: out["status"] = "EXPIRED"
    return out


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
    cs = sbgz.fetch_candles(coin + "USDT", iv, int(bars), timeout=8)
    if cs: _cache[coin] = (now, iv, cs)
    return cs


def status(now=None):
    """GET /api/setups: the saved list with every setup judged now."""
    now = now or _now_ms()
    doc, ok = sbgz_auto._read_key(KEY)
    if not ok: return dict(ok=False, error="the setup store could not be read; try again in a minute")
    if not doc or not doc.get("rows"): return dict(ok=True, empty=True)
    created = int(doc["created"]); rows = doc["rows"]
    from concurrent.futures import ThreadPoolExecutor
    coins = sorted({r["coin"] for r in rows})
    with ThreadPoolExecutor(8) as ex: got = dict(zip(coins, ex.map(lambda c: _candles(c, created, now), coins)))
    out = []
    for r in rows:
        cs = [c for c in (got.get(r["coin"]) or []) if c["start"] >= created // 60_000 * 60_000]
        if not got.get(r["coin"]):
            out.append(dict(r, status="NO DATA", entered=None, tp1_t=None, tp2_t=None, stop_t=None, best_r=None, R1=None, R2=None, now_r=None)); continue
        j = judge(r, cs, created, now)
        last = got[r["coin"]][-1]["close"]
        sd = 1 if r["side"] == "LONG" else -1
        j["price"] = last; j["dist_pct"] = round((last - r["entry"]) / r["entry"] * 100, 3)
        if j["status"] in ("ENTERED", "TP1 HIT"): j["now_r"] = round(sd * (last - r["entry"]) / abs(r["entry"] - r["stop"]), 2)
        out.append(dict(r, **j))
    done = [x for x in out if x["R1"] is not None]
    done2 = [x for x in out if x["R2"] is not None]
    summ = dict(n=len(out), waiting=sum(1 for x in out if x["status"] == "WAITING"), entered=sum(1 for x in out if x["entered"]),
                open=sum(1 for x in out if x["status"] in ("ENTERED", "TP1 HIT")), expired=sum(1 for x in out if x["status"] == "EXPIRED"),
                stopped=sum(1 for x in out if x["status"].startswith("STOPPED")), tp1=sum(1 for x in out if x["tp1_t"]), tp2=sum(1 for x in out if x["tp2_t"]),
                r_tp1_plan=round(sum(x["R1"] for x in done), 2), r_tp2_plan=round(sum(x["R2"] for x in done2), 2), decided1=len(done), decided2=len(done2))
    return dict(ok=True, created=created, expire_h=EXPIRE_MS // 3_600_000, generated=now, summary=summ, rows=out)
