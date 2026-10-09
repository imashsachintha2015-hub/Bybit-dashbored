"""Round 5 control (written after the judges ran; exploratory, flagged as such): does the AVWAP pullback TIMING matter, or is any add-on inside
a running trend as good? For every production trade: (a) RANDOM = 5 draws of a uniform bar j in [i+2, exit-2], entry next open, S3 stop,
production exit; (b) EARLY = j = i+2 (the first possible bar). Same cost and exit rules as the AVWAP add-ons."""
import os, pickle
import numpy as np
import hf_core as C
import hf_ladder as LD
import r4
import r5_prep as P5
import r5_run as R5

rng = np.random.default_rng(12345)
out = {}
btc = C.load_coin("BTC"); trend_days = LD.btc_daily_trend(btc)
for group in ("design", "unseen", "holdout2"):
    names = r4.GROUPS[group]; tr = R5.load(group)["trades"]
    rowsR, rowsE = [], []
    for cid, nm in enumerate(names):
        co = btc if nm == "BTC" else C.load_coin(nm); ft, fr = C.load_funding(nm); bar = P5.bars4h(co)
        m = np.flatnonzero(tr["coin"] == cid)
        for k in m:
            i = int(tr["i"][k]); side = int(tr["side"][k]); xb = int((tr["ext"][k] - co.t0) // P5.BMS) - 1
            lo, hi = i + 2, min(xb - 1, len(bar["C"]) - 2)
            if hi <= lo: continue
            r = P5.add_on_trade(co, bar, ft, fr, lo, side, "S3")
            if r is not None: rowsE.append((cid, k) + r)
            for _ in range(5):
                j = int(rng.integers(lo, hi))
                r = P5.add_on_trade(co, bar, ft, fr, j, side, "S3")
                if r is not None: rowsR.append((cid, k) + r)
        print(group, nm, flush=True)
    out[group] = (np.array(rowsR), np.array(rowsE))
pickle.dump(out, open(os.path.join(P5.OUT, "r5_control.pkl"), "wb"))
print("\n# Stand-alone net R of add-ons by timing rule (S3 stop, production exit, 14 bps + funding)")
print(f"  {'set':10s} {'AVWAP-pullback':>26s} {'RANDOM bar (5 draws)':>26s} {'EARLY (first bar)':>26s}")
fz = None
for group, label, w in (("design", "A+B 21-25", (R5.T_A[0], R5.T_B[1])), ("design", "J1 FINAL", R5.T_J1), ("unseen", "J2 UNSEEN", (0, 2 ** 62)), ("holdout2", "J3 HOLDOUT2", (0, 2 ** 62))):
    ad = R5.load(group)["addons"][("AVWAP", "S3")]
    rr, ee = out[group]
    cells = []
    for arr in (None, rr, ee):
        if arr is None: ent, ret, sf = ad["ent"], ad["ret"], ad["sf"]
        else: ent, ret, sf = arr[:, 2].astype(np.int64), arr[:, 4], arr[:, 5]
        mm = (ent >= w[0]) & (ent < w[1])
        st = C.cluster_stats(ret[mm] / sf[mm], ent[mm] // C.DAY) if mm.sum() > 1 else dict(n=0, mean=np.nan, t=np.nan)
        cells.append(f"n {st['n']:5d} R {st['mean']:+.3f} (t {st['t']:+.2f})")
    print(f"  {label:10s} {cells[0]:>26s} {cells[1]:>26s} {cells[2]:>26s}")
