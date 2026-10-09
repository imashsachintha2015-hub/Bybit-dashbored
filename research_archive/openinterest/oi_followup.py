"""Exploratory follow-up on the one filter that passed (L_OI_HIGH: longs only when open interest is above its 30-day mean).
Not part of the pre-registered rule: how much of the separation comes from outliers, per calendar year, and win rates."""
import pickle, math, collections
import numpy as np
import funding as FU, oi_feat as OF
from ls_improve import trades, year
from inv_run import seg
DAY = 86400000

def mse(pairs):
    """n, mean, day-clustered standard error (same as oi_test.mse / prereg_run.tstat)"""
    d = collections.defaultdict(list)
    for t, r in pairs: d[t // DAY].append(r)
    n = sum(len(v) for v in d.values())
    if n < 2: return n, float("nan"), float("nan")
    m = sum(x for v in d.values() for x in v) / n
    return n, m, math.sqrt(sum((sum(v) - m * len(v)) ** 2 for v in d.values())) / n

E = pickle.load(open("ls_entries.pkl", "rb"))
for r in E:
    r["favg"] = None; ft, fcum = FU.series(r["sym"])
    if ft:
        b = int(np.searchsorted(ft, r["t"] + 4 * 3600000, side="right"))
        if b >= 9: r["favg"] = (fcum[b] - fcum[b - 9]) / 9
E2 = [r for r in E if not (r["side"] == "L" and r["favg"] is not None and r["favg"] > 0.0003)]
final = trades(E2, {"SHORT_FUND"})
F = OF.feats
HOLD = {"VAL", "FINAL", "UNSEEN", "OLD"}
rows = []
for te, sym, R, tx, t0, sd in final:
    if sd != "L": continue
    v = F(sym, t0)["OI30"]
    if v is None: continue
    rows.append(dict(t=t0, R=R, keep=v > 0, seg=seg({"t": t0, "sym": sym}), y=year(t0), sym=sym))

def desc(lab, rs):
    k = [r for r in rs if r["keep"]]; x = [r for r in rs if not r["keep"]]
    def one(g):
        if not g: return "    -"
        R = sorted([r["R"] for r in g], reverse=True); cut = max(1, len(R) // 100)
        return (f"n {len(g):4d}  mean {np.mean(R):+.3f}  median {np.median(R):+.3f}  win {np.mean([r > 0 for r in R]) * 100:3.0f}%  "
                f"without best 1% {np.mean(R[cut:]):+.3f}")
    nk, mk, sk = mse([(r["t"], r["R"]) for r in k]); nx, mx, sx = mse([(r["t"], r["R"]) for r in x])
    t = (mk - mx) / math.sqrt(sk ** 2 + sx ** 2) if nk > 1 and nx > 1 else float("nan")
    print(f"  {lab}\n     kept    {one(k)}\n     removed {one(x)}\n     separation t = {t:+.2f}")

print("=== L_OI_HIGH, exploratory ===")
desc("pooled holdouts (the tested set)", [r for r in rows if r["seg"] in HOLD])
desc("DEV (design coins 2021-12 .. 2023-12)", [r for r in rows if r["seg"] == "DEV"])
desc("everything with data", rows)
# outlier dependence of the separation itself: drop the best 1% of each group
hk = sorted([r["R"] for r in rows if r["seg"] in HOLD and r["keep"]], reverse=True); hx = sorted([r["R"] for r in rows if r["seg"] in HOLD and not r["keep"]], reverse=True)
for drop in (0, 1, 2, 5):
    a = hk[int(len(hk) * drop / 100):]; b = hx[int(len(hx) * drop / 100):]
    print(f"  holdouts without the best {drop}% of each group: kept {np.mean(a):+.3f} vs removed {np.mean(b):+.3f} (gap {np.mean(a) - np.mean(b):+.3f}R)")
print("\n  by calendar year (all coins with data): kept vs removed, mean net R (n)")
for y in sorted({r["y"] for r in rows}):
    g = [r for r in rows if r["y"] == y]; k = [r["R"] for r in g if r["keep"]]; x = [r["R"] for r in g if not r["keep"]]
    print(f"    {y}: kept {np.mean(k) if k else float('nan'):+.3f} ({len(k):3d}) | removed {np.mean(x) if x else float('nan'):+.3f} ({len(x):3d})")
