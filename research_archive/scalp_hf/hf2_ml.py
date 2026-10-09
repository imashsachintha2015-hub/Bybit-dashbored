"""Round 2 kitchen sink (PREREG_round2.md): gradient boosting on every setup event, features known at entry, target = net R.
Model 1: trained on DEV, threshold = 90th percentile of out-of-fold DEV predictions (3 contiguous time blocks), tested on VAL.
Model 2: retrained on DEV+VAL (threshold from its own 3-block out-of-fold predictions), tested once on FINAL, UNSEEN (and OLD, reported).
Selected events then go through one position per coin (any setup), and the $10 portfolio."""
import os, sys
import numpy as np
from sklearn.ensemble import HistGradientBoostingRegressor
import hf_core as C
import hf2_setups as S
import hf2_eval as E

EXIT_IDX = {"R15": 0, "LVL": 2}


def design_matrix(ev):
    X = np.column_stack([ev["X"].astype(np.float64), ev["sid"], ev["side"], ev["tf"], ev["datr"], ev["sf"]])
    return X


def model():
    return HistGradientBoostingRegressor(max_iter=300, learning_rate=0.05, max_leaf_nodes=31, min_samples_leaf=200,
                                         categorical_features=[0, 1, 21], random_state=0)


def oof_threshold(X, y, t):
    o = np.argsort(t); blocks = np.array_split(o, 3); pred = np.full(len(y), np.nan)
    for b in range(3):
        tr = np.concatenate([blocks[q] for q in range(3) if q != b])
        m = model().fit(X[tr], y[tr]); pred[blocks[b]] = m.predict(X[blocks[b]])
    return np.percentile(pred, 90), pred


def evaluate(ev, sel, x, seg, label):
    idx = np.flatnonzero(sel)
    o = np.lexsort((ev["ent"][idx], ev["coin"][idx])); idx = idx[o]
    keep = E.nonoverlap(ev["coin"][idx], ev["ent"][idx], ev["xms"][idx, x]); idx = idx[keep]
    net = ev["gr"][idx, x] - E.COST - ev["fu"][idx, x]; R = net / ev["sf"][idx]
    net2 = net - 0.0004; R2 = net2 / ev["sf"][idx]
    m = C.seg_mask(ev["ent"][idx], seg)
    if m.sum() < 2: print(f"  {label:10s} n {m.sum()}"); return None
    st = C.cluster_stats(R[m], ev["ent"][idx][m] // C.DAY)
    ii = idx[m]
    eq, taken, mdd, _, _ = C.portfolio(ev["ent"][ii], ev["xms"][ii, x], net[m], ev["sf"][ii], ev["coin"][ii], 10.0, 0.005, 5, 3.0, 6.0)
    eq1, _, _, _, _ = C.portfolio(ev["ent"][ii], ev["xms"][ii, x], net[m], ev["sf"][ii], ev["coin"][ii], 10.0, 0.01, 5, 3.0, 6.0)
    e = ev["ent"][ii]
    q = [float(np.mean(R[m][(e >= a) & (e < b)])) if ((e >= a) & (e < b)).sum() else float("nan") for a, b in E.QUARTERS]
    print(f"  {label:10s} trades {st['n']:6d} win {np.mean(R[m] > 0) * 100:3.0f}% net R {st['mean']:+.3f} t {st['t']:+.2f} | $10@0.5% -> {eq:7.2f} "
          f"(taken {taken}, maxDD {mdd * 100:.0f}%)  $10@1% -> {eq1:7.2f}  +2bps R {np.mean(R2[m]):+.3f}"
          + ("  quarters " + " ".join(f"{v:+.2f}" for v in q) if seg == "FINAL" else ""))
    return st


def main():
    evd = E.load(C.DESIGN); evu = E.load(C.UNSEEN)
    for ev in (evd, evu):
        keep = ev["sid"] < 25
        for k in list(ev): ev[k] = ev[k][keep]
    Xd = design_matrix(evd); Xu = design_matrix(evu)
    dev = C.seg_mask(evd["ent"], "DEV"); val = C.seg_mask(evd["ent"], "VAL")
    for xname, x in EXIT_IDX.items():
        y = (evd["gr"][:, x] - E.COST - evd["fu"][:, x]) / evd["sf"]
        print(f"\n# exit {xname}: model 1 (train DEV, test VAL); {dev.sum()} DEV events, mean net R of all DEV events {y[dev].mean():+.3f}")
        thr, pred = oof_threshold(Xd[dev], y[dev], evd["ent"][dev])
        print(f"  threshold (DEV out-of-fold p90) {thr:+.3f}; out-of-fold selected DEV events mean net R {y[dev][pred >= thr].mean():+.3f}")
        sel_dev = np.zeros(len(y), bool); sel_dev[np.flatnonzero(dev)[pred >= thr]] = True
        evaluate(evd, sel_dev, x, "DEV", "DEV (oof)")
        m1 = model().fit(Xd[dev], y[dev])
        p_val = m1.predict(Xd); sel = val & (p_val >= thr)
        sv = evaluate(evd, sel, x, "VAL", "VAL")
        print(f"  model 2 (train DEV+VAL, tested once on FINAL / UNSEEN / OLD)")
        tv = dev | val
        thr2, _ = oof_threshold(Xd[tv], y[tv], evd["ent"][tv])
        m2 = model().fit(Xd[tv], y[tv])
        pf = m2.predict(Xd); pu = m2.predict(Xu)
        print(f"  threshold {thr2:+.3f}")
        evaluate(evd, C.seg_mask(evd["ent"], "FINAL") & (pf >= thr2), x, "FINAL", "FINAL")
        evaluate(evu, C.seg_mask(evu["ent"], "FINAL") & (pu >= thr2), x, "FINAL", "UNSEEN")
        evaluate(evd, C.seg_mask(evd["ent"], "OLD") & (pf >= thr2), x, "OLD", "OLD")
        imp = None
        try:
            from sklearn.inspection import permutation_importance
        except Exception: pass


if __name__ == "__main__":
    main()
