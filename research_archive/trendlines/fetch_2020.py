"""Fetch OKX USDT-swap 1H candles from the earliest available bar (not before 2019-12-01) up to the start of d5 (2021-06-01) into d6/.
Pages backwards from 2021-06-01 00:00 UTC; confirmed bars only; base-currency volume (same fields as fetch_old.py)."""
import json, os, sys, time, urllib.request, calendar
from concurrent.futures import ThreadPoolExecutor
START = calendar.timegm(time.strptime("2019-12-01", "%Y-%m-%d")) * 1000
END = calendar.timegm(time.strptime("2021-06-01", "%Y-%m-%d")) * 1000
os.makedirs("d6", exist_ok=True)
def get(u):
    for i in range(5):
        try:
            with urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "x"}), timeout=20) as r: return json.loads(r.read())
        except Exception: time.sleep(1 + i)
    return {}
def job(sym):
    after = END; out = {}
    while True:
        d = get(f"https://www.okx.com/api/v5/market/history-candles?instId={sym}-USDT-SWAP&bar=1H&limit=100&after={after}").get("data", [])
        if not d: break
        for b in d:
            if b[8] == "1": out[int(b[0])] = [int(b[0]), float(b[1]), float(b[2]), float(b[3]), float(b[4]), float(b[6])]
        after = d[-1][0]
        if int(after) < START: break
        time.sleep(0.08)
    rows = sorted(v for k, v in out.items() if START <= k < END)
    json.dump(rows, open(f"d6/{sym}_1H.json", "w")); return sym, len(rows), (time.strftime("%Y-%m-%d", time.gmtime(rows[0][0] / 1000)) if rows else "-")
coins = sys.argv[1].split(",")
with ThreadPoolExecutor(6) as ex:
    for r in ex.map(job, coins): print(r, flush=True)
