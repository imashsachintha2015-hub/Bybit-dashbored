import json, os, sys, time, urllib.request
from concurrent.futures import ThreadPoolExecutor
START = int(time.mktime(time.strptime("2021-06-01", "%Y-%m-%d"))) * 1000
def get(u):
    for i in range(5):
        try:
            with urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "x"}), timeout=20) as r: return json.loads(r.read())
        except Exception: time.sleep(1 + i)
    return {}
def job(sym):
    first = json.load(open(f"d4/{sym}_1H.json"))[0][0]; after = first; out = {}
    while True:
        d = get(f"https://www.okx.com/api/v5/market/history-candles?instId={sym}-USDT-SWAP&bar=1H&limit=100&after={after}").get("data", [])
        if not d: break
        for b in d:
            if b[8] == "1": out[int(b[0])] = [int(b[0]), float(b[1]), float(b[2]), float(b[3]), float(b[4]), float(b[6])]
        after = d[-1][0]
        if int(after) < START: break
        time.sleep(0.08)
    rows = sorted(v for k, v in out.items() if k >= START)
    json.dump(rows, open(f"d5/{sym}_1H.json", "w")); return sym, len(rows)
coins = sys.argv[1].split(",")
with ThreadPoolExecutor(6) as ex:
    for r in ex.map(job, coins): print(r, flush=True)
