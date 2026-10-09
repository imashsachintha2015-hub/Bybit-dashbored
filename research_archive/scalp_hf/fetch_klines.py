"""Download Binance USDT-M 1-minute klines (monthly archive) into compact per-month npz files.
usage: python3 fetch_klines.py OUT_DIR [workers]
Per file: t (int64 ms open time), X (float32 [n, 7] = open, high, low, close, quote_volume, trades, taker_buy_quote_volume)."""
import io, os, sys, time, zipfile, urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor
import numpy as np

BASE = "https://data.binance.vision/data/futures/um/monthly/klines/{s}/1m/{s}-1m-{y}-{m:02d}.zip"
DESIGN = "BTC ETH SOL XRP DOGE BNB ADA LINK AVAX LTC".split()
UNSEEN = "DOT NEAR ATOM APT ARB OP SUI INJ TIA WLD".split()


def months(y0, m0, y1, m1):
    y, m = y0, m0
    while (y, m) <= (y1, m1):
        yield y, m
        m += 1
        if m == 13: y, m = y + 1, 1


def jobs():
    for c in DESIGN:
        for y, m in months(2021, 1, 2026, 9): yield c, y, m
    for c in UNSEEN:
        for y, m in months(2025, 6, 2026, 9): yield c, y, m


def fetch(job, out):
    c, y, m = job
    path = os.path.join(out, c, f"{y}-{m:02d}.npz")
    if os.path.exists(path): return "have"
    url = BASE.format(s=c + "USDT", y=y, m=m)
    for attempt in range(5):
        try:
            with urllib.request.urlopen(url, timeout=60) as r: blob = r.read()
            z = zipfile.ZipFile(io.BytesIO(blob))
            raw = z.read(z.namelist()[0]).decode().splitlines()
            if raw and not raw[0][:1].isdigit(): raw = raw[1:]                       # header row in some months
            a = np.array([ln.split(",")[:11] for ln in raw], dtype=np.float64)
            t = a[:, 0].astype(np.int64)
            X = np.column_stack([a[:, 1], a[:, 2], a[:, 3], a[:, 4], a[:, 7], a[:, 8], a[:, 10]]).astype(np.float32)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            np.savez(path + ".tmp.npz", t=t, X=X); os.replace(path + ".tmp.npz", path)
            return "ok"
        except urllib.error.HTTPError as e:
            if e.code == 404: return "404"
            time.sleep(1 + attempt)
        except Exception:
            time.sleep(1 + attempt)
    return "fail"


if __name__ == "__main__":
    out = sys.argv[1]; workers = int(sys.argv[2]) if len(sys.argv) > 2 else 8
    js = list(jobs()); res = {}
    t0 = time.time()
    with ThreadPoolExecutor(workers) as ex:
        for i, (job, r) in enumerate(zip(js, ex.map(lambda j: fetch(j, out), js))):
            res[r] = res.get(r, 0) + 1
            if r in ("404", "fail"): print("MISSING" if r == "404" else "FAILED", job, flush=True)
            if (i + 1) % 50 == 0: print(f"{i + 1}/{len(js)} {res} {time.time() - t0:.0f}s", flush=True)
    print("done", res, f"{time.time() - t0:.0f}s", flush=True)
