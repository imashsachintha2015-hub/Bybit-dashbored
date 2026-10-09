"""Binance USDT-M positioning data (data.binance.vision futures/um/daily/metrics, 5-minute rows) for the 36 coins.
  python3 -I fetch_oi.py list      -> oi_raw/_list.json   (file names per Binance symbol, from the public S3 listing)
  python3 -I fetch_oi.py download  -> oi_raw/{BSYM}/{BSYM}-metrics-YYYY-MM-DD.zip   (resumable)
  python3 -I fetch_oi.py build     -> oi/{SYM}.npz   hourly rows (UTC hour start ms):
        last   create_time of the last snapshot in the hour (ms)
        oi     open interest in coins (sum_open_interest), last snapshot of the hour
        oiv    open interest in USD (sum_open_interest_value), last snapshot
        ls     all-account long/short ratio (count_long_short_ratio), last snapshot
        topc   top-trader long/short account ratio, last snapshot;  tops  top-trader long/short position ratio, last snapshot
        tk     mean of ln(taker buy volume / taker sell volume) over the hour's snapshots;  tkn  number of those snapshots
Downloaded files are data only: keys from the listing are validated against the exact expected name and the zips are read in memory."""
import io, os, re, sys, json, time, zipfile, calendar, math, urllib.request, urllib.error, urllib.parse
from concurrent.futures import ThreadPoolExecutor
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__)); RAW = os.path.join(HERE, "oi_raw"); OUT = os.path.join(HERE, "oi")
SYMS = "AAVE ADA APT ARB ATOM AVAX BCH BNB BTC CRV DOGE DOT ETC ETH FIL HBAR ICP INJ LINK LTC MKR NEAR ONDO OP POL RUNE SEI SOL STX SUI TIA TON TRX UNI WLD XRP".split()
ALIAS = {"POL": ["POLUSDT", "MATICUSDT"]}                       # same mapping as funding (POL first, MATIC fills earlier hours)
BUCKET = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
BASE = "https://data.binance.vision/data/futures/um/daily/metrics"
H = 3600000

def get(url, tries=6):
    """every URL requested comes from the listing, so a 404 is retried too (the S3 endpoint returns occasional spurious 404s
    under parallel requests); None only if it persists"""
    n404 = 0
    for k in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "research"}), timeout=30) as r: return r.read()
        except urllib.error.HTTPError as e:
            n404 += e.code == 404
            time.sleep(1 + 2 * k)
        except Exception: time.sleep(1 + 2 * k)
    if n404 == tries: return None
    raise RuntimeError("failed: " + url)

def list_sym(bs):
    pat = re.compile(rf"^data/futures/um/daily/metrics/{bs}/{bs}-metrics-(\d{{4}}-\d{{2}}-\d{{2}})\.zip$")
    days, marker = [], ""
    while True:
        x = get(f"{BUCKET}?delimiter=/&prefix=data/futures/um/daily/metrics/{bs}/" + (f"&marker={urllib.parse.quote(marker, safe='')}" if marker else ""))
        if x is None: raise RuntimeError("listing failed for " + bs)
        x = x.decode()
        keys = re.findall(r"<Key>([^<]+)</Key>", x)
        days += [m.group(1) for k in keys for m in [pat.match(k)] if m]
        if "<IsTruncated>true</IsTruncated>" not in x or not keys: break
        marker = keys[-1]
    return bs, sorted(set(days))

def dl(job):
    bs, d = job; p = os.path.join(RAW, bs, f"{bs}-metrics-{d}.zip")
    if os.path.exists(p) and os.path.getsize(p) > 0: return 0
    raw = get(f"{BASE}/{bs}/{bs}-metrics-{d}.zip")
    if raw is None: return -1
    tmp = f"{p}.{os.getpid()}.part"
    with open(tmp, "wb") as f: f.write(raw)
    os.replace(tmp, p); return 1

_day = {}
def ms_of(s):
    d, tm = s[:10], s[11:19]
    if d not in _day: _day[d] = calendar.timegm(time.strptime(d, "%Y-%m-%d")) * 1000
    hh, mm, ss = int(tm[0:2]), int(tm[3:5]), int(tm[6:8]); return _day[d] + (hh * 3600 + mm * 60 + ss) * 1000

COLS = ["sum_open_interest", "sum_open_interest_value", "count_long_short_ratio", "count_toptrader_long_short_ratio",
        "sum_toptrader_long_short_ratio", "sum_taker_long_short_vol_ratio"]
def fnum(x):
    try:
        v = float(x); return v if math.isfinite(v) else float("nan")
    except ValueError: return float("nan")

def parse(p):
    z = zipfile.ZipFile(p); names = [n for n in z.namelist() if n.endswith(".csv")]
    if not names: return []
    lines = z.read(names[0]).decode("utf-8", "replace").splitlines()
    if not lines: return []
    if lines[0][:1].isdigit(): head = ["create_time", "symbol"] + COLS; body = lines
    else: head = [h.strip() for h in lines[0].split(",")]; body = lines[1:]
    ix = [head.index(c) if c in head else None for c in ["create_time"] + COLS]
    out = []
    for ln in body:
        f = ln.split(",")
        if len(f) < len(head) or not f[ix[0]][:1].isdigit(): continue
        try: t = ms_of(f[ix[0]])
        except Exception: continue
        out.append((t,) + tuple(fnum(f[i]) if i is not None else float("nan") for i in ix[1:]))
    return out

def build(sym, lists):
    rows = {}
    for bs in ALIAS.get(sym, [sym + "USDT"]):
        hrs = {}
        for d in lists.get(bs, []):
            p = os.path.join(RAW, bs, f"{bs}-metrics-{d}.zip")
            if not os.path.exists(p): continue
            for t, oi, oiv, ls, topc, tops, tk in parse(p):
                hk = t // H * H; r = hrs.get(hk)
                if r is None: r = hrs[hk] = [-1, np.nan, np.nan, np.nan, np.nan, np.nan, 0.0, 0]
                if t > r[0] and math.isfinite(oi) and oi > 0: r[0], r[1], r[2], r[3], r[4], r[5] = t, oi, oiv, ls, topc, tops
                if math.isfinite(tk) and tk > 0: r[6] += math.log(tk); r[7] += 1
        for hk, r in hrs.items():
            if hk not in rows and r[0] > 0: rows[hk] = r          # first symbol in the alias list wins
    ks = sorted(rows)
    if not ks: return sym, 0, "-", "-"
    A = np.array([rows[k] for k in ks], dtype=float)
    tk = np.where(A[:, 7] > 0, A[:, 6] / np.maximum(A[:, 7], 1), np.nan)
    np.savez(os.path.join(OUT, f"{sym}.npz"), hour=np.array(ks, dtype=np.int64), last=A[:, 0].astype(np.int64), oi=A[:, 1], oiv=A[:, 2],
             ls=A[:, 3], topc=A[:, 4], tops=A[:, 5], tk=tk, tkn=A[:, 7].astype(np.int64))
    f = lambda m: time.strftime("%Y-%m-%d", time.gmtime(m / 1000))
    return sym, len(ks), f(ks[0]), f(ks[-1])

if __name__ == "__main__":
    mode = sys.argv[1]; os.makedirs(RAW, exist_ok=True); os.makedirs(OUT, exist_ok=True)
    bsyms = [b for s in SYMS for b in ALIAS.get(s, [s + "USDT"])]
    if mode == "list":
        with ThreadPoolExecutor(4) as ex: lists = dict(ex.map(list_sym, bsyms))
        json.dump(lists, open(os.path.join(RAW, "_list.json"), "w"))
        for b in bsyms: print(f"  {b:10s} {len(lists[b]):5d} days  {lists[b][0] if lists[b] else '-'} .. {lists[b][-1] if lists[b] else '-'}")
        print("total files:", sum(len(v) for v in lists.values()))
    elif mode == "download":
        lists = json.load(open(os.path.join(RAW, "_list.json")))
        for b in lists: os.makedirs(os.path.join(RAW, b), exist_ok=True)
        jobs = [(b, d) for b, ds in lists.items() for d in ds]; done = new = miss = 0; t0 = time.time()
        if len(sys.argv) > 2 and sys.argv[2] == "reverse": jobs = jobs[::-1]
        with ThreadPoolExecutor(16) as ex:
            for r in ex.map(dl, jobs):
                done += 1; new += r == 1; miss += r == -1
                if done % 5000 == 0: print(f"  {done}/{len(jobs)} files ({new} new, {miss} missing) {time.time() - t0:.0f}s", flush=True)
        print(f"download finished: {done} files, {new} new, {miss} missing, {time.time() - t0:.0f}s")
    elif mode == "build":
        lists = json.load(open(os.path.join(RAW, "_list.json")))
        for s in (sys.argv[2].split(",") if len(sys.argv) > 2 else SYMS): print("  %-5s %6d hours  %s .. %s" % build(s, lists), flush=True)
