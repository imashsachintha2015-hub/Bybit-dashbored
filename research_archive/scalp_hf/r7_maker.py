"""Round 7 follow-up (PREREG_round7.md, exploratory, never counted as a pass): the frozen 30-minute cell with a maker entry.
Limit order at the signal-bar close P, alive for 3 one-minute bars; fills only if price trades through P by max(0.05 ATR(5m), 1 bp);
maker fee 2 bps on entry, taker 7 bps on exit; unfilled signals are skipped; the exit (stop level, panel/slow-trend exit) is unchanged."""
import os, json, pickle
import numpy as np
import hf_core as C
import hf_ladder as LD
import r4
import r7_sens as R7

fz = json.load(open(os.path.join(R7.HERE, "r7_frozen.json")))["30"]; th, K, X = fz
btc = C.load_coin("BTC"); td = LD.btc_daily_trend(btc)
sets = {"J1 design FINAL": ("design", R7.T_J1), "J2 UNSEEN": ("unseen", (0, 2 ** 62)), "J3 HOLDOUT2": ("holdout2", (0, 2 ** 62))}
res = {}
for group in ("design", "unseen", "holdout2"):
    parts = []
    for cid, nm in enumerate(r4.GROUPS[group]):
        co = btc if nm == "BTC" else C.load_coin(nm); ft, fr = C.load_funding(nm)
        t, O, H, L, Cc, Q, TB, valid, a = R7.prep(co, 30)
        day = np.clip(((t + 30 * 60000) // C.DAY) - 1 - co.t0 // C.DAY, 0, len(td) - 1)
        zs = R7.estimator_z(O, H, L, Cc, Q, TB); S = R7.votes(zs, th); zslow = R7.R6.kalman_z_p(Cc, 1e-4)
        e, x, r, s, rs, sd, ep, xp, fv = R7.run_s(t, O, H, L, Cc, S, a, valid, td[day], ft, fr, 30, K, X, zslow)
        O5, H5, L5, C5, *_ = C.aggregate(co.o, co.h, co.l, co.c, co.qv, co.n, co.tb, 5); a5 = C.atr_wilder(H5, L5, C5, 14)
        k1 = (e - co.t0) // 60000                                             # 1m index of the entry bar open = signal close
        P = co.c[k1 - 1]; at = a5[np.clip(k1 // 5 - 1, 0, None)]
        pen = np.maximum(0.05 * at, 1e-4 * P)
        filled = np.zeros(len(e), bool)
        for q in range(3):
            lo = co.l[np.clip(k1 + q, 0, co.N - 1)]; hi = co.h[np.clip(k1 + q, 0, co.N - 1)]
            filled |= np.where(sd == 1, lo <= P - pen, hi >= P + pen)
        gross = sd * (xp / P - 1.0)
        net = gross - 0.0002 - 0.0007 - fv
        m = filled & np.isfinite(net)
        parts.append((e[m], x[m], net[m], s[m], np.full(m.sum(), cid), len(e), m.sum(), r[~m], r[m]))
    res[group] = parts
print(f"# MAKER follow-up of the frozen 30-minute cell (THETA {th}, K {K}, X {X})")
for nm, (g, w) in sets.items():
    p = res[g]; e = np.concatenate([q[0] for q in p]); x = np.concatenate([q[1] for q in p]); r = np.concatenate([q[2] for q in p])
    s = np.concatenate([q[3] for q in p]); cid = np.concatenate([q[4] for q in p])
    sig = sum(q[5] for q in p); fl = sum(q[6] for q in p)
    allr = np.concatenate([q[7] for q in p]); fr_ = np.concatenate([q[8] for q in p])
    m = R7.inwin(e, w); st = R7.cl(r[m] / s[m], e[m]); eq, tk, dd = R7.money(e[m], x[m], r[m], s[m], cid[m])
    print(f"  {nm:16s} signals {sig:5d} filled {fl} ({fl / sig * 100:.0f}%)  maker net R {st['mean']:+.3f} (t {st['t']:+.2f}, n {st['n']})  $10 -> {eq:7.2f} ({tk} taken, DD {dd * 100:.0f}%)"
          f" | taker result of the filled trades {np.mean(fr_):+.4f}, of the missed trades {np.mean(allr):+.4f} (return per trade)")
