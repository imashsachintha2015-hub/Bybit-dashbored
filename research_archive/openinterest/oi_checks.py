"""Checks required by PREREG_openinterest.md before any result is read (no trade outcome is printed here):
1. timestamps: implied price (OI value / OI, last snapshot of each hour) must track OKX's 1H close best at lag 0
2. no look-ahead: features recomputed after deleting, or scrambling, every hourly row past the cut-off must be identical
3. coverage: final-system signals with each feature available, per side and segment"""
import os, pickle, random, collections, time
import numpy as np
import funding as FU, tl_core as T, oi_feat as OF
from ls_improve import trades
from inv_run import seg
H = 3600000

print("=== 1. timestamp alignment (implied price of the hour's last snapshot vs OKX 1H close of the bar starting at hour + lag) ===")
for sym in ("BTC", "ETH", "SOL", "XRP", "DOGE"):
    z = np.load(os.path.join("oi", f"{sym}.npz"), allow_pickle=False)
    mins = (z["last"] - z["hour"]) / 60000.0
    pb = dict(zip(z["hour"].tolist(), (z["oiv"] / z["oi"]).tolist()))
    ok = {int(r[0]): r[4] for r in T.series_1h(sym)}
    row = []
    for L in (-2, -1, 0, 1, 2):
        hs = [h for h in pb if h + L * H in ok and h - H in pb and h - H + L * H in ok and pb[h] > 0 and pb[h - H] > 0]
        d = np.array([abs(np.log(pb[h] / ok[h + L * H])) for h in hs])
        ra = np.array([np.log(pb[h] / pb[h - H]) for h in hs]); rb = np.array([np.log(ok[h + L * H] / ok[h - H + L * H]) for h in hs])
        row.append(f"lag {L:+d}h: median |gap| {np.median(d) * 1e4:6.1f} bp, return corr {np.corrcoef(ra, rb)[0, 1]:.3f}")
    print(f"  {sym:4s} last snapshot at minute {np.median(mins):.0f} of the hour (5%-95%: {np.percentile(mins, 5):.0f}-{np.percentile(mins, 95):.0f}), {len(hs)} hours")
    for x in row: print("       " + x)

E = pickle.load(open("ls_entries.pkl", "rb"))
for r in E:
    r["favg"] = None; ft, fcum = FU.series(r["sym"])
    if ft:
        b = int(np.searchsorted(ft, r["t"] + 4 * 3600000, side="right"))
        if b >= 9: r["favg"] = (fcum[b] - fcum[b - 9]) / 9
E2 = [r for r in E if not (r["side"] == "L" and r["favg"] is not None and r["favg"] > 0.0003)]
final = trades(E2, {"SHORT_FUND"})

print("\n=== 2. no look-ahead: 400 random final-system signals with data ===")
random.seed(7); cand = [x for x in final if OF.grid(x[1]) is not None]; same_cut = same_scr = n_feat = 0; sample = random.sample(cand, min(400, len(cand)))
for te, sym, R, tx, t0, sd in sample:
    G = OF.grid(sym); full = OF.feats(sym, t0, G); k = OF.cut_index(G, t0)
    if k < 0 or k >= G["n"]: same_cut += 1; same_scr += 1; continue
    cut = dict(g0=G["g0"], n=k + 1, **{a: G[a][:k + 1].copy() for a in ("oi", "oiv", "ls", "tk")})
    scr = dict(g0=G["g0"], n=G["n"], **{a: G[a].copy() for a in ("oi", "oiv", "ls", "tk")})
    for a in ("oi", "oiv", "ls"): scr[a][k + 1:] = np.exp(np.random.randn(G["n"] - k - 1)) * 1e3
    scr["tk"][k + 1:] = np.random.randn(G["n"] - k - 1)
    same_cut += OF.feats(sym, t0, cut) == full; same_scr += OF.feats(sym, t0, scr) == full; n_feat += sum(v is not None for v in full.values())
print(f"  identical after deleting later rows: {same_cut}/{len(sample)} | identical after scrambling later rows: {same_scr}/{len(sample)} "
      f"| features defined in the sample: {n_feat}/{4 * len(sample)}")

print("\n=== 3. coverage: final-system signals (before the 8-position limit) with each feature, by side and segment ===")
cov = collections.defaultdict(collections.Counter)
for te, sym, R, tx, t0, sd in final:
    sg = seg({"t": t0, "sym": sym}) or "pre-2020"; f = OF.feats(sym, t0); key = (sd, sg)
    cov[key]["all"] += 1
    for nm in OF.NAMES: cov[key][nm] += f[nm] is not None
print(f"  {'side':5s} {'segment':8s} {'signals':>8s} " + " ".join(f"{nm:>6s}" for nm in OF.NAMES))
for sd in ("S", "L"):
    for sg in ("DEV", "VAL", "FINAL", "UNSEEN", "OLD"):
        c = cov[(sd, sg)]; print(f"  {sd:5s} {sg:8s} {c['all']:8d} " + " ".join(f"{c[nm]:6d}" for nm in OF.NAMES))
