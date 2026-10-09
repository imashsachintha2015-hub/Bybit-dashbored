"""Re-cost the Kalman trend trades (kal_improve.py, kal_trades.pkl) with REAL funding instead of the flat 0.5 bp / 8h for both sides.
Net R = gross R - 14 bps / stop% - funding paid / stop%  (longs pay positive funding, shorts receive it)."""
import pickle, collections, time
import numpy as np
import funding as FU
from kal_improve import pick, st_, eqs, year, BASE, FEE, FUND_BAR

G = pickle.load(open("kal_trades.pkl", "rb"))
def recost(rows):
    out = []; real = 0
    for r in rows:
        rp = (FEE + FUND_BAR * r["bars"]) / (r["Rg"] - r["R"])
        f, ok = FU.paid(r["sym"], r["side"], r["te"], r["tx"]); real += ok
        out.append(dict(r, R=r["Rg"] - FEE / rp - f / rp, fund_R=f / rp))
    return out, real
for lab, var in (("Kalman baseline", BASE), ("Kalman + BTC filter", (1e-4, 1.0, "z", "btc"))):
    x = pick(G, var); y, real = recost(x)
    print(f"\n=== {lab}: flat funding model vs REAL funding (real data covers {real}/{len(y)} trades) ===")
    print(f"  {'':22s} " + " ".join(f"{yy:>15d}" for yy in range(2020, 2027)) + f" {'all':>16s}")
    for side, nm in (("L", "longs"), ("S", "shorts"), (None, "all")):
        for mdl, rows in (("flat", x), ("real", y)):
            sel = [r for r in rows if side is None or r["side"] == side]
            cells = []
            for yy in range(2020, 2027):
                v = [r["R"] for r in sel if year(r["t"]) == yy]; cells.append(f"{np.mean(v):+.3f} ({len(v):4d})" if v else "-")
            print(f"  {nm:7s} {mdl:5s} funding " + " ".join(f"{c_:>15s}" for c_ in cells) + f" {np.mean([r['R'] for r in sel]):+.3f} ({len(sel)})")
    fl = [r["fund_R"] for r in y if r["side"] == "L"]; fs = [r["fund_R"] for r in y if r["side"] == "S"]
    print(f"  average funding per trade in R: longs pay {np.mean(fl):+.3f}R, shorts pay {np.mean(fs):+.3f}R (negative = received)")
    for risk, mx in ((0.005, 8),):
        print(f"  $10 at {risk*100:.1f}% risk, max {mx} open, 2020-2026: flat model {eqs(x, risk, mx)} | real funding {eqs(y, risk, mx)}")
