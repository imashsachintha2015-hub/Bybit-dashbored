"""Round 8, M2 (PREREG_round8.md): analog (nearest-neighbour) forecaster of the 60-minute return from a 12-dimensional market state.
usage: python3 r8_knn.py build | predict | select | judge"""
import os, sys, json, pickle
import numpy as np
import hf_core as C
import hf_fam as F
import hf2_feat as FT
import r3_common as R3
import r4
import r6_fast as R6
import r8_shock as SH

OUT = os.path.join(C.DATA, "r8"); HERE = os.path.dirname(os.path.abspath(__file__))
inwin, cl, money = R6.inwin, R6.cl, R6.money
T_A, T_B, T_J1 = R6.T_A, R6.T_B, R6.T_J1
FEATS = ["r30m", "r2h", "r8h", "r24h", "volratio", "flow1h", "oi4h", "fund", "hsin", "hcos", "btc2h", "vwapdist"]
K_NN = 400; TAUS = (15.0, 25.0)


def btc_series():
    co = C.load_coin("BTC")
    O, H, L, C5, *_ = C.aggregate(co.o, co.h, co.l, co.c, co.qv, co.n, co.tb, 5)
    C5f = C5.copy()
    from hf_ladder import ffill
    C5f, _ = ffill(C5)
    r5 = np.full(len(C5f), np.nan); r5[1:] = np.log(C5f[1:] / C5f[:-1])
    sg = SH.ew_sigma_prev(r5, 2016)
    return C5f, sg


def coin_samples(co, ft, fr, oi, cid, btcC5, btcsg):
    from hf_ladder import ffill
    O5, H5, L5, C5, Q5, N5, TB5 = C.aggregate(co.o, co.h, co.l, co.c, co.qv, co.n, co.tb, 5)
    ok5 = np.isfinite(C5); C5f, _ = ffill(C5)
    n5 = len(C5f)
    r5 = np.full(n5, np.nan); r5[1:] = np.log(C5f[1:] / C5f[:-1]); r5[~ok5] = np.nan
    sg = SH.ew_sigma_prev(r5, 2016)
    r5z = np.where(np.isfinite(r5), r5, 0.0)
    cs2 = np.concatenate([[0.0], np.cumsum(r5z * r5z)])
    qz = np.where(np.isfinite(Q5), Q5, 0.0); tz = np.where(np.isfinite(TB5), TB5, 0.0)
    csq = np.concatenate([[0.0], np.cumsum(qz)]); cst = np.concatenate([[0.0], np.cumsum(tz)])
    okc = np.concatenate([[0], np.cumsum(ok5.astype(np.int64))])
    vw, _ = FT.anchored_vwap(co.h, co.l, co.c, co.qv, 1440, 0)
    O30, H30, L30, C30, *_ = C.aggregate(co.o, co.h, co.l, co.c, co.qv, co.n, co.tb, 30)
    atr30 = C.atr_wilder(H30, L30, C30, 14)
    j = np.arange(600, n5 - 13, 12)                                           # decision at each hour start; last closed 5m bar is j-1
    i1 = j * 5
    good = (okc[j] - okc[j - 288] >= 0.95 * 288) & np.isfinite(sg[j]) & np.isfinite(co.o[i1]) & np.isfinite(co.c[np.minimum(i1 + 59, co.N - 1)]) & (i1 + 60 < co.N)
    def ret(m): return np.log(C5f[j - 1] / C5f[j - 1 - m]) / (sg[j] * np.sqrt(m))
    with np.errstate(all="ignore"):
        rv_s = np.sqrt((cs2[j] - cs2[j - 24]) / 24); rv_l = np.sqrt((cs2[j] - cs2[j - 288]) / 288)
        flow = (2 * (cst[j] - cst[j - 12]) - (csq[j] - csq[j - 12])) / (csq[j] - csq[j - 12])
        oi4 = np.where((j - 49 >= 0) & (j - 1 < len(oi)), np.log(oi[np.clip(j - 1, 0, len(oi) - 1)] / oi[np.clip(j - 49, 0, len(oi) - 1)]), 0.0)
        oi4 = np.where(np.isfinite(oi4), oi4, 0.0)
        k = np.searchsorted(ft, co.t0 + i1 * 60000, side="right") - 1
        fund = np.where(k >= 0, fr[np.clip(k, 0, len(fr) - 1)], 0.0) * 1e4
        hr = ((i1 // 60) % 24) / 24.0 * 2 * np.pi
        btc2 = np.log(btcC5[j - 1] / btcC5[j - 25]) / (btcsg[j] * np.sqrt(24))
        vwd = (co.c[i1 - 1] - vw[i1 - 1]) / co.c[i1 - 1] / (sg[j] * np.sqrt(72))
        X = np.column_stack([ret(6), ret(24), ret(96), ret(288), rv_s / rv_l, flow, oi4, fund, np.sin(hr), np.cos(hr), btc2, vwd])
        tgt = np.log(co.c[np.minimum(i1 + 59, co.N - 1)] / co.o[i1]) * 1e4
        a30 = atr30[np.clip(i1 // 30 - 1, 0, len(atr30) - 1)]
    good &= np.all(np.isfinite(X), 1) & np.isfinite(tgt) & np.isfinite(a30)
    return dict(X=X[good].astype(np.float32), tgt=tgt[good], i1=i1[good], atr30=a30[good], coin=np.full(good.sum(), cid), t=(co.t0 + i1[good] * 60000))


def build():
    os.makedirs(OUT, exist_ok=True); btcC5, btcsg = btc_series()
    for group in ("design", "unseen", "holdout2"):
        parts = []
        for cid, nm in enumerate(r4.GROUPS[group]):
            co = C.load_coin(nm); ft, fr = C.load_funding(nm); oi = C.load_oi(nm)
            parts.append(coin_samples(co, ft, fr, oi, cid, btcC5, btcsg)); print(group, nm, len(parts[-1]["tgt"]), flush=True)
        d = {k: np.concatenate([p[k] for p in parts]) for k in parts[0]}
        pickle.dump(d, open(os.path.join(OUT, f"r8_knn_samples_{group}.pkl"), "wb"))


def predict():
    import faiss
    S = {g: pickle.load(open(os.path.join(OUT, f"r8_knn_samples_{g}.pkl"), "rb")) for g in ("design", "unseen", "holdout2")}
    d = S["design"]; trainm = inwin(d["t"], T_A)
    mu = d["X"][trainm].mean(0); sd = d["X"][trainm].std(0)
    def z(X): return np.clip((X - mu) / sd, -5, 5).astype(np.float32)
    Xtr = np.ascontiguousarray(z(d["X"][trainm])); ytr = d["tgt"][trainm]
    index = faiss.IndexFlatL2(Xtr.shape[1]); index.add(Xtr)
    print(f"training samples {len(ytr)} (design coins, A); mean target {ytr.mean():+.2f} bps, std {ytr.std():.1f}")
    for g in S:
        X = np.ascontiguousarray(z(S[g]["X"])); preds = np.zeros(len(X), np.float32)
        for a in range(0, len(X), 50000):
            D_, I_ = index.search(X[a:a + 50000], K_NN); preds[a:a + 50000] = ytr[I_].mean(1)
        S[g]["pred"] = preds; print(g, "predicted", len(preds), flush=True)
        pickle.dump(S[g], open(os.path.join(OUT, f"r8_knn_pred_{g}.pkl"), "wb"))


def diagnostics(S):
    """does the prediction carry information? realised 60-minute return by prediction quintile, per window"""
    print("# Prediction value (realised 60-minute return in bps by prediction quintile; quintile cuts from each window itself)")
    wins = [("design A (train, in-sample)", "design", T_A), ("design B 2024-07..2025-06", "design", T_B), ("J1 design FINAL", "design", T_J1),
            ("J2 UNSEEN", "unseen", (0, 2 ** 62)), ("J3 HOLDOUT2", "holdout2", (0, 2 ** 62))]
    for nm, g, w in wins:
        d = S[g]; m = inwin(d["t"], w); p = d["pred"][m]; y = d["tgt"][m]
        q = np.digitize(p, np.percentile(p, [20, 40, 60, 80]))
        by = [y[q == i].mean() for i in range(5)]
        ic = np.corrcoef(p, y)[0, 1]
        print(f"  {nm:30s} n {m.sum():6d} pred sd {p.std():5.1f}  corr {ic:+.3f}  quintiles: " + " ".join(f"{b:+6.1f}" for b in by) + f"   top-bottom {by[4] - by[0]:+6.1f} bps")


def trades(S, tau, g, w):
    d = S[g]; m = inwin(d["t"], w) & (np.abs(d["pred"]) >= tau)
    names = r4.GROUPS[g]; tab = R3.Table()
    for cid, nm in enumerate(names):
        mm = m & (d["coin"] == cid)
        if not mm.any(): continue
        co = C.load_coin(nm); ft, fr = C.load_funding(nm)
        i1 = d["i1"][mm]; sd_ = np.sign(d["pred"][mm]).astype(np.int64); pe = co.o[i1]
        dist = np.maximum(3.0 * d["atr30"][mm], 0.005 * pe); stop = pe - sd_ * dist
        ent, ext, gr, fu, ok = R3.sim_list(co.o, co.h, co.l, co.c, i1, sd_, stop, np.zeros(len(i1)), np.full(len(i1), 60, np.int64), co.t0, ft, fr)
        tab.add("T", ent[ok], ext[ok], gr[ok] - R3.COST - fu[ok], (dist / pe)[ok], np.full(ok.sum(), cid))
    out = tab.finish()
    return out.get("T")


def select():
    S = {g: pickle.load(open(os.path.join(OUT, f"r8_knn_pred_{g}.pkl"), "rb")) for g in ("design", "unseen", "holdout2")}
    diagnostics(S)
    print("\n# M2 selection: design coins, trade when |prediction| >= TAU (A is the training window, so A is in-sample; B is the real test)")
    best = None
    for tau in TAUS:
        res = {}
        for lab, w in (("A", T_A), ("B", T_B)):
            t = trades(S, tau, "design", w)
            res[lab] = t
        ra = res["A"]; rb = res["B"]
        sa = cl(ra["net"] / ra["sf"], ra["ent"]) if ra is not None else dict(n=0, mean=np.nan, t=np.nan)
        sb = cl(rb["net"] / rb["sf"], rb["ent"]) if rb is not None else dict(n=0, mean=np.nan, t=np.nan)
        print(f"  TAU {tau:4.0f}: A n {sa['n']:6d} R {sa['mean']:+.3f} (t {sa['t']:+.2f}) | B n {sb['n']:6d} R {sb['mean']:+.3f} (t {sb['t']:+.2f}) | B gross {np.mean(rb['net'] + R3.COST) * 1e4 if rb is not None else np.nan:+.1f} bps")
        if sa["n"] + sb["n"] >= 150 and sa["mean"] > 0 and sb["mean"] > 0:
            tt = sb["t"]
            if best is None or tt > best[1]: best = (tau, tt)
    print("  -> frozen:", best[0] if best else None)
    json.dump(best[0] if best else None, open(os.path.join(HERE, "r8_knn_frozen.json"), "w"))


def judge():
    tau = json.load(open(os.path.join(HERE, "r8_knn_frozen.json")))
    S = {g: pickle.load(open(os.path.join(OUT, f"r8_knn_pred_{g}.pkl"), "rb")) for g in ("design", "unseen", "holdout2")}
    print("# M2 judges")
    if tau is None: print("  no TAU qualified on SELECTION"); return
    for nm, g, w in (("J1 design FINAL", "design", T_J1), ("J2 UNSEEN", "unseen", (0, 2 ** 62)), ("J3 HOLDOUT2", "holdout2", (0, 2 ** 62))):
        t = trades(S, tau, g, w)
        if t is None: print(f"  {nm}: no trades"); continue
        st = cl(t["net"] / t["sf"], t["ent"]); eq, tk, dd = money(t["ent"], t["ext"], t["net"], t["sf"], t["slot"])
        print(f"  {nm:16s} TAU {tau:.0f}: trades {st['n']:5d}  gross {np.mean(t['net'] + R3.COST) * 1e4:+6.1f} bps  net R {st['mean']:+.3f} (t {st['t']:+.2f})  $10 -> {eq:7.2f} ({tk} taken, DD {dd * 100:.0f}%)")


if __name__ == "__main__":
    {"build": build, "predict": predict, "select": select, "judge": judge}[sys.argv[1]]()
