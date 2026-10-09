"""Round 6 follow-up (exploratory, after the judges): the production-setting Kalman trend at each bar size on the fresh coins (J3) and the
unseen coins (J2) if the round-trip cost were lower. Same trades, same fills, cost refunded in full: optimistic (no adverse selection)."""
import os, pickle
import numpy as np
import hf_core as C
import r6_fast as R6

d = {g: pickle.load(open(os.path.join(R6.OUT, f"r6_{g}.pkl"), "rb")) for g in ("unseen", "holdout2")}
print("# $10 (0.5% risk, 8 open) / net R on J3 fresh coins 2021-2026 (J2 unseen in brackets) for the reference setting, by round-trip cost")
print(f"  {'bar':>5s} {'trades/day':>10s} | " + " | ".join(f"{c:>2d} bps RT" + " " * 16 for c in (14, 10, 6, 3)))
for B in (15, 30, 60, 120, 240):
    if B == 240:
        import hf_ladder as LD
        continue
    e, x, r, s, cid = d["holdout2"][(B, R6.REF)]; e2, x2, r2, s2, c2 = d["unseen"][(B, R6.REF)]
    days = (e.max() - e.min()) / C.DAY
    cells = []
    for rt in (14, 10, 6, 3):
        add = (14 - rt) / 1e4
        out = []
        for (ee, xx, rr, ss, cc) in ((e, x, r, s, cid), (e2, x2, r2, s2, c2)):
            rr2 = rr + add; R = rr2 / ss
            eq, tk, dd = R6.money(ee, xx, rr2, ss, cc)
            out.append((R.mean(), eq, dd))
        cells.append(f"R {out[0][0]:+.3f} ${out[0][1]:6.2f} DD{out[0][2] * 100:3.0f}% (${out[1][1]:5.2f})")
    print(f"  {B:3d}m {len(e) / days:10.1f} | " + " | ".join(cells))
