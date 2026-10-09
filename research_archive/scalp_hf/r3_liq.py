"""Round 3, idea A (PREREG_round3.md): a liquidation map estimated from 5-minute open interest, and two trades on it:
MAGNET (price is pulled toward the side with more liquidation fuel) and SWEEP (fade the bar after a big estimated liquidation cascade).
usage: python3 r3_liq.py design|unseen"""
import sys
import numpy as np
import numba as nb
import hf_core as C
import hf_fam as F
import r3_common as R3

STEP = np.log(1.001)
LEVS = np.array([10.0, 25.0, 50.0, 100.0])


@nb.njit(cache=True)
def engine(O, H, L, Cc, Q, TB, oi, hl_hours, p0, nbins):
    """per 5m bar j: cleared long-/short-liquidation notional (clrL, clrS) and, at 15m closes, A/B (short-liq above / long-liq below
    within 0.5-3%) and the centres of the densest 5-bin windows (pa, pb). Maps hold value/g (g = global scale for decay and OI-down scaling)."""
    n = len(Cc)
    mL = np.zeros(nbins); mS = np.zeros(nbins); g = 1.0
    f = 0.5 ** (1.0 / hl_hours)
    clrL = np.zeros(n); clrS = np.zeros(n)
    A = np.full(n, np.nan); B = np.full(n, np.nan); pa = np.full(n, np.nan); pb = np.full(n, np.nan)
    lp0 = np.log(p0)
    for j in range(1, n):
        if j % 12 == 0: g *= f
        if g < 1e-150:
            for b in range(nbins): mL[b] *= g; mS[b] *= g
            g = 1.0
        # OI change of bar j-1 (stamps j-1 -> j), known at the close of bar j
        if j < len(oi) and np.isfinite(oi[j]) and np.isfinite(oi[j - 1]) and oi[j - 1] > 0 and np.isfinite(Cc[j - 1]) and np.isfinite(Q[j - 1]) and Q[j - 1] > 0:
            d = oi[j] - oi[j - 1]
            if d > 0:
                p = (H[j - 1] + L[j - 1] + Cc[j - 1]) / 3.0
                s = min(max(TB[j - 1] / Q[j - 1], 0.0), 1.0)
                N = d * p
                for q in range(4):
                    lv = LEVS[q]
                    bl = int((np.log(p * (1 - 1 / lv + 0.005)) - lp0) / STEP); bs = int((np.log(p * (1 + 1 / lv - 0.005)) - lp0) / STEP)
                    if 0 <= bl < nbins: mL[bl] += N * s / 4.0 / g
                    if 0 <= bs < nbins: mS[bs] += N * (1 - s) / 4.0 / g
            else:
                g *= oi[j] / oi[j - 1]
        if not (np.isfinite(H[j]) and np.isfinite(L[j]) and np.isfinite(Cc[j])): continue
        b0 = max(int((np.log(L[j]) - lp0) / STEP), 0); b1 = min(int((np.log(H[j]) - lp0) / STEP), nbins - 1)
        cl = 0.0; cs = 0.0
        for b in range(b0, b1 + 1):
            cl += mL[b]; cs += mS[b]; mL[b] = 0.0; mS[b] = 0.0
        clrL[j] = cl * g; clrS[j] = cs * g
        if j % 3 == 2:                                                        # a 15m close
            P = Cc[j]; lP = np.log(P)
            ua = int((lP + np.log(1.005) - lp0) / STEP) + 1; ub = int((lP + np.log(1.03) - lp0) / STEP)
            da = int((lP + np.log(0.97) - lp0) / STEP); db = int((lP + np.log(0.995) - lp0) / STEP) - 1
            ua = max(ua, 0); ub = min(ub, nbins - 1); da = max(da, 0); db = min(db, nbins - 1)
            sa = 0.0; best = -1.0; bi = -1
            for b in range(ua, ub + 1):
                sa += mS[b]
                w = 0.0
                for q in range(max(b - 2, ua), min(b + 2, ub) + 1): w += mS[q]
                if w > best: best = w; bi = b
            sb = 0.0; bestb = -1.0; bj = -1
            for b in range(da, db + 1):
                sb += mL[b]
                w = 0.0
                for q in range(max(b - 2, da), min(b + 2, db) + 1): w += mL[q]
                if w > bestb: bestb = w; bj = b
            A[j] = sa * g; B[j] = sb * g
            if bi >= 0: pa[j] = np.exp(lp0 + (bi + 0.5) * STEP)
            if bj >= 0: pb[j] = np.exp(lp0 + (bj + 0.5) * STEP)
    return clrL, clrS, A, B, pa, pb


def coin_trades(co, oi, ft, fr, cid, tab):
    O, H, L, Cc, Q, NN, TB = C.aggregate(co.o, co.h, co.l, co.c, co.qv, co.n, co.tb, 5)
    n = len(Cc)
    fin = Cc[np.isfinite(Cc)]
    p0 = fin.min() * 0.5; nbins = int((np.log(fin.max() * 2) - np.log(p0)) / STEP) + 2
    v24 = F.rolling_sum(np.where(np.isfinite(Q), Q, 0.0), 288)
    atr5 = C.atr_wilder(H, L, Cc, 14)
    oi = oi[:n] if len(oi) >= n else np.concatenate([oi, np.full(n - len(oi), np.nan)])
    for hl, hln in ((24.0, "HL1d"), (72.0, "HL3d")):
        clrL, clrS, A, B, pa, pb = engine(O, H, L, Cc, Q, TB, oi, hl, p0, nbins)
        with np.errstate(invalid="ignore", divide="ignore"):
            XA = A / v24; XB = B / v24
        dec = np.arange(2, n, 3)                                              # 15m closes: the percentile is taken over these values
        thrA = np.full(n, np.nan); thrB = np.full(n, np.nan)
        thrA[dec] = F.daily_quantile(XA[dec], 96, 30 * 96, 0.8); thrB[dec] = F.daily_quantile(XB[dec], 96, 30 * 96, 0.8)
        # ---- MAGNET
        for R in (2, 4):
            for sd, X, thr, own, other, tgtp in ((1, XA, thrA, A, B, pa), (-1, XB, thrB, B, A, pb)):
                cond = np.isfinite(X) & np.isfinite(thr) & (X > thr) & (own > 0) & (own >= R * other) & np.isfinite(tgtp)
                j = np.flatnonzero(cond)
                i1 = (j + 1) * 5
                ok = i1 < len(co.o); j, i1 = j[ok], i1[ok]
                pe = co.o[i1]; ok = np.isfinite(pe); j, i1, pe = j[ok], i1[ok], pe[ok]
                tg = tgtp[j]; dist = sd * (tg - pe)
                ok = dist > 0.003 * pe; j, i1, pe, tg, dist = j[ok], i1[ok], pe[ok], tg[ok], dist[ok]
                for ex, frac in (("T1R", 1.0), ("T2R", 0.5)):
                    stop = pe - sd * frac * dist
                    ent, ext, gr, fu, okk = R3.sim_list(co.o, co.h, co.l, co.c, i1, np.full(len(i1), sd), stop, tg, np.full(len(i1), 1440), co.t0, ft, fr)
                    m = okk
                    tab.add(f"MAGNET|{'L' if sd == 1 else 'S'}|{hln}|R{R}|{ex}", ent[m], ext[m], gr[m] - R3.COST - fu[m], (frac * dist / pe)[m], np.full(m.sum(), cid))
        # ---- SWEEP
        for q in (0.98, 0.995):
            for sd, clr in ((1, clrL), (-1, clrS)):
                with np.errstate(invalid="ignore", divide="ignore"): X = clr / v24
                thr = F.daily_quantile(X, 288, 30 * 288, q)
                j = np.flatnonzero(np.isfinite(X) & np.isfinite(thr) & (X >= thr) & (X > 0))
                j = j[j + 2 < n]
                doi = oi[j + 1] - oi[j]                                           # OI change of bar j, known at the close of bar j+1
                j = j[np.isfinite(doi) & (doi < 0)]
                i1 = (j + 2) * 5
                ok = i1 < len(co.o); j, i1 = j[ok], i1[ok]
                pe = co.o[i1]; a = atr5[j + 1]
                if sd == 1: ext_ = np.fmin(L[j], L[j + 1]) - 0.25 * a
                else: ext_ = np.fmax(H[j], H[j + 1]) + 0.25 * a
                d = sd * (pe - ext_)
                ok = np.isfinite(pe) & np.isfinite(d) & (d > 0) & (d <= 0.04 * pe)
                j, i1, pe, d = j[ok], i1[ok], pe[ok], d[ok]
                d = np.maximum(d, 0.003 * pe)
                for ex, mult in (("T15", 1.5), ("T3", 3.0)):
                    stop = pe - sd * d; tg = pe + sd * mult * d
                    ent, ext, gr, fu, okk = R3.sim_list(co.o, co.h, co.l, co.c, i1, np.full(len(i1), sd), stop, tg, np.full(len(i1), 240), co.t0, ft, fr)
                    m = okk
                    tab.add(f"SWEEP|{'L' if sd == 1 else 'S'}|{hln}|q{q * 100:g}|{ex}", ent[m], ext[m], gr[m] - R3.COST - fu[m], (d / pe)[m], np.full(m.sum(), cid))


def main(which, names=None, first=None, last=None):
    names = names or (C.DESIGN if which == "design" else C.UNSEEN)
    tab = R3.Table()
    for cid, nm in enumerate(names):
        co = C.load_coin(nm, **({} if first is None else dict(first=first, last=last)))
        ft, fr = C.load_funding(nm); oi = C.load_oi(nm)
        coin_trades(co, oi, ft, fr, cid, tab)
        print(nm, flush=True)
    return tab.finish()


if __name__ == "__main__":
    which = sys.argv[1]
    R3.save("LIQ", which, main(which))
