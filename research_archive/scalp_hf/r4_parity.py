"""Round 4 parity: each candidate's base rule, re-simulated by r4.py, against the round-2 / round-3 tables (DEV, design coins)."""
import pickle, os
import numpy as np
import hf_core as C
import hf2_eval as E2
import r3_common as R3
import r4

evs = pickle.load(open(os.path.join(r4.OUT, "events_design.pkl"), "rb")); mk = r4.Market("design")
ev2 = E2.load(C.DESIGN); cells = E2.Cells(ev2)
r3liq = R3.load("LIQ", "design"); r3td = R3.load("TRENDDAY", "design")
X = {"LVL": 2, "TRAIL": 3}
for cname, kind, sid, side, k, own in r4.CANDIDATES:
    tr = r4.run_rule(mk, evs[cname], own, r4.BASE); a = r4.segstats(tr, "DEV")
    if kind == "r2":
        idx, R, net = cells.trades(sid, side, k, "ALL", "NONE", X[own]); m = C.seg_mask(ev2["ent"][idx], "DEV")
        st = C.cluster_stats(R[m], ev2["ent"][idx][m] // C.DAY); ref = "round 2 cell"
    else:
        d = r3liq["MAGNET|S|HL3d|R4|T1R"] if kind == "magnet" else r3td["TRENDDAY|L|T08|k0.5|daylow"]
        st = R3.stats(d, "DEV"); ref = "round 3 cell"
    print(f"{cname:13s} r4: n {a['n']:6d} R {a['mean']:+.4f} | {ref}: n {st['n']:6d} R {st['mean']:+.4f}")
