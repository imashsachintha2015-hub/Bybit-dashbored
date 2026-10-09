"""Stage 1 of PREREG_scalp_hf.md: the signal-quality screen. Gross edge (bps) of every cell at fixed horizons, day-clustered.
usage: python3 hf_stage1.py SEGS [COINS=design|unseen] [CELLS.json]   (SEGS: DEV | VAL | DEV,VAL ...)
Only events whose entry time lies in the requested segments are evaluated; with CELLS.json only those cells are kept."""
import sys, json, pickle, time
import numpy as np
import hf_core as C, hf_fam as F

HZ = {"fund": (1, 5, 15, 30), "lead": (1, 3, 5, 10), "flow": (5, 20, 60), "oi": (5, 20, 60), "xsec": (15, 30, 60)}
HZ4 = (5, 10, 30, 60)
def horizons(fam): return HZ.get(fam, HZ4)


def collect(store, fam, variant, cid, co, idx, side, adj, segs, hz):
    if len(idx) == 0: return
    t_e = co.t0 + (idx + 1) * C.MIN
    keep = np.zeros(len(idx), bool)
    for s in segs: keep |= C.seg_mask(t_e, s)
    if not keep.any(): return
    idx = idx[keep]; side = side[keep]; t_e = t_e[keep]
    hs = np.array([h + 2 if fam == "fund" else h for h in hz], dtype=np.int64)
    g = C.fixed_horizon(co.o, co.c, idx, side.astype(np.int64), hs)
    if adj is not None: g = g + adj[keep][:, None]
    d = store.setdefault((fam, variant), dict(coin=[], t=[], side=[], g=[]))
    d["coin"].append(np.full(len(idx), cid)); d["t"].append(t_e); d["side"].append(side); d["g"].append(g)


def main():
    segs = sys.argv[1].split(","); which = sys.argv[2] if len(sys.argv) > 2 else "design"
    cells = None
    if len(sys.argv) > 3: cells = {(f, tuple(v) if isinstance(v, list) else v) for f, v in json.load(open(sys.argv[3]))}
    names = C.DESIGN if which == "design" else C.UNSEEN
    t0 = time.time(); store = {}
    btc = F.Prep(C.load_coin("BTC"))
    preps = {}
    for cid, nm in enumerate(names):
        pr = btc if nm == "BTC" else F.Prep(C.load_coin(nm)); preps[nm] = pr; co = pr.coin
        ft, fr = C.load_funding(nm)
        sess = F.session_starts(co)
        jobs = [("fund", F.fam_fund_settle(pr, ft, fr)), ("flow", F.fam_flow(pr)), ("oi", F.fam_oi(pr, C.load_oi(nm))),
                ("orb", F.fam_orb(pr, sess)), ("vwap", F.fam_vwap(pr)), ("sweep", F.fam_sweep(pr)), ("squeeze", F.fam_squeeze(pr)),
                ("pullback", F.fam_pullback(pr)), ("rsi2", F.fam_rsi2(pr))]
        if nm != "BTC": jobs.insert(1, ("lead", F.fam_btc_lead(btc, pr)))
        for fam, res in jobs:
            for var, (idx, side, adj) in res.items():
                if cells is None or (fam, var) in cells: collect(store, fam, var, cid, co, idx, side, adj, segs, horizons(fam))
        print(f"{nm} done {time.time() - t0:.0f}s", flush=True)
    if which == "design":                                                    # XSEC needs all coins at once
        for var, per in F.fam_xsec(preps).items():
            if cells is not None and ("xsec", var) not in cells: continue
            for cid, nm in enumerate(names):
                idx, side, adj = per[nm]; collect(store, "xsec", var, cid, preps[nm].coin, idx, side, adj, segs, horizons("xsec"))
    out = {}
    for key, d in store.items():
        out[key] = dict(coin=np.concatenate(d["coin"]), t=np.concatenate(d["t"]), side=np.concatenate(d["side"]), g=np.vstack(d["g"]))
    tag = which + "_" + "_".join(segs)
    pickle.dump(out, open(f"{C.DATA}/stage1_{tag}.pkl", "wb"))
    print("saved", len(out), "variants", f"{time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
