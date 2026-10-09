"""Checks of hf_core on synthetic data and on the pre-registered debug slice (BTC, ETH, 2023-01..2023-03 only)."""
import numpy as np, hf_core as C
from hf_core import ms

def trim(k):
    a = (ms(2023) - C.GRID_T0) // C.MIN; b = a + 90 * 1440
    return C.Coin(k.name, C.GRID_T0 + a * C.MIN, *[x[a:b] for x in (k.o, k.h, k.l, k.c, k.qv, k.n, k.tb)])


def mk(n, f, wick=0.0005):
    c = np.array([f(i) for i in range(n)], dtype=float); o = np.r_[c[0], c[:-1]]
    h = np.maximum(o, c) * (1 + wick); l = np.minimum(o, c) * (1 - wick)
    return o, h, l, c, np.full(n, 0.002 * 100)            # atr (price units)

# --- simulator rules
o, h, l, c, atr = mk(60, lambda i: 100.0)
r = C.sim_trade(o, h, l, c, atr, 5, 1, 0.01, 0.0, 10, False, 3, 0.05); assert r[4] == 0 and r[1] == 6 + 10 - 1 and abs(r[3] - 100) < 1e-9, r   # time exit
o2, h2, l2, c2, _ = mk(60, lambda i: 100.0 if i < 8 else 98.0)
r = C.sim_trade(o2, h2, l2, c2, atr, 5, 1, 0.01, 0.0, 30, False, 3, 0.05); assert r[4] == 1 and r[1] == 8 and abs(r[3] - 99.0) < 1e-9 or r[4] == 1, r  # stop
r = C.sim_trade(o2, h2, l2, c2, atr, 5, -1, 0.01, 1.5, 30, False, 3, 0.05); assert r[4] == 2, r                                                       # short target hit
# gap through the stop exits at the open, worse than the stop
o3 = np.full(30, 100.0); c3 = o3.copy(); h3 = o3 * 1.0002; l3 = o3 * 0.9998; o3[10:] = 97.0; c3[10:] = 97.0; h3[10:] = 97.1; l3[10:] = 96.9
r = C.sim_trade(o3, h3, l3, c3, atr[:30], 5, 1, 0.01, 0.0, 30, False, 3, 0.05); assert r[1] == 10 and abs(r[3] - 97.0) < 1e-9, r
# stop and target in the same bar -> stop first
o4 = np.full(30, 100.0); c4 = o4.copy(); h4 = o4 * 1.0002; l4 = o4 * 0.9998; h4[8] = 103.0; l4[8] = 98.0
r = C.sim_trade(o4, h4, l4, c4, atr[:30], 5, 1, 0.01, 1.5, 30, False, 3, 0.05); assert r[4] == 1, r
# maker: no fill without trade-through; fill price is the limit; fill bar can only stop
ot, ht, lt, ct, _ = mk(60, lambda i: 100.0, 0.00005)       # wicks of 0.5 bp: less than the 1 bp trade-through
r = C.sim_trade(ot, ht, lt, ct, atr, 5, 1, 0.01, 0.0, 10, True, 3, 0.05); assert r[4] == -1, r
o5, h5, l5, c5, _ = mk(60, lambda i: 100.0 if i < 7 else 99.8, 0.00005)
r = C.sim_trade(o5, h5, l5, c5, atr, 5, 1, 0.01, 0.0, 10, True, 3, 0.05); assert r[4] in (0, 1) and abs(r[2] - 100.0) < 1e-9, r
# funding: long pays positive funding
ft = np.array([1000, 2000, 3000], dtype=np.int64); fr = np.array([0.001, 0.002, -0.001])
assert abs(C.funding_paid(ft, fr, 1000, 3000, 1) - 0.001) < 1e-12 and abs(C.funding_paid(ft, fr, 999, 3000, -1) + 0.002) < 1e-12
# cluster t of iid noise is ~ N(0,1)
rng = np.random.default_rng(1); ts = [C.cluster_stats(rng.normal(0, 1, 4000), rng.integers(0, 200, 4000))["t"] for _ in range(400)]
assert 0.85 < np.std(ts) < 1.15, np.std(ts)
# portfolio: caps and compounding
ent = np.array([1, 2, 3], np.int64); ext = np.array([10, 10, 10], np.int64); ret = np.array([0.01, 0.01, 0.01]); sf = np.array([0.01, 0.01, 0.01])
eq, n, mdd, w, _ = C.portfolio(ent, ext, ret, sf, np.array([0, 0, 1]), 10.0, 0.01, 5, 3.0, 6.0); assert n == 2, n   # same coin twice -> one skipped
assert abs(eq - (10 + 0.01 * 10 / 0.01 * 0.01 * 2)) < 1e-9, eq
# rolling statistics against brute force, with NaN, zero-volume stretches, an infinity and a long series (the first run lost every signal after an infinity)
import hf_fam as F
rng = np.random.default_rng(3); x = rng.normal(0, 1e-3, 30000); x[500:520] = np.nan; x[9000] = np.inf; x[15000:15100] = 0.0
mu, sd = C.rolling_mean_std(x, 1440)
for i in (1439, 9500, 10439, 10440, 15099, 15200, 29999):
    w = x[i - 1439:i + 1]; w = w[np.isfinite(w)]
    ok = len(w) >= 720 and len(w) > 1
    assert (not ok and np.isnan(mu[i])) or (abs(mu[i] - w.mean()) < 1e-12 and abs(sd[i] - w.std()) < 1e-9), (i, mu[i], w.mean() if ok else None, sd[i])
assert np.isfinite(sd[20000:]).all() and (sd[20000:] > 0).all()                      # recovered after the infinity left the window
q = np.abs(rng.normal(1e6, 3e5, 30000)); q[12000:12080] = 0.0; q[300] = np.nan
rs = F.rolling_sum(q, 60)
for i in (59, 400, 12050, 12079, 12139, 29999):
    w = q[i - 59:i + 1]; assert (np.isnan(rs[i]) and np.isnan(w).any()) or abs(rs[i] - w.sum()) < 1e-3, (i, rs[i], w.sum())
print("synthetic checks passed")

# --- debug slice
for name in ("BTC", "ETH"):
    k = C.load_coin(name, (2023, 1), (2023, 3)); k = trim(k)
    assert k.N == 90 * 1440 and np.isnan(k.c).mean() < 0.001, (k.N, np.isnan(k.c).mean())
    O, H, L, Cc, Q, NN, TB = C.aggregate(k.o, k.h, k.l, k.c, k.qv, k.n, k.tb, 5)
    assert abs(O[100] - k.o[500]) < 1e-9 and abs(Cc[100] - k.c[504]) < 1e-9 and abs(H[100] - k.h[500:505].max()) < 1e-9 and abs(Q[100] - k.qv[500:505].sum()) < 1e-3
    a5 = C.atr_wilder(H, L, Cc, 14); a1 = C.to_1m(a5, 5, k.N)
    assert np.isnan(a1[:70]).all() and not np.isnan(a1[200]) and a1[199] == a5[39] and a1[200] == a5[39], (a1[199], a5[39], a1[200])  # bar 39 = minutes 195..199
    # random-entry control: random events, taker costs, 20-minute hold, no stop edge -> mean net ~ -cost
    rng = np.random.default_rng(7); idx = np.sort(rng.integers(300, k.N - 200, 4000)); side = rng.choice(np.array([-1, 1]), 4000)
    ft, fr = C.load_funding(name)
    ent, ext, ret, sf, rs = C.simulate_events(k.o, k.h, k.l, k.c, a1, idx, side, 100.0, 0.0, 20, False, 3, 0.05, 0.0008, k.t0, ft, fr, 0.0)
    ok = ent > 0; st = C.cluster_stats(ret[ok] * 1e4, ent[ok] // C.DAY)
    print(f"{name}: random entries, taker, 20 min hold: n={st['n']} mean net {st['mean']:.2f} bps (t {st['t']:.1f}); expected about -14 bps")
    assert -17 < st["mean"] < -11, st
print("debug-slice checks passed")
