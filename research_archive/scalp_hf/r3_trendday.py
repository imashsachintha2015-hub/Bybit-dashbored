"""Round 3, idea D (PREREG_round3.md): intraday trend days. usage: python3 r3_trendday.py design|unseen"""
import sys
import numpy as np
import hf_core as C
import r3_common as R3


def coin_trades(co, ft, fr, cid, tab):
    nd = co.N // 1440
    O, H, L, Cc, *_ = C.aggregate(co.o, co.h, co.l, co.c, co.qv, co.n, co.tb, 1440)
    atr = C.atr_wilder(H, L, Cc, 14)
    A = np.full(nd, np.nan); A[1:] = atr[:nd - 1]                                 # completed days only
    h2 = co.h[:nd * 1440].reshape(nd, 1440); l2 = co.l[:nd * 1440].reshape(nd, 1440); o2 = co.o[:nd * 1440].reshape(nd, 1440)
    c2 = co.c[:nd * 1440].reshape(nd, 1440)
    for T in (480, 720, 960):
        with np.errstate(all="ignore"):
            hi = np.nanmax(h2[:, :T], 1); lo = np.nanmin(l2[:, :T], 1)
            valid = np.isfinite(h2[:, :T]).sum(1) >= 0.95 * T
        op = o2[:, 0]; P = c2[:, T - 1]
        base = valid & np.isfinite(op) & np.isfinite(P) & np.isfinite(A) & (hi > lo)
        for k in (0.5, 0.8):
            for sd in (1, -1):
                with np.errstate(invalid="ignore"):
                    pos = (P - lo) / (hi - lo)
                    cond = base & (sd * (P - op) >= k * A) & ((pos >= 0.75) if sd == 1 else (pos <= 0.25))
                d = np.flatnonzero(cond)
                i1 = d * 1440 + T
                pe = co.o[i1]; ok = np.isfinite(pe); d, i1, pe = d[ok], i1[ok], pe[ok]
                for sname in ("half", "daylow"):
                    if sname == "half": stop = P[d] - sd * 0.5 * A[d]
                    else: stop = (lo[d] - 0.1 * A[d]) if sd == 1 else (hi[d] + 0.1 * A[d])
                    dist = sd * (pe - stop)
                    m = (dist > 0) & (dist <= 0.06 * pe)
                    ent, ext, gr, fu, okk = R3.sim_list(co.o, co.h, co.l, co.c, i1[m], np.full(m.sum(), sd), stop[m], np.zeros(m.sum()),
                                                        np.full(m.sum(), 1440 - T), co.t0, ft, fr)
                    tab.add(f"TRENDDAY|{'L' if sd == 1 else 'S'}|T{T // 60:02d}|k{k:g}|{sname}", ent[okk], ext[okk], gr[okk] - R3.COST - fu[okk],
                            (dist[m] / pe[m])[okk], np.full(okk.sum(), cid))


def main(which, names=None, first=None, last=None):
    names = names or (C.DESIGN if which == "design" else C.UNSEEN)
    tab = R3.Table()
    for cid, nm in enumerate(names):
        co = C.load_coin(nm, **({} if first is None else dict(first=first, last=last)))
        ft, fr = C.load_funding(nm)
        coin_trades(co, ft, fr, cid, tab)
    return tab.finish()


if __name__ == "__main__":
    which = sys.argv[1]
    R3.save("TRENDDAY", which, main(which))
