"""Does our research process predict anything? (descriptive, selects nothing)
For EVERY scenario configuration of round 1 (scen.py) and round 2 (scen2.py): mean net R on 2024-26 (d4, where we searched)
vs mean net R on 2021-24 (d5, never used). If the in-sample ranking carries real information, good configs stay good.
Also: the frozen AMD-FVG rule year by year 2021-2026 and its $10 equity at 1% and 2% risk."""
import os, sys, json, pickle, collections, math, time
sys.path.insert(0, "/home/user/Bybit-dashbored")
import numpy as np
import scen
from prereg_run import tstat, equity, summarize, FEE, FUND_8H

def round1_d5():
    path = "scen_ev_d5.pkl"                      # never overwrite scen_ev.pkl (that is the d4 file)
    if os.path.exists(path): return pickle.load(open(path, "rb"))["ev"]
    scen.D = "d5"
    coins = sorted(f.split("_")[0] for f in os.listdir("d5") if f.endswith("_1H.json") and len(json.load(open(f"d5/{f}"))) >= 2000)
    out = scen.run(coins); ev = [e for _, _, E, _ in out for e in E]
    pickle.dump(dict(ev=ev, coins=coins), open(path, "wb")); return ev

BIAS = {"any": lambda e: True, "with1D": lambda e: e["tr1d"] == 1, "against1D": lambda e: e["tr1d"] == -1}
BTC = {"any": lambda e: True, "btcAligned": lambda e: e["btc"] == 1}

def config_table(ev):
    by = collections.defaultdict(list)
    for e in ev:
        if e["fam"] == "STATE_SAMPLE" or not e["res"]: continue
        for tg, (Rg, rp, hours, te, tx) in e["res"].items():
            R = Rg - (FEE[e["kind"]] + FUND_8H * hours / 8) / rp
            for b, fb in BIAS.items():
                if not fb(e): continue
                for bt, fbt in BTC.items():
                    if fbt(e): by[(e["fam"], b, bt, tg)].append((e["t"], R))
    return {k: tstat(v) for k, v in by.items()}

def rank(x): return np.argsort(np.argsort(x)).astype(float)

if __name__ == "__main__":
    r1_d4 = pickle.load(open("scen_ev.pkl", "rb"))["ev"]; r1_d5 = round1_d5()
    r2_d4 = pickle.load(open("scen2_ev.pkl", "rb"))["ev"]; r2_d5 = pickle.load(open("scen2_ev_d5.pkl", "rb"))["ev"]
    A = {**config_table(r1_d4), **config_table(r2_d4)}; B = {**config_table(r1_d5), **config_table(r2_d5)}
    keys = [k for k in A if k in B and A[k][0] >= 60 and B[k][0] >= 60]
    ma = np.array([A[k][1] for k in keys]); mb = np.array([B[k][1] for k in keys]); ta = np.array([A[k][2] for k in keys])
    rho = np.corrcoef(rank(ma), rank(mb))[0, 1]
    print(f"configurations with >= 60 trades in both periods: {len(keys)} (of {len(A)} tried)")
    print(f"rank correlation of avg net R, 2024-26 vs 2021-24: {rho:+.2f}   (0 = in-sample ranking is pure noise, 1 = perfect)")
    print(f"configs positive in 2024-26: {int((ma > 0).sum())}; of those still positive in 2021-24: {int(((ma > 0) & (mb > 0)).sum())}")
    good = ta >= 2.0
    print(f"configs with t >= 2 in 2024-26: {int(good.sum())}; still positive in 2021-24: {int((good & (mb > 0)).sum())}; "
          f"avg 2021-24 R of that group {mb[good].mean() if good.any() else float('nan'):+.3f}")
    order = np.argsort(-ta)
    for lab, idx in (("top 20 by 2024-26 t", order[:20]), ("bottom 20 by 2024-26 t", order[-20:])):
        print(f"{lab:24s}: 2024-26 avg {ma[idx].mean():+.3f}R | 2021-24 avg {mb[idx].mean():+.3f}R | positive in 2021-24: {int((mb[idx] > 0).sum())}/20")
    fam = collections.defaultdict(lambda: [[], []])
    for k, a_, b_ in zip(keys, ma, mb): fam[k[0]][0].append(a_); fam[k[0]][1].append(b_)
    print("\nper family (average over its variants): 2024-26 -> 2021-24, sorted by 2021-24")
    rows = sorted(((f, sum(v[0]) / len(v[0]), sum(v[1]) / len(v[1]), len(v[0])) for f, v in fam.items()), key=lambda r: -r[2])
    for f, a_, b_, n in rows: print(f"  {f:26s} {a_:+.3f} -> {b_:+.3f}  ({n} variants)")

    T = pickle.load(open("prereg_results.pkl", "rb"))
    print("\n=== frozen AMD-FVG, year by year (net R per trade, trades) ===")
    for cfg in ("4 AMD_NO_GATE (frozen)", "6 AMD_PRIMARY (frozen, reference)"):
        rows = T["d5"][cfg] + T["d4"][cfg]; yrs = collections.defaultdict(list)
        for r in rows: yrs[time.gmtime(r["t"] / 1000).tm_year].append(r["R"])
        print(f"  {cfg[2:]:30s} " + "  ".join(f"{y}: {sum(v)/len(v):+.3f} ({len(v)})" for y, v in sorted(yrs.items())))
        for risk in (0.01, 0.02):
            for lab, rr in (("2021-24 never seen", T["d5"][cfg]), ("2021-26 all", rows)):
                eq, taken, dd = equity([(r["te"], r["sym"], r["R"], r["tx"]) for r in rr], risk=risk)
                print(f"     risk {risk*100:.0f}%  {lab:18s}: $10 -> ${eq:,.2f}  ({taken} trades taken of {len(rr)}, max drawdown {dd:.0f}%)")
