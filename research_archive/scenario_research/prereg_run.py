"""ONE-LOOK run of the 6 tests frozen in PREREG_old_period.md (written before d5 was downloaded).
d5 = OKX 1H 2021-06-01 .. 2024-04-09 (never used for design or selection). d4 = 2024-04-09 .. 2026-09-30 (where the ideas
were found) is shown next to it as the in-sample reference only. Verdicts come from d5 alone.
Costs: 14 bps market / 8 bps limit round trip + 0.5 bp funding per 8h. $10 portfolio: 2% risk, max 3 open, one position per coin."""
import os, sys, json, math, collections, pickle, subprocess, time
sys.path.insert(0, "/home/user/Bybit-dashbored")
from multiprocessing import Pool
from backend_lib import amd_fvg
import scen, scen2
DAY = 86400000; HR = 3600000; FEE = {"mkt": 14e-4, "lmt": 8e-4}; FUND_8H = 0.5e-4

def tstat(pairs):
    d = collections.defaultdict(list)
    for t, r in pairs: d[t // DAY].append(r)
    n = sum(len(v) for v in d.values())
    if n < 2: return n, 0.0, 0.0
    m = sum(x for v in d.values() for x in v) / n
    se = math.sqrt(sum((sum(v) - m * len(v)) ** 2 for v in d.values())) / n
    return n, m, (m / se if se > 0 else 0.0)

def equity(tr, risk=0.02, maxpos=3, start=10.0):
    tr = sorted(tr); openp = []; eq = start; pk = start; dd = 0.0; taken = 0
    for te, sym, R, tx in tr:
        for p in sorted([p for p in openp if p[0] <= te]):
            openp.remove(p); eq += p[1]; pk = max(pk, eq); dd = min(dd, eq / pk - 1)
        if len(openp) >= maxpos or any(p[2] == sym for p in openp) or eq < 1: continue
        openp.append((tx, eq * risk * R, sym)); taken += 1
    for p in sorted(openp): eq += p[1]; pk = max(pk, eq); dd = min(dd, eq / pk - 1)
    return eq, taken, dd * 100

def summarize(rows):
    if len(rows) < 2: return dict(n=len(rows), m=0, t=0, win=0, pf=0, tot=0, eq=10, taken=0, dd=0)
    n, m, t = tstat([(r["t"], r["R"]) for r in rows])
    gp = sum(r["R"] for r in rows if r["R"] > 0); gl = -sum(r["R"] for r in rows if r["R"] < 0)
    eq, taken, dd = equity([(r["te"], r["sym"], r["R"], r["tx"]) for r in rows])
    return dict(n=n, m=m, t=t, win=sum(r["R"] > 0 for r in rows) / len(rows) * 100, pf=gp / max(gl, 1e-9),
                tot=sum(r["R"] for r in rows), eq=eq, taken=taken, dd=dd)

def at_cost(rows, bps):
    return [dict(r, R=r["Rg"] - (bps * 1e-4 + FUND_8H * r["hours"] / 8) / r["rp"]) for r in rows]

# ---------------- event generation per folder ----------------
def _w2(args):
    return scen2.worker(args)

def scen2_events(folder):
    path = "scen2_ev.pkl" if folder == "d4" else f"scen2_ev_{folder}.pkl"
    if os.path.exists(path): return pickle.load(open(path, "rb"))["ev"]
    scen.D = folder
    coins = sorted(f.split("_")[0] for f in os.listdir(folder) if f.endswith("_1H.json") and len(json.load(open(f"{folder}/{f}"))) >= 2000)
    btcL = scen.btc_daily(False); btcS = scen.btc_daily(True)
    t0 = time.time()
    with Pool(4) as p: out = p.map(_w2, [(s, inv, btcS if inv else btcL) for s in coins for inv in (False, True)])
    ev = [e for _, _, E in out for e in E]
    pickle.dump(dict(ev=ev, coins=coins), open(path, "wb"))
    print(f"[{folder}] scen2 events {len(ev)} from {len(coins)} coins in {int(time.time() - t0)}s", flush=True)
    return ev

def ev_rows(ev, fam, bias, btc_aligned, tg="struct"):
    out = []
    for e in ev:
        if e["fam"] != fam or not e["res"] or tg not in e["res"]: continue
        if bias == "with" and e["tr1d"] != 1: continue
        if bias == "against" and e["tr1d"] != -1: continue
        if btc_aligned and e["btc"] != 1: continue
        Rg, rp, hours, te, tx = e["res"][tg]
        out.append(dict(t=e["t"], te=te, tx=tx, sym=e["sym"], side=e["side"], Rg=Rg, rp=rp, hours=hours,
                        R=Rg - (FEE[e["kind"]] + FUND_8H * hours / 8) / rp))
    return out

def cme_rows(folder):
    path = f"cme_{folder}.pkl"
    need = True
    if os.path.exists(path):
        P = pickle.load(open(path, "rb")); need = not P["W"] or "te" not in P["W"][0]
    if need:
        subprocess.run([sys.executable, "cme_check.py"], env=dict(os.environ, CME_DATA=folder), check=True, stdout=open(f"cme_{folder}_out.txt", "w"))
        P = pickle.load(open(path, "rb"))
    return P["W"], P["PL"]

def _amd(args):
    folder, sym, inv = args
    rows = json.load(open(f"{folder}/{sym}_1H.json"))
    if len(rows) < 300: return []
    A = amd_fvg.prep([tuple(r) for r in rows], invert=inv); out = []
    for P_ in (amd_fvg.NO_GATE, amd_fvg.PRIMARY):
        for r in amd_fvg.scan(A, P_, side="S" if inv else "L"):
            if r["status"] != "CLOSED": continue
            rp = r["risk_pct"] / 100; hours = (r["exit_t"] - r["fill_t"]) / HR + 1; Rg = r["R"] + amd_fvg.FEE_LIMIT / rp
            out.append(dict(cfg=P_["name"], t=r["signal_t"], te=r["fill_t"], tx=r["exit_t"] + HR, sym=sym, side="S" if inv else "L",
                            Rg=Rg, rp=rp, hours=hours, R=Rg - (FEE["lmt"] + FUND_8H * hours / 8) / rp))
    return out

def amd_rows(folder):
    coins = sorted(f.split("_")[0] for f in os.listdir(folder) if f.endswith("_1H.json"))
    with Pool(4) as p: parts = p.map(_amd, [(folder, s, inv) for s in coins for inv in (False, True)])
    rows = [r for P_ in parts for r in P_]
    return {c: [r for r in rows if r["cfg"] == c] for c in ("AMD_NO_GATE", "AMD_PRIMARY")}

def fill_rate_test(W, PL, gmin):
    w = [r for r in W if r["s"] == 1.0 and r["gap"] >= gmin]; p = [r for r in PL if r["s"] == 1.0 and r["gap"] >= gmin]
    def by_date(rows):
        d = collections.defaultdict(list)
        for r in rows: d[r["t"] // DAY].append(1.0 if r["fill24"] else 0.0)
        return [sum(v) / len(v) for v in d.values()]
    a, b = by_date(w), by_date(p)
    if len(a) < 3 or len(b) < 3: return len(w), len(p), 0, 0, 0
    ma, mb = sum(a) / len(a), sum(b) / len(b)
    va = sum((x - ma) ** 2 for x in a) / (len(a) - 1); vb = sum((x - mb) ** 2 for x in b) / (len(b) - 1)
    t = (ma - mb) / math.sqrt(va / len(a) + vb / len(b))
    rw = sum(r["fill24"] for r in w) / len(w); rpl = sum(r["fill24"] for r in p) / len(p)
    return len(w), len(p), rw * 100, rpl * 100, t

if __name__ == "__main__":
    tests = {}
    for folder in ("d4", "d5"):
        ev = scen2_events(folder); W, PL = cme_rows(folder); amd = amd_rows(folder)
        tests[folder] = {
            "1 CME gap fill, gap>=1%, stop 1x gap, coin 1D against, BTC aligned": ev_rows(ev, "CMEGAP_fill_g0.01_s1.0", "against", True),
            "2 CME gap fill, gap>=2%, stop 2x gap, no filters": [r for r in W if r["gap"] >= 0.02 and r["s"] == 2.0],
            "4 AMD_NO_GATE (frozen)": amd["AMD_NO_GATE"],
            "5 PWL sweep+reclaim, coin 1D with, BTC aligned, target PWH": ev_rows(ev, "PWL_SWEEP_RECLAIM", "with", True),
            "6 AMD_PRIMARY (frozen, reference)": amd["AMD_PRIMARY"],
        }
        tests[folder]["_fill"] = {g: fill_rate_test(W, PL, g) for g in (0.005, 0.01, 0.02)}
        tests[folder]["_cme_s1_against_nobtc"] = [r for r in W if r["gap"] >= 0.01 and r["s"] == 1.0 and r["tr1d"] == -1]
        print(f"[{folder}] done", flush=True)
    pickle.dump(tests, open("prereg_results.pkl", "wb"))

    print("\n================ PRE-REGISTERED TESTS: one look at 2021-06 .. 2024-04 (never used) ================")
    print("pass = avg net R > 0 and t >= 2.0 (strict, Bonferroni for 6 tests: t >= 2.6). t is clustered by day.\n")
    hdr = f"{'test':66s} | {'2024-26 (found here)':>22s} | {'2021-24 NEVER SEEN':>22s} | {'win%':>4s} {'PF':>4s} | {'$10 ->':>8s} {'taken':>5s} {'maxDD':>5s} | verdict"
    print(hdr); print("-" * len(hdr))
    for name in tests["d5"]:
        if name.startswith("_"): continue
        a = summarize(tests["d4"][name]); b = summarize(tests["d5"][name])
        v = "PASS strict" if b["m"] > 0 and b["t"] >= 2.6 else "PASS" if b["m"] > 0 and b["t"] >= 2.0 else "FAIL"
        print(f"{name:66s} | n={a['n']:5d} {a['m']:+.3f}R t={a['t']:+4.1f} | n={b['n']:5d} {b['m']:+.3f}R t={b['t']:+4.1f} | {b['win']:4.0f} {b['pf']:4.2f} |"
              f" ${b['eq']:7.2f} {b['taken']:5d} {b['dd']:4.0f}% | {v}")
        if name.startswith("2"):
            for g in (0.01,):
                n4w, n4p, rw4, rp4, t4 = tests["d4"]["_fill"][g]; n5w, n5p, rw5, rp5, t5 = tests["d5"]["_fill"][g]
                v3 = "PASS strict" if rw5 > rp5 and t5 >= 2.6 else "PASS" if rw5 > rp5 and t5 >= 2.0 else "FAIL"
                print(f"{'3 weekend vs placebo: filled within 24h (gap>=1%)':66s} | wkd {rw4:3.0f}% vs plc {rp4:3.0f}% t={t4:+4.1f} | wkd {rw5:3.0f}% vs plc {rp5:3.0f}% t={t5:+4.1f} |"
                      f" n weekend {n5w}, placebo {n5p} {'':12s} | {v3}")

    print("\n---- descriptive only (not tests): the same frozen rules on the never-seen period, split up ----")
    for name, rows in tests["d5"].items():
        if name.startswith("_"): continue
        yrs = collections.defaultdict(list)
        for r in rows: yrs[time.gmtime(r["t"] / 1000).tm_year].append(r["R"])
        sides = {s: [r["R"] for r in rows if r["side"] == s] for s in ("L", "S")}
        cost = {bps: summarize(at_cost(rows, bps))["m"] for bps in (4, 8, 14, 20)}
        print(f"{name[:40]:40s} years " + " ".join(f"{y}:{sum(v)/len(v):+.2f}({len(v)})" for y, v in sorted(yrs.items()))
              + " | long " + (f"{sum(sides['L'])/len(sides['L']):+.2f}({len(sides['L'])})" if sides['L'] else "-")
              + " short " + (f"{sum(sides['S'])/len(sides['S']):+.2f}({len(sides['S'])})" if sides['S'] else "-")
              + " | cost 4/8/14/20bps " + " ".join(f"{cost[k]:+.3f}" for k in (4, 8, 14, 20)))
    for g in (0.005, 0.01, 0.02):
        n5w, n5p, rw5, rp5, t5 = tests["d5"]["_fill"][g]
        print(f"  fill within 24h, gap>={g*100:.1f}%: weekend {rw5:.0f}% (n={n5w}) vs placebo {rp5:.0f}% (n={n5p}), t={t5:+.1f}")
    x = summarize(tests["d5"]["_cme_s1_against_nobtc"])
    print(f"  robustness for test 1 without the round-2 float quirk and without the BTC filter: n={x['n']} {x['m']:+.3f}R t={x['t']:+.1f}")
