"""Round 3, Addendum 1 (PREREG_round3.md): MAGNET with maker execution (limit entry at the signal close, limit target), 16 cells.
usage: python3 r3_liq_maker.py design|unseen"""
import sys
import numpy as np
import numba as nb
import hf_core as C
import hf_fam as F
import r3_common as R3
import r3_liq as LQ


@nb.njit(cache=True)
def walk_maker(o, h, l, c, i1, side, P, pen, tgt, frac, tp_pen, tmax):
    """returns (fill minute, exit minute, exit price, target_hit, stopfrac) or fill -1"""
    N = len(o); fill = -1
    for b in range(i1, min(i1 + 3, N)):
        if np.isnan(h[b]): continue
        if (side == -1 and h[b] >= P + pen) or (side == 1 and l[b] <= P - pen): fill = b; break
    if fill < 0: return -1, -1, 0.0, False, 0.0
    dist = side * (tgt - P)
    if dist <= 0.003 * P: return -1, -1, 0.0, False, 0.0
    stop = P - side * frac * dist
    last = min(fill + tmax - 1, N - 1)
    for b in range(fill, last + 1):
        if np.isnan(h[b]) or np.isnan(l[b]): continue
        if b > fill and ((side == 1 and o[b] <= stop) or (side == -1 and o[b] >= stop)): return fill, b, o[b], False, frac * dist / P
        if (side == 1 and l[b] <= stop) or (side == -1 and h[b] >= stop): return fill, b, stop, False, frac * dist / P
        if b > fill and ((side == 1 and h[b] >= tgt + tp_pen) or (side == -1 and l[b] <= tgt - tp_pen)): return fill, b, tgt, True, frac * dist / P
    for b in range(last, fill - 1, -1):
        if not np.isnan(c[b]): return fill, b, c[b], False, frac * dist / P
    return -1, -1, 0.0, False, 0.0


@nb.njit(cache=True)
def run(o, h, l, c, js, side, P, pen, tgt, frac, tp_pen, t0, ft, fr):
    n = len(js)
    ent = np.zeros(n, np.int64); ext = np.zeros(n, np.int64); net = np.zeros(n); sf = np.zeros(n); ok = np.zeros(n, np.bool_)
    for e in range(n):
        i1 = (js[e] + 1) * 5
        if i1 >= len(o): continue
        f, x, px, hit, s = walk_maker(o, h, l, c, i1, side, P[e], pen[e], tgt[e], frac, tp_pen[e], 1440)
        if f < 0: continue
        te = t0 + f * 60000; tx = t0 + (x + 1) * 60000
        gross = side * (px / P[e] - 1.0)
        net[e] = gross - C.MAKER - (C.MAKER if hit else C.TAKER) - C.funding_paid(ft, fr, te, tx, side)
        ent[e] = te; ext[e] = tx; sf[e] = s; ok[e] = True
    return ent, ext, net, sf, ok


def coin_trades(co, oi, ft, fr, cid, tab):
    O, H, L, Cc, Q, NN, TB = C.aggregate(co.o, co.h, co.l, co.c, co.qv, co.n, co.tb, 5)
    n = len(Cc); fin = Cc[np.isfinite(Cc)]
    p0 = fin.min() * 0.5; nbins = int((np.log(fin.max() * 2) - np.log(p0)) / LQ.STEP) + 2
    v24 = F.rolling_sum(np.where(np.isfinite(Q), Q, 0.0), 288)
    atr5 = C.atr_wilder(H, L, Cc, 14)
    oi = oi[:n] if len(oi) >= n else np.concatenate([oi, np.full(n - len(oi), np.nan)])
    for hl, hln in ((24.0, "HL1d"), (72.0, "HL3d")):
        clrL, clrS, A, B, pa, pb = LQ.engine(O, H, L, Cc, Q, TB, oi, hl, p0, nbins)
        with np.errstate(invalid="ignore", divide="ignore"): XA = A / v24; XB = B / v24
        dec = np.arange(2, n, 3)
        thrA = np.full(n, np.nan); thrB = np.full(n, np.nan)
        thrA[dec] = F.daily_quantile(XA[dec], 96, 30 * 96, 0.8); thrB[dec] = F.daily_quantile(XB[dec], 96, 30 * 96, 0.8)
        for R in (2, 4):
            for sd, X, thr, own, other, tgtp in ((1, XA, thrA, A, B, pa), (-1, XB, thrB, B, A, pb)):
                cond = np.isfinite(X) & np.isfinite(thr) & (X > thr) & (own > 0) & (own >= R * other) & np.isfinite(tgtp) & np.isfinite(atr5)
                j = np.flatnonzero(cond); P = Cc[j]
                pen = np.maximum(0.05 * atr5[j], 1e-4 * P); tpp = 0.02 * atr5[j]
                for ex, frac in (("T1R", 1.0), ("T2R", 0.5)):
                    ent, ext, net, sf, ok = run(co.o, co.h, co.l, co.c, j, sd, P, pen, tgtp[j], frac, tpp, co.t0, ft, fr)
                    tab.add(f"MAKERMAG|{'L' if sd == 1 else 'S'}|{hln}|R{R}|{ex}", ent[ok], ext[ok], net[ok], sf[ok], np.full(ok.sum(), cid))


def main(which):
    names = C.DESIGN if which == "design" else C.UNSEEN
    tab = R3.Table()
    for cid, nm in enumerate(names):
        co = C.load_coin(nm); ft, fr = C.load_funding(nm); oi = C.load_oi(nm)
        coin_trades(co, oi, ft, fr, cid, tab); print(nm, flush=True)
    return tab.finish()


if __name__ == "__main__":
    R3.save("LIQMK", sys.argv[1], main(sys.argv[1]))
