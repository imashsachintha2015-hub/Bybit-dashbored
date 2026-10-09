"""DEV-only diagnosis after the round-2 screen: is there an edge BEFORE costs, and how does it depend on the stop width?
gross R = side x price move / stop distance (no fees, no funding). Regime ALL, filter NONE, one position per coin per cell."""
import numpy as np
import hf_core as C
import hf2_setups as S
import hf2_eval as E

ev = E.load(C.DESIGN); cells = E.Cells(ev)
print("# DEV gross R (before costs) and cost in R per setup x timeframe, both sides pooled; exit R15 / LVL / TRAIL")
print(f"{'setup':12s} {'tf':>3s} {'n':>6s} {'stop%':>6s} {'cost R':>7s} | {'gross R15':>9s} {'t':>5s} | {'gross LVL':>9s} {'t':>5s} | {'grossTRAIL':>10s} {'t':>5s} | {'gross bps R15':>13s}")
rows = []
for sid in range(26):
    for tf in (5, 15):
        out = []; n = 0
        for x in (0, 2, 3):
            I = []; G = []
            for side in (1, -1):
                idx, R, net = cells.trades(sid, side, tf, "ALL", "NONE", x)
                m = C.seg_mask(ev["ent"][idx], "DEV"); I.append(idx[m]); G.append(ev["gr"][idx[m], x])
            idx = np.concatenate(I); g = np.concatenate(G)
            gR = g / ev["sf"][idx]
            st = C.cluster_stats(gR, ev["ent"][idx] // C.DAY)
            out.append((st["mean"], st["t"], np.mean(g) * 1e4, np.median(ev["sf"][idx]) * 100, np.mean(E.COST / ev["sf"][idx]), len(idx)))
        rows.append((sid, tf, out))
        o0, o2, o3 = out
        print(f"{S.SETUPS[sid]:12s} {tf:3d} {o0[5]:6d} {o0[3]:6.2f} {-o0[4]:+7.3f} | {o0[0]:+9.3f} {o0[1]:+5.1f} | {o2[0]:+9.3f} {o2[1]:+5.1f} | {o3[0]:+10.3f} {o3[1]:+5.1f} | {o0[2]:+13.1f}")

print("\n# DEV, all 25 setups pooled (one position per coin per cell), exit LVL: net and gross R by stop width")
I = []; 
for sid in range(25):
    for side in (1, -1):
        for tf in (5, 15):
            idx, R, net = cells.trades(sid, side, tf, "ALL", "NONE", 2)
            I.append(idx[C.seg_mask(ev["ent"][idx], "DEV")])
idx = np.concatenate(I)
ridx = np.concatenate([cells.trades(25, s, tf, "ALL", "NONE", 2)[0] for s in (1, -1) for tf in (5, 15)])
ridx = ridx[C.seg_mask(ev["ent"][ridx], "DEV")]
for lo, hi in ((0.0015, 0.003), (0.003, 0.006), (0.006, 0.012), (0.012, 0.0401)):
    for lab, ii in (("setups", idx), ("random", ridx)):
        m = (ev["sf"][ii] >= lo) & (ev["sf"][ii] < hi); j = ii[m]
        if len(j) < 10: continue
        g = ev["gr"][j, 2] / ev["sf"][j]; nt = (ev["gr"][j, 2] - E.COST - ev["fu"][j, 2]) / ev["sf"][j]
        st = C.cluster_stats(g, ev["ent"][j] // C.DAY)
        print(f"  stop {lo * 100:.2f}-{hi * 100:.2f}%  {lab:7s} n {len(j):7d}  gross R {st['mean']:+.3f} (t {st['t']:+.1f})  net R {nt.mean():+.3f}")
