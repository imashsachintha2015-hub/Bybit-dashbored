"""Round 9 (PREREG_round9.md): meta-labeling the sensitive panel. usage: python3 r9_meta.py test | build | fit | judge"""
import os, sys, json, pickle, math
import numpy as np
import numba as nb
import hf_core as C
import hf_fam as F
import hf_ladder as LD
import r3_common as R3
import r4
import r6_fast as R6
import r7_sens as R7

BARS = (5, 15, 30); K_SIG = 4; THETA = 1.0; TS = (0.0, 0.1, 0.2, 0.3)
OUT = os.path.join(C.DATA, "r9"); HERE = os.path.dirname(os.path.abspath(__file__))
inwin, cl, money = R6.inwin, R6.cl, R6.money
T_A, T_B, T_J1 = R6.T_A, R6.T_B, R6.T_J1
T_J3C = (C.ms(2024, 7), C.ms(2026, 10))
WARM, HOLD, FEE, MINSTOP, STOP_ATR = 260, 120, 0.0014, 0.005, 3.0
FUND_CAP, FUND_WINDOW = 0.0003, 72 * 3600_000
FEATS = (["score"] + [f"z_{n}" for n in R7.NAMES] + ["atrp", "atr_ratio", "hsin", "hcos", "dsin", "dcos", "cz60", "cz240", "btcz60", "btcz240",
         "mom4", "mom16", "mom64", "range_pos", "relvol", "fund", "oi24", "side"])


@nb.njit(cache=True)
def run_all(t, o, h, l, c, S, a, valid, trend_bar, ft, fr, B, K, zslow):
    """every candidate signal walked on its own (no one-position rule). returns signal bar, entry ms, exit ms, net return, stop fraction, side, funding avg"""
    n = len(c); cap = 700000
    sig = np.zeros(cap, np.int64); ent = np.zeros(cap, np.int64); ext = np.zeros(cap, np.int64); ret = np.zeros(cap); sf = np.zeros(cap)
    sdv = np.zeros(cap, np.int64); fav = np.zeros(cap); k = 0
    for i in range(WARM, n - 1):
        if not valid[i]: continue
        sgn = 0
        if S[i] >= K and S[i - 1] < K: sgn = 1
        elif S[i] <= -K and S[i - 1] > -K: sgn = -1
        if sgn == 0: continue
        tr = trend_bar[i]; tc = t[i] + B * 60000
        lo = np.searchsorted(ft, tc - FUND_WINDOW, side="right"); hi = np.searchsorted(ft, tc, side="right")
        have = len(ft) > 0 and ft[0] <= tc - FUND_WINDOW + 8 * 3600_000 and hi - lo >= 3
        favg = 0.0
        if have:
            for q in range(lo, hi): favg += fr[q]
            favg /= 9.0
        if sgn == 1:
            if tr != 1: continue
            if have and favg > FUND_CAP: continue
        else:
            if tr != -1: continue
            if not have or not favg > 0: continue
        if not valid[i + 1]: continue
        ep = o[i + 1]; stop = c[i] - sgn * STOP_ATR * a[i]
        if sgn * (ep - stop) < MINSTOP * ep: stop = ep - sgn * MINSTOP * ep
        risk = sgn * (ep - stop)
        xt = -1; xp = 0.0; reason = -1
        for j in range(i + 1, min(n, i + 1 + HOLD)):
            if (sgn == 1 and l[j] <= stop) or (sgn == -1 and h[j] >= stop):
                xt, xp, reason = t[j] + B * 60000, stop, 1; break
            if (sgn == 1 and zslow[j] < 0) or (sgn == -1 and zslow[j] > 0):
                if j + 1 < n: xt, xp, reason = t[j + 1] + B * 60000, o[j + 1], 3
                break
        else:
            j = i + HOLD
            if j <= n - 1: xt, xp, reason = t[j] + B * 60000, c[j], 0
        if reason < 0: continue
        gross = sgn * (xp / ep - 1.0)
        f = 0.0
        a0 = np.searchsorted(ft, t[i + 1], side="right"); b0 = np.searchsorted(ft, xt, side="right")
        for q in range(a0, b0): f += fr[q]
        f *= sgn
        if k < cap:
            sig[k] = i; ent[k] = t[i + 1]; ext[k] = xt; ret[k] = gross - FEE - f; sf[k] = risk / ep; sdv[k] = sgn; fav[k] = favg; k += 1
    return sig[:k], ent[:k], ext[:k], ret[:k], sf[:k], sdv[:k], fav[:k]


def slow_z(co, B):
    t, O, H, L, Cc, valid, a = R6.prep_bars(co, B)
    return R6.kalman_z_p(Cc, 1e-4)


def map_idx(tc, t0, minutes, n):
    return np.clip((tc - t0) // (minutes * 60000) - 1, 0, n - 1)


def candidates(co, B, ft, fr, oi, td, btcz):
    t, O, H, L, Cc, Q, TB, valid, a = R7.prep(co, B)
    zs = R7.estimator_z(O, H, L, Cc, Q, TB); S = R7.votes(zs, THETA); zslow = R6.kalman_z_p(Cc, 1e-4)
    day = np.clip(((t + B * 60000) // C.DAY) - 1 - co.t0 // C.DAY, 0, len(td) - 1)
    sig, ent, ext, ret, sf, sd, fav = run_all(t, O, H, L, Cc, S, a, valid, td[day], ft, fr, B, K_SIG, zslow)
    if len(sig) == 0: return None
    tc = t[sig] + B * 60000
    z60, z240 = slow_z(co, 60), slow_z(co, 240)
    cz60 = sd * z60[map_idx(tc, co.t0, 60, len(z60))]; cz240 = sd * z240[map_idx(tc, co.t0, 240, len(z240))]
    bz60 = sd * btcz[60][map_idx(tc, co.t0, 60, len(btcz[60]))]; bz240 = sd * btcz[240][map_idx(tc, co.t0, 240, len(btcz[240]))]
    atrp = a / Cc; ratio = atrp / C.ema(atrp, int(30 * 1440 / B))
    hour = (tc // 3600000) % 24; dow = ((tc // C.DAY) + 4) % 7
    w = 1440 // B; hi = F.rolling_max(H, w); lo = F.rolling_min(L, w)
    with np.errstate(all="ignore"):
        pos = (Cc - lo) / (hi - lo); pos = np.where(sd == 1, pos[sig], 1 - pos[sig])
        qm = F.shift(F.rolling_sum(Q, 20), 1) / 20.0; relvol = (Q / qm)[sig]
        mom = lambda m: sd * (Cc[sig] / Cc[np.maximum(sig - m, 0)] - 1) / atrp[sig]
        s_new = ((tc - co.t0) // 60000) // 5 - 1; s_old = s_new - 288
        ok = (s_old >= 0) & (s_new < len(oi))
        oi24 = np.zeros(len(sig))
        v = np.log(oi[np.clip(s_new, 0, len(oi) - 1)] / oi[np.clip(s_old, 0, len(oi) - 1)]); oi24 = np.where(ok & np.isfinite(v), v, 0.0)
    X = np.column_stack([sd * S[sig]] + [sd * zs[k, sig] for k in range(9)] + [atrp[sig], ratio[sig], np.sin(hour / 24 * 2 * np.pi), np.cos(hour / 24 * 2 * np.pi),
                         np.sin(dow / 7 * 2 * np.pi), np.cos(dow / 7 * 2 * np.pi), cz60, cz240, bz60, bz240, mom(4), mom(16), mom(64), pos, relvol,
                         sd * fav * 1e4, oi24, sd]).astype(np.float32)
    assert X.shape[1] == len(FEATS), (X.shape, len(FEATS))
    return dict(e=ent, x=ext, r=ret, s=sf, sd=sd, X=X)


def build():
    os.makedirs(OUT, exist_ok=True); btc = C.load_coin("BTC"); td = LD.btc_daily_trend(btc)
    btcz = {60: slow_z(btc, 60), 240: slow_z(btc, 240)}
    for group in ("design", "unseen", "holdout2"):
        res = {B: [] for B in BARS}
        for cid, nm in enumerate(r4.GROUPS[group]):
            co = btc if nm == "BTC" else C.load_coin(nm); ft, fr = C.load_funding(nm); oi = C.load_oi(nm)
            for B in BARS:
                d = candidates(co, B, ft, fr, oi, td, btcz)
                if d is not None: d["cid"] = np.full(len(d["e"]), cid); res[B].append(d)
            print(group, nm, {B: sum(len(p["e"]) for p in res[B]) for B in BARS}, flush=True)
        out = {B: {k: np.concatenate([p[k] for p in res[B]]) for k in res[B][0]} for B in BARS}
        pickle.dump(out, open(os.path.join(OUT, f"r9_{group}.pkl"), "wb"))


def test():
    """no look-ahead in the features: computed on data cut mid-way, every candidate before the cut has identical features"""
    btc = C.load_coin("BTC"); td = LD.btc_daily_trend(btc); ft, fr = C.load_funding("BTC"); oi = C.load_oi("BTC")
    btcz = {60: slow_z(btc, 60), 240: slow_z(btc, 240)}
    cut = C.ms(2024, 3, 1) + 11 * 60000
    co2 = C.load_coin("BTC"); m = co2.times >= cut
    for arr in (co2.o, co2.h, co2.l, co2.c, co2.qv, co2.n, co2.tb): arr[m] = np.nan
    fails = 0
    for B in BARS:
        full = candidates(btc, B, ft, fr, oi, td, btcz); part = candidates(co2, B, ft, fr, oi, td, btcz)
        # candidates whose whole trade closed before the cut
        a = full["x"] < cut; b = part["x"] < cut
        ok = a.sum() == b.sum() and np.array_equal(full["e"][a], part["e"][b]) and np.allclose(full["X"][a], part["X"][b], equal_nan=True, rtol=1e-5, atol=1e-6)
        # the btcz of the cut data differs only after the cut by construction (it was computed on full data above): check the coin-only features separately
        coin_cols = [i for i, n in enumerate(FEATS) if not n.startswith("btcz")]
        ok2 = np.allclose(full["X"][a][:, coin_cols], part["X"][b][:, coin_cols], equal_nan=True, rtol=1e-5, atol=1e-6)
        print(f"B={B}: candidates closed before the cut {a.sum()} vs {b.sum()}; same entries {ok}; same coin features {ok2}"); fails += 0 if (ok2 and a.sum() == b.sum()) else 1
    print("FAILS", fails); return fails


def decile_table(e, pred, r, s, w, label):
    m = inwin(e, w)
    if m.sum() < 500: return
    q = np.digitize(pred[m], np.percentile(pred[m], np.arange(10, 100, 10)))
    R = (r[m] / s[m]); by = [R[q == i].mean() for i in range(10)]
    print(f"   {label:24s} n {m.sum():7d} realised net R by predicted decile: " + " ".join(f"{b:+.2f}" for b in by) + f"   top-bottom {by[9] - by[0]:+.2f}  corr {np.corrcoef(pred[m], R)[0, 1]:+.3f}")


def take(d, pred, T, w, extra_mask=None):
    m = inwin(d["e"], w) & (pred >= T)
    if extra_mask is not None: m &= extra_mask
    idx = np.flatnonzero(m)
    o = np.lexsort((d["e"][idx], d["cid"][idx])); idx = idx[o]
    keep = R3.nonoverlap(d["cid"][idx], d["e"][idx], d["x"][idx]); idx = idx[keep]
    return idx


def stats_of(d, idx):
    R = d["r"][idx] / d["s"][idx]; st = cl(R, d["e"][idx]); eq, tk, dd = money(d["e"][idx], d["x"][idx], d["r"][idx], d["s"][idx], d["cid"][idx])
    return st, eq, tk, dd, np.mean(d["r"][idx] + FEE) * 1e4, np.mean((d["x"][idx] - d["e"][idx]) / 3.6e6), np.mean((d["r"][idx] - 0.0004) / d["s"][idx])


def fit():
    from sklearn.ensemble import HistGradientBoostingRegressor
    data = {g: pickle.load(open(os.path.join(OUT, f"r9_{g}.pkl"), "rb")) for g in ("design", "unseen", "holdout2")}
    frozen = {}; models = {}
    print("# SELECTION: model trained on design coins 2021-01..2024-06 (A); thresholds chosen on 2024-07..2025-06 (B)")
    for B in BARS:
        d = data["design"][B]; A = inwin(d["e"], T_A)
        y = np.clip(d["r"] / d["s"], -2, 5)
        mdl = HistGradientBoostingRegressor(max_iter=200, learning_rate=0.05, max_leaf_nodes=15, min_samples_leaf=100, l2_regularization=1.0, random_state=0)
        mdl.fit(d["X"][A], y[A]); models[B] = mdl
        pred = mdl.predict(d["X"]); data["design"][B]["pred"] = pred
        for g in ("unseen", "holdout2"): data[g][B]["pred"] = mdl.predict(data[g][B]["X"])
        span = (T_B[1] - T_B[0]) / C.DAY
        print(f"\n## bar {B} min: {A.sum()} training signals, {inwin(d['e'], T_B).sum()} validation signals")
        decile_table(d["e"], pred, d["r"], d["s"], T_A, "A (in-sample)")
        decile_table(d["e"], pred, d["r"], d["s"], T_B, "B (validation)")
        best = None
        base = take(d, pred, -1e9, T_B); sb = stats_of(d, base)
        print(f"   unfiltered primary on B: n {sb[0]['n']} ({sb[0]['n'] / span:.1f}/day) gross {sb[4]:+.1f} bps net R {sb[0]['mean']:+.3f} (t {sb[0]['t']:+.2f}) $10 -> {sb[1]:.2f} (DD {sb[3] * 100:.0f}%)")
        for T in TS:
            idx = take(d, pred, T, T_B)
            if len(idx) < 50: print(f"   T {T:.1f}: n {len(idx)}"); continue
            st, eq, tk, dd, gb, hold, r2 = stats_of(d, idx)
            print(f"   T {T:.1f}: n {st['n']:5d} ({st['n'] / span:5.2f}/day) gross {gb:+6.1f} bps  net R {st['mean']:+.3f} (t {st['t']:+.2f})  hold {hold:4.1f}h  $10 -> {eq:7.2f} ({tk} taken, DD {dd * 100:.0f}%)")
            if st["n"] >= 300 and st["mean"] > 0 and (best is None or st["t"] > best[1]): best = (T, st["t"])
        frozen[str(B)] = best[0] if best else None
        print(f"   -> frozen T for {B} min: {frozen[str(B)]}")
    json.dump(frozen, open(os.path.join(HERE, "r9_frozen.json"), "w"), indent=1)
    pickle.dump({g: {B: dict(pred=data[g][B]["pred"]) for B in BARS} for g in data}, open(os.path.join(OUT, "r9_pred.pkl"), "wb"))


def judge():
    fz = json.load(open(os.path.join(HERE, "r9_frozen.json")))
    data = {g: pickle.load(open(os.path.join(OUT, f"r9_{g}.pkl"), "rb")) for g in ("design", "unseen", "holdout2")}
    pr = pickle.load(open(os.path.join(OUT, "r9_pred.pkl"), "rb"))
    for g in data:
        for B in BARS: data[g][B]["pred"] = pr[g][B]["pred"]
    sets = {"J1 design FINAL": ("design", T_J1), "J2 UNSEEN": ("unseen", (0, 2 ** 62)), "J3c FRESH from 2024-07": ("holdout2", T_J3C)}
    print("# JUDGES ($10, 0.5% risk, 8 open). Primary = the panel's own signals without the model.")
    for B in BARS:
        T = fz[str(B)]; print(f"\n## bar {B} min")
        if T is None: print("  no threshold qualified on the validation year"); continue
        rows = {}
        for nm, (g, w) in sets.items():
            d = data[g][B]; days = (w[1] - w[0]) / C.DAY if g == "design" else (d["e"][inwin(d["e"], w)].max() - d["e"][inwin(d["e"], w)].min()) / C.DAY
            decile_table(d["e"], d["pred"], d["r"], d["s"], w, nm)
            for lab, thr in (("primary", -1e9), (f"model T={T}", T)):
                idx = take(d, d["pred"], thr, w)
                st, eq, tk, dd, gb, hold, r2 = stats_of(d, idx); rows[(nm, lab)] = (st, eq, tk, dd, gb, hold, r2, len(idx) / days)
        passed = None
        for lab in ("primary", f"model T={T}"):
            print(f"  {lab}:")
            for nm in sets:
                st, eq, tk, dd, gb, hold, r2, pd_ = rows[(nm, lab)]
                print(f"     {nm:24s} trades {st['n']:5d} ({pd_:4.2f}/day, hold {hold:4.1f}h)  gross {gb:+6.1f} bps  net R {st['mean']:+.3f} (t {st['t']:+.2f})  +2bps R {r2:+.3f}  $10 -> {eq:7.2f} ({tk} taken, DD {dd * 100:.0f}%)")
        j = [rows[(nm, f"model T={T}")] for nm in sets]
        passed = (j[2][0]["n"] >= 300 and j[2][0]["mean"] > 0 and j[2][0]["t"] >= 2.0 and j[0][0]["n"] >= 30 and j[0][0]["mean"] > 0 and j[1][0]["n"] >= 30
                  and j[1][0]["mean"] > 0 and all(x[1] > 10 and x[3] < 0.4 for x in j) and j[2][6] > 0)
        print(f"  PASS (model): {'YES' if passed else 'no'}")
        d3 = data["holdout2"][B]; idx = take(d3, d3["pred"], T, T_J3C)
        print("  model trades at a lower round-trip cost (fresh coins, optimistic): " + "  ".join(
            f"{rt}bps: ${money(d3['e'][idx], d3['x'][idx], d3['r'][idx] + (14 - rt) / 1e4, d3['s'][idx], d3['cid'][idx])[0]:.2f}" for rt in (10, 6, 3)))


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "test": sys.exit(1 if test() else 0)
    {"build": build, "fit": fit, "judge": judge}[cmd]()
