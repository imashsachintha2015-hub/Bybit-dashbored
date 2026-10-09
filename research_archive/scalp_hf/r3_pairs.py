"""Round 3, idea B (PREREG_round3.md): statistical arbitrage on the 45 pairs of a coin group, 1H closes.
Note written before running: while a trade is open its exit z uses the hedge ratio it was entered with (the position is fixed);
entries use today's ratio. usage: python3 r3_pairs.py design|unseen"""
import sys, itertools
import numpy as np
import numba as nb
import hf_core as C
import r3_common as R3


@nb.njit(cache=True)
def daily_beta(la, lb, nd):
    beta = np.full(nd, np.nan); hl = np.full(nd, np.nan)
    for d in range(30, nd):
        a0 = (d - 30) * 24; a1 = d * 24
        sx = 0.0; sy = 0.0; sxx = 0.0; sxy = 0.0; m = 0
        for t in range(a0, a1):
            if np.isfinite(la[t]) and np.isfinite(lb[t]):
                sx += lb[t]; sy += la[t]; sxx += lb[t] * lb[t]; sxy += lb[t] * la[t]; m += 1
        if m < 500: continue
        vx = sxx / m - (sx / m) ** 2
        if vx <= 0: continue
        b = (sxy / m - sx / m * sy / m) / vx
        beta[d] = b
        # AR(1) of the spread: ds_t = c + g s_{t-1}
        px = 0.0; py = 0.0; pxx = 0.0; pxy = 0.0; k = 0
        for t in range(a0 + 1, a1):
            if np.isfinite(la[t]) and np.isfinite(lb[t]) and np.isfinite(la[t - 1]) and np.isfinite(lb[t - 1]):
                s0 = la[t - 1] - b * lb[t - 1]; s1 = la[t] - b * lb[t]
                px += s0; py += s1 - s0; pxx += s0 * s0; pxy += s0 * (s1 - s0); k += 1
        if k > 100:
            v = pxx / k - (px / k) ** 2
            if v > 0:
                g = (pxy / k - px / k * py / k) / v
                phi = 1 + g
                if 0 < phi < 1: hl[d] = -np.log(2) / np.log(phi)
    return beta, hl


@nb.njit(cache=True)
def zscore(la, lb, h, b, Z):
    s = 0.0; s2 = 0.0; m = 0
    for t in range(h - Z + 1, h + 1):
        if np.isfinite(la[t]) and np.isfinite(lb[t]):
            v = la[t] - b * lb[t]; s += v; s2 += v * v; m += 1
    if m < 0.9 * Z or not (np.isfinite(la[h]) and np.isfinite(lb[h])): return np.nan, np.nan
    mu = s / m; var = s2 / m - mu * mu
    if var <= 0: return np.nan, np.nan
    sd = np.sqrt(var)
    return (la[h] - b * lb[h] - mu) / sd, sd


@nb.njit(cache=True)
def run_pair(la, lb, oa, ob, beta, hl, Z, k, use_hl, t0, fta, fra, ftb, frb):
    """oa/ob: 1m opens at each hour start (index h = open of hour h). returns ent, ext, net, sf"""
    n = len(la); cap = 5000
    ent = np.zeros(cap, np.int64); ext = np.zeros(cap, np.int64); net = np.zeros(cap); sf = np.zeros(cap); q = 0
    zprev = np.nan; h = Z + 30 * 24
    while h < n - 2:
        d = h // 24; b = beta[d]
        if not np.isfinite(b): zprev = np.nan; h += 1; continue
        z, sd = zscore(la, lb, h, b, Z)
        side = 0
        if np.isfinite(z) and np.isfinite(zprev):
            if zprev < k <= z: side = -1
            elif zprev > -k >= z: side = 1
        if side != 0 and use_hl and not (np.isfinite(hl[d]) and 2 <= hl[d] <= 72): side = 0
        zprev = z
        if side == 0: h += 1; continue
        e = h + 1
        if not (np.isfinite(oa[e]) and np.isfinite(ob[e])): h += 1; continue
        wa = 1.0 / (1 + abs(b)); wb = -b / (1 + abs(b))
        x = -1
        for hh in range(e, min(e + 72, n - 1)):
            zz, _ = zscore(la, lb, hh, b, Z)
            if not np.isfinite(zz): continue
            if side * zz >= 0 or side * zz <= -(k + 2): x = hh + 1; break
        if x < 0: x = min(e + 72, n - 1)
        while x > e and not (np.isfinite(oa[x]) and np.isfinite(ob[x])): x -= 1
        if x <= e: h += 1; continue
        te = t0 + e * 3600000; tx = t0 + x * 3600000
        gross = side * (wa * (oa[x] / oa[e] - 1.0) + wb * (ob[x] / ob[e] - 1.0))
        fund = abs(wa) * C.funding_paid(fta, fra, te, tx, side) + abs(wb) * C.funding_paid(ftb, frb, te, tx, side * (1 if wb > 0 else -1))
        if q < cap:
            ent[q] = te; ext[q] = tx; net[q] = gross - R3.COST - fund; sf[q] = max(2 * sd / (1 + abs(b)), 0.003); q += 1
        h = x                                                                    # one trade per pair at a time
        zprev = np.nan
    return ent[:q], ext[:q], net[:q], sf[:q]


def hourly(co):
    O, H, L, Cc, *_ = C.aggregate(co.o, co.h, co.l, co.c, co.qv, co.n, co.tb, 60)
    with np.errstate(invalid="ignore", divide="ignore"): la = np.log(Cc)
    oo = co.o[::60][:len(Cc)]                                                     # 1m open at each hour start
    return la, oo


def main(which, names=None, first=None, last=None):
    names = names or (C.DESIGN if which == "design" else C.UNSEEN)
    data = {}
    for nm in names:
        co = C.load_coin(nm, **({} if first is None else dict(first=first, last=last)))
        data[nm] = hourly(co) + C.load_funding(nm)
    tab = R3.Table()
    for pid, (a, b) in enumerate(itertools.combinations(names, 2)):
        la, oa, fta, fra = data[a]; lb, ob, ftb, frb = data[b]
        nd = len(la) // 24
        beta, hl = daily_beta(la, lb, nd)
        for Z, k, flt in itertools.product((72, 168), (2.0, 2.5), ("none", "HL")):
            e, x, r, s = run_pair(la, lb, oa, ob, beta, hl, Z, k, flt == "HL", C.GRID_T0, fta, fra, ftb, frb)
            tab.add(f"PAIRS|X|Z{Z}|k{k:g}|{flt}", e, x, r, s, np.full(len(e), pid))
    return tab.finish()


if __name__ == "__main__":
    which = sys.argv[1]
    R3.save("PAIRS", which, main(which))
