"""Data-created situations: cluster 1H market states (k-means fitted on DEV design data only), then test whether any
cluster is a profitable situation out of sample. Trade per sample: market at next open, stop 1.5 ATR, target 2R, 48h max,
14 bps + funding. Same coin split / time split as scen_an.py."""
import io, contextlib, os, sys, math, random, collections, pickle
import numpy as np
from sklearn.cluster import KMeans
os.environ.setdefault("NO_AMD", "1")
with contextlib.redirect_stdout(io.StringIO()):
    exec(open("scen_an.py").read())          # DESIGN, UNSEEN, T1, T2, tstat, ...
import scen
STEP = 4; K = int(os.environ.get("K", 30)); HOLDC = 48
FEATS = ["r4", "r24", "r96", "pos96", "d_ema50", "d_ema200", "vol_regime", "vrel", "close_loc", "body", "tr1d", "btc1d", "btc_r24", "hour_sin", "hour_cos"]

def series_feats(sym, inv, btc_arr):
    t, o, h, l, c, v = scen.load(sym, inv); n = len(t)
    tr = [h[0] - l[0]] + [max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])) for i in range(1, n)]
    atr = scen.ema(tr, 27); atrl = scen.ema(tr, 500); e50 = scen.ema(c, 50); e200 = scen.ema(c, 200)
    tr1d, _ = scen.daily_trend(t, o, h, l, c)
    cv = np.concatenate([[0.0], np.cumsum(v)])
    bt_idx = {x: k for k, x in enumerate(btc_arr["t"])}
    rows = []
    for i in range(500, n - HOLDC - 2, STEP):
        a = atr[i]
        if a <= 0: continue
        hi = max(h[i - 96:i + 1]); lo = min(l[i - 96:i + 1]); rng = max(h[i] - l[i], 1e-12)
        k = bt_idx.get(t[i])
        if k is None or k < 30: continue
        bc, batr = btc_arr["c"], btc_arr["atr"]
        hr = (t[i] // 3600000) % 24
        f = [(c[i] - c[i - 4]) / a, (c[i] - c[i - 24]) / a, (c[i] - c[i - 96]) / a, (c[i] - lo) / max(hi - lo, 1e-12),
             (c[i] - e50[i]) / a, (c[i] - e200[i]) / a, atr[i] / max(atrl[i], 1e-12), v[i] / max((cv[i] - cv[i - 24]) / 24, 1e-12),
             (c[i] - l[i]) / rng, (c[i] - o[i]) / a, tr1d[i], btc_arr["tr1d"][k], (bc[k] - bc[k - 24]) / max(batr[k], 1e-12),
             math.sin(2 * math.pi * hr / 24), math.cos(2 * math.pi * hr / 24)]
        # outcome: market long at next open, stop 1.5 ATR, target 2R, 48h
        e = i + 1; ep = o[e]; risk = 1.5 * a; stop = ep - risk; tp = ep + 2 * risk; R = None
        for j in range(e, e + HOLDC):
            if l[j] <= stop: R = -1.0; break
            if h[j] >= tp: R = 2.0; break
        if R is None: j = e + HOLDC - 1; R = (c[j] - ep) / risk
        rp = risk / abs(ep); hours = j - e + 1
        net = R - (14e-4 + 0.5e-4 * hours / 8) / rp
        rows.append((sym, "S" if inv else "L", t[i], f, net, rp, t[e], t[j] + 3600000))
    return rows

def btc_arrays(inv):
    t, o, h, l, c, v = scen.load("BTC", inv)
    tr = [h[0] - l[0]] + [max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])) for i in range(1, len(t))]
    tr1d, _ = scen.daily_trend(t, o, h, l, c)
    return dict(t=t, c=c, atr=scen.ema(tr, 27), tr1d=tr1d)

if __name__ == "__main__":
    from multiprocessing import Pool
    bL, bS = btc_arrays(False), btc_arrays(True)
    with Pool(4) as p:
        parts = p.starmap(series_feats, [(s, inv, bS if inv else bL) for s in COINS for inv in (False, True)])
    rows = [r for P_ in parts for r in P_]
    X = np.array([r[3] for r in rows], dtype=float); X = np.clip(X, -50, 50)
    seg_of = np.array(["dev" if (r[0] in DESIGN and r[2] < T1) else "val" if (r[0] in DESIGN and r[2] < T2) else "final" if r[0] in DESIGN else "unseen" for r in rows])
    dev = seg_of == "dev"
    mu = X[dev].mean(0); sd = X[dev].std(0) + 1e-9; Z = (X - mu) / sd
    km = KMeans(n_clusters=K, n_init=4, random_state=0).fit(Z[dev]); lab = km.predict(Z)
    net = np.array([r[4] for r in rows]); ts = np.array([r[2] for r in rows])
    print(f"samples {len(rows)} (every {STEP}th 1H bar, both sides); K={K} clusters fitted on DEV design data only")
    print(f"unconditional net R per sample: dev {net[dev].mean():+.3f}")
    stats = []
    for k in range(K):
        m = (lab == k)
        d = tstat(list(zip(ts[m & dev], net[m & dev]))); v_ = tstat(list(zip(ts[m & (seg_of == 'val')], net[m & (seg_of == 'val')])))
        stats.append((k, d, v_))
    stats.sort(key=lambda s: -s[1][2])
    def describe(k):
        cz = km.cluster_centers_[k] * sd + mu
        return ", ".join(f"{nm}={cz[j]:+.2f}" for j, nm in enumerate(FEATS) if abs(km.cluster_centers_[k][j]) > 0.8) or "near average"
    print(f"\n{'cluster':7s} {'DEV n':>6s} {'avgR':>7s} {'t':>5s} | {'VAL n':>6s} {'avgR':>7s} {'t':>5s} | what the situation looks like (features > 0.8 sd from average)")
    for k, d, v_ in stats[:8] + stats[-3:]:
        print(f"{k:7d} {d[0]:6d} {d[1]:+7.3f} {d[2]:+5.1f} | {v_[0]:6d} {v_[1]:+7.3f} {v_[2]:+5.1f} | {describe(k)}")
    frozen = [s for s in stats[:3] if s[1][1] > 0 and s[2][1] > 0]
    print(f"\nfrozen (top-3 by DEV t, positive on VAL): {[s[0] for s in frozen]}  -- {K} clusters tried")
    for k, d, v_ in frozen:
        m = lab == k
        for segn in ("final", "unseen"):
            mm = m & (seg_of == segn); n_, mean_, t_ = tstat(list(zip(ts[mm], net[mm])))
            # $10, 2% risk, max 3 open, one per coin
            tr_ = sorted((rows[i][6], rows[i][0] + rows[i][1], net[i], rows[i][7]) for i in np.where(mm)[0])
            eq = 10.0; openp = []; pk = 10.0; dd = 0.0; taken = 0
            for te, sym, R, tx in tr_:
                for p_ in sorted([p_ for p_ in openp if p_[0] <= te]): openp.remove(p_); eq += p_[1]; pk = max(pk, eq); dd = min(dd, eq / pk - 1)
                if len(openp) >= 3 or any(p_[2] == sym for p_ in openp) or eq < 1: continue
                openp.append((tx, eq * 0.02 * R, sym)); taken += 1
            for p_ in openp: eq += p_[1]
            print(f"  cluster {k} {segn:6s}: n={n_:5d} avgR={mean_:+.3f} t={t_:+.1f} | $10 -> ${eq:.2f} ({taken} taken, max DD {dd*100:.0f}%)")
    pickle.dump(dict(km=km, mu=mu, sd=sd, stats=stats), open("scen_cluster.pkl", "wb"))
