"""Cause map (Truth 2 for every scenario configuration of rounds 1 + 2): WHY did each one fail, and is the reason the same in
2024-26 (d4, where we searched) and 2021-24 (d5, never used)? Class per period from the gross (before-fee) edge:
  wrong direction : gross t <= -2 (loses even with zero fees)
  killed by fees  : gross t >= +2 but net <= 0
  profitable      : gross t >= +2 and net > 0
  no edge         : otherwise (|gross| indistinguishable from 0, fees make it negative)
Note: limit-entry families (kind 'lmt') used a fill rule that checked cancel conditions before the fill on the same bar (the same
look-ahead fixed in amd_fvg.py on 2026-10-08), so their numbers are an UPPER bound; they still failed."""
import os, sys, pickle, collections, math
REPO = os.environ.get("REPO", "/home/user/Bybit-dashbored")
for _d in ("resonance_test", "scenario_research"): sys.path.insert(1, f"{REPO}/research_archive/{_d}")
from prereg_run import tstat, FEE, FUND_8H
BIAS = {"any": lambda e: True, "with1D": lambda e: e["tr1d"] == 1, "against1D": lambda e: e["tr1d"] == -1}
BTC = {"any": lambda e: True, "btcAligned": lambda e: e["btc"] == 1}

def table(ev):
    by = collections.defaultdict(lambda: ([], []))
    kind = {}
    for e in ev:
        if e["fam"] == "STATE_SAMPLE" or not e["res"]: continue
        kind[e["fam"]] = e["kind"]
        for tg, (Rg, rp, hours, te, tx) in e["res"].items():
            for b, fb in BIAS.items():
                if not fb(e): continue
                for bt, fbt in BTC.items():
                    if fbt(e):
                        g, nt = by[(e["fam"], b, bt, tg)]
                        g.append((e["t"], Rg - FUND_8H * hours / 8 / rp)); nt.append((e["t"], Rg - (FEE[e["kind"]] + FUND_8H * hours / 8) / rp))
    return {k: (tstat(g), tstat(nt)) for k, (g, nt) in by.items()}, kind

def cls(g, nt):
    if g[2] <= -2: return "wrong direction"
    if g[2] >= 2: return "profitable" if nt[1] > 0 else "killed by fees"
    return "no edge"

if __name__ == "__main__":
    A, kind = {}, {}
    for f in ("scen_ev.pkl", "scen2_ev.pkl"):
        a, k = table(pickle.load(open(f, "rb"))["ev"]); A.update(a); kind.update(k)
    B = {}
    for f in ("scen_ev_d5.pkl", "scen2_ev_d5.pkl"):
        a, _ = table(pickle.load(open(f, "rb"))["ev"]); B.update(a)
    keys = [k for k in A if k in B and A[k][1][0] >= 60 and B[k][1][0] >= 60]
    cA = {k: cls(*A[k]) for k in keys}; cB = {k: cls(*B[k]) for k in keys}
    print(f"configs with >= 60 trades in both periods: {len(keys)}")
    print("\nclass in 2024-26 (rows) vs 2021-24 (columns):")
    labs = ["wrong direction", "no edge", "killed by fees", "profitable"]
    print(f"{'':18s}" + "".join(f"{l:>17s}" for l in labs))
    for a in labs:
        print(f"{a:18s}" + "".join(f"{sum(1 for k in keys if cA[k] == a and cB[k] == b):17d}" for b in labs))
    same = sum(1 for k in keys if cA[k] == cB[k])
    print(f"same class in both periods: {same}/{len(keys)} ({same/len(keys)*100:.0f}%)")
    fam = collections.defaultdict(list)
    for k in keys: fam[k[0]].append(k)
    print("\nper setup family (all its variants): share of variants per class in BOTH periods, avg gross / net R per trade 2024-26 -> 2021-24")
    rows = []
    for f, ks in fam.items():
        both = collections.Counter(cA[k] for k in ks if cA[k] == cB[k])
        ga = sum(A[k][0][1] for k in ks) / len(ks); gb = sum(B[k][0][1] for k in ks) / len(ks)
        na = sum(A[k][1][1] for k in ks) / len(ks); nb = sum(B[k][1][1] for k in ks) / len(ks)
        main = both.most_common(1)[0] if both else ("mixed", 0)
        rows.append((f, kind.get(f, "?"), main[0], main[1], len(ks), ga, gb, na, nb))
    order = {"profitable": 0, "killed by fees": 1, "no edge": 2, "wrong direction": 3, "mixed": 4}
    for f, kd, m, cnt, n, ga, gb, na, nb in sorted(rows, key=lambda r: (order[r[2]], -r[3] / r[4])):
        print(f"  {f:26s} [{'limit*' if kd == 'lmt' else 'market'}] {m:15s} ({cnt}/{n} variants in both) | gross {ga:+.3f} -> {gb:+.3f} | net {na:+.3f} -> {nb:+.3f}")
    cands = [k for k in keys if cA[k] == "profitable" and cB[k] == "profitable"]
    print(f"\nprofitable in BOTH periods (gross t>=2 and net>0 in each): {len(cands)} -> {cands if cands else 'none'}")
    fees = [k for k in keys if cA[k] == "killed by fees" and cB[k] == "killed by fees"]
    print(f"killed by fees in BOTH periods (a real but small edge - only cheaper execution could save it): {len(fees)}")
    for k in sorted(fees, key=lambda k: -(A[k][0][1] + B[k][0][1]))[:12]:
        print(f"    {'|'.join(k):58s} gross {A[k][0][1]:+.3f} / {B[k][0][1]:+.3f}  net {A[k][1][1]:+.3f} / {B[k][1][1]:+.3f}  (n {A[k][1][0]} / {B[k][1][0]})")
    print("* limit-entry families: upper bound (fill-order look-ahead in scen.py/scen2.py, same as the AMD bug)")
