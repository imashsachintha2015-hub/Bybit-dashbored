"""Download Binance USDT-M daily bookDepth (free, no key: data.binance.vision) and keep it small: the last snapshot of every 5-minute bin.
Per coin-day file: t (ms, bin start), depth[N,12] (base-asset depth) and notional[N,12] (USDT) at the levels
-5 -4 -3 -2 -1 -0.2 +0.2 +1 +2 +3 +4 +5 % from the mid price (negative = bids, positive = asks).
usage: python3 fetch_bookdepth.py OUT_DIR [workers]   (resumable: existing files are skipped)"""
import io, os, sys, time, zipfile, datetime, urllib.request, urllib.error
from concurrent.futures import ProcessPoolExecutor
import numpy as np
import pandas as pd

URL = "https://data.binance.vision/data/futures/um/daily/bookDepth/{s}/{s}-bookDepth-{d}.zip"
LEVELS = [-5.0, -4.0, -3.0, -2.0, -1.0, -0.2, 0.2, 1.0, 2.0, 3.0, 4.0, 5.0]
COINS = ("BTC ETH SOL XRP DOGE BNB ADA LINK AVAX LTC DOT NEAR ATOM APT ARB OP SUI INJ TIA WLD AAVE BCH CRV ETC FIL HBAR ICP RUNE TRX UNI").split()


def days(a, b):
    d = a
    while d <= b:
        yield d.isoformat(); d += datetime.timedelta(days=1)


def one(job):
    out, coin, day = job
    path = os.path.join(out, coin, day + ".npz")
    if os.path.exists(path): return "have"
    url = URL.format(s=coin + "USDT", d=day)
    for k in range(4):
        try:
            with urllib.request.urlopen(url, timeout=60) as r: blob = r.read()
            z = zipfile.ZipFile(io.BytesIO(blob)); df = pd.read_csv(io.BytesIO(z.read(z.namelist()[0])))
            t = pd.to_datetime(df["timestamp"], format="%Y-%m-%d %H:%M:%S").values.astype("datetime64[s]").astype(np.int64)
            lv = np.round(df["percentage"].values.astype(float), 1)
            ut, inv = np.unique(t, return_inverse=True)
            li = np.full(len(lv), -1)
            for j, L in enumerate(LEVELS): li[lv == L] = j
            ok = li >= 0
            D = np.full((len(ut), 12), np.nan, np.float32); N = np.full((len(ut), 12), np.nan, np.float32)
            D[inv[ok], li[ok]] = df["depth"].values[ok]; N[inv[ok], li[ok]] = df["notional"].values[ok]
            b = ut // 300                                                # keep the last snapshot of every 5-minute bin
            last = np.r_[b[1:] != b[:-1], True]
            os.makedirs(os.path.dirname(path), exist_ok=True)
            np.savez_compressed(path + ".tmp.npz", t=(b[last] * 300 * 1000).astype(np.int64), depth=D[last], notional=N[last])
            os.replace(path + ".tmp.npz", path)
            return "ok"
        except urllib.error.HTTPError as e:
            if e.code == 404: return "404"
            time.sleep(1 + k)
        except Exception:
            time.sleep(1 + k)
    return "fail"


if __name__ == "__main__":
    out = sys.argv[1]; workers = int(sys.argv[2]) if len(sys.argv) > 2 else 8
    first = datetime.date(2023, 1, 1); last = datetime.date(2026, 10, 8)
    jobs = [(out, c, d) for c in COINS for d in days(first, last)]
    res = {}; t0 = time.time()
    with ProcessPoolExecutor(workers) as ex:
        for i, r in enumerate(ex.map(one, jobs, chunksize=8)):
            res[r] = res.get(r, 0) + 1
            if (i + 1) % 500 == 0: print(f"{i + 1}/{len(jobs)} {res} {time.time() - t0:.0f}s", flush=True)
    print("done", res, f"{time.time() - t0:.0f}s", flush=True)
