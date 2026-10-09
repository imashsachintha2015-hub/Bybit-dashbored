"""Historical funding rates from Binance's public data archive (data.binance.vision, monthly fundingRate CSVs, USDT-M perpetuals).
Binance funding is used as the proxy for the funding a Bybit/OKX position would have paid (the venues track each other closely).
Output funding/{SYM}.json: sorted [[funding_time_ms, rate], ...]; rate is per interval (paid by longs to shorts when positive)."""
import io, os, sys, json, zipfile, urllib.request, time
from concurrent.futures import ThreadPoolExecutor
os.makedirs("funding", exist_ok=True)
ALIAS = {"POL": ["POLUSDT", "MATICUSDT"]}
MONTHS = [f"{y}-{m:02d}" for y in range(2019, 2027) for m in range(1, 13) if "2019-09" <= f"{y}-{m:02d}" <= "2026-09"]

def fetch(sym_b, month):
    u = f"https://data.binance.vision/data/futures/um/monthly/fundingRate/{sym_b}/{sym_b}-fundingRate-{month}.zip"
    for k in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "x"}), timeout=20) as r: raw = r.read()
            z = zipfile.ZipFile(io.BytesIO(raw)); lines = z.read(z.namelist()[0]).decode().splitlines()
            head = lines[0].split(",") if lines and not lines[0][0].isdigit() else ["calc_time", "funding_interval_hours", "last_funding_rate"]
            body = lines[1:] if lines and not lines[0][0].isdigit() else lines
            it = head.index("calc_time"); ir = head.index("last_funding_rate")
            return [(int(x.split(",")[it]) // 1000 * 1000, float(x.split(",")[ir])) for x in body if x.strip()]
        except urllib.error.HTTPError as e:
            if e.code == 404: return []
            time.sleep(1 + k)
        except Exception: time.sleep(1 + k)
    return None

def job(sym):
    names = ALIAS.get(sym, [sym + "USDT"]); out = {}; missing = 0
    for nb in names:
        for m in MONTHS:
            rows = fetch(nb, m)
            if rows is None: missing += 1; continue
            for t, r in rows: out.setdefault(t, r)
    rows = sorted([t, r] for t, r in out.items())
    json.dump(rows, open(f"funding/{sym}.json", "w"))
    first = time.strftime("%Y-%m", time.gmtime(rows[0][0] / 1000)) if rows else "-"
    return sym, len(rows), first, missing

if __name__ == "__main__":
    syms = sys.argv[1].split(",")
    with ThreadPoolExecutor(8) as ex:
        for r in ex.map(job, syms): print(r, flush=True)
