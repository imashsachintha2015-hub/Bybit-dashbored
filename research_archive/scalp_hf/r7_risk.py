"""Round 7 follow-up (exploratory): the frozen 30-minute cell and the plain 30-minute Kalman at different risk per trade."""
import os, pickle, json
import numpy as np
import hf_core as C
import r6_fast as R6
import r7_sens as R7

fz = json.load(open(os.path.join(R7.HERE, "r7_frozen.json")))["30"]; key = (30, fz[0], fz[1], fz[2])
sets = {"J1 design FINAL": ("design", R7.T_J1), "J2 UNSEEN": ("unseen", (0, 2 ** 62)), "J3 HOLDOUT2": ("holdout2", (0, 2 ** 62))}
d7 = {g: pickle.load(open(os.path.join(R7.OUT, f"r7_{g}.pkl"), "rb")) for g in ("design", "unseen", "holdout2")}
d6 = {g: pickle.load(open(os.path.join(R6.OUT, f"r6_{g}.pkl"), "rb")) for g in ("design", "unseen", "holdout2")}
print("# $10 (max drawdown) by risk per trade; 8 open at most")
print(f"  {'system':34s} {'risk':>5s} | " + " | ".join(f"{n:>22s}" for n in sets))
for lab, d, k in (("panel + slow exit (frozen 30m)", d7, key), ("plain Kalman 30m (production setting)", d6, (30, R6.REF))):
    for risk in (0.0015, 0.0025, 0.005):
        cells = []
        for nm, (g, w) in sets.items():
            e, x, r, s, cid = d[g][k]; m = R7.inwin(e, w)
            eq, tk, dd = R7.money(e[m], x[m], r[m], s[m], cid[m], risk)
            cells.append(f"${eq:7.2f} ({tk:4d} tr, DD {dd * 100:3.0f}%)")
        print(f"  {lab:34s} {risk * 100:4.2f}% | " + " | ".join(f"{c:>22s}" for c in cells))
e, x, r, s, cid = d7["holdout2"][key]
mo = (e.max() - e.min()) / (C.DAY * 30.44)
print(f"\n  trades per month (fresh coins, 10 coins): {len(e) / mo:.0f}; with 30 coins about {3 * len(e) / mo:.0f}; median hold {np.median((x - e) / 3.6e6):.1f} h; win rate {np.mean(r > 0) * 100:.0f}%")
