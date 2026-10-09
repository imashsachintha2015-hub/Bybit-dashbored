"""Round 5 (PREREG_round5.md) data prep: production 4H Kalman trades of a coin group, the 11 trend-birth indicators at each signal bar,
and the level-based add-on trades. usage: python3 r5_prep.py design|unseen|holdout2"""
import os, sys, pickle
import numpy as np
import hf_core as C
import hf_fam as F
import hf_ladder as LD
import r4

B = 240
BMS = B * 60000
OUT = os.path.join(C.DATA, "r5")
FEATS5 = ["AGE", "MAT", "AVD", "RNG", "BRK", "VP", "OI24", "CVD", "BREADTH", "VOLR", "ZJUMP"]
LEVELS = ("AVWAP", "EMA20", "FIB50")
STOPS = ("S3", "STRUCT")


def bars4h(co):
    O, H, L, Cc, Q, NN, TB = C.aggregate(co.o, co.h, co.l, co.c, co.qv, co.n, co.tb, B)
    valid = ~(np.isnan(O) | np.isnan(H) | np.isnan(L) | np.isnan(Cc)); first = np.argmax(valid)
    O, _ = LD.ffill(O); H, _ = LD.ffill(H); L, _ = LD.ffill(L); Cc, _ = LD.ffill(Cc)
    O[:first] = Cc[:first] = H[:first] = L[:first] = Cc[first]; valid[:first] = False
    Q = np.where(np.isfinite(Q), Q, 0.0); TB = np.where(np.isfinite(TB), TB, 0.0)
    t = co.t0 + np.arange(len(O), dtype=np.int64) * BMS
    z = LD.kalman_z(Cc); a = LD.atr14_rma(H, L, Cc)
    return dict(O=O, H=H, L=L, C=Cc, Q=Q, TB=TB, t=t, z=z, a=a, valid=valid)


def run_start(z):
    """index of the first bar of the current run of same-sign z (NaN breaks a run)"""
    n = len(z); rs = np.zeros(n, np.int64); sg = np.sign(np.where(np.isfinite(z), z, 0.0))
    for i in range(n):
        rs[i] = rs[i - 1] if (i > 0 and sg[i] == sg[i - 1] and sg[i] != 0) else i
    return rs


def volume_profile(H, L, C_, Q, i, lookback=180, nb=60):
    lo = i - lookback; tp = (H[lo:i] + L[lo:i] + C_[lo:i]) / 3.0; w = Q[lo:i]
    pmin, pmax = tp.min(), tp.max()
    if pmax <= pmin or w.sum() <= 0: return np.nan, np.nan
    hist, edges = np.histogram(tp, bins=nb, range=(pmin, pmax), weights=w)
    p = int(np.argmax(hist)); lo_b = hi_b = p; acc = hist[p]; tot = hist.sum()
    while acc < 0.7 * tot and (lo_b > 0 or hi_b < nb - 1):
        up = hist[hi_b + 1] if hi_b < nb - 1 else -1.0; dn = hist[lo_b - 1] if lo_b > 0 else -1.0
        if up >= dn: hi_b += 1; acc += up
        else: lo_b -= 1; acc += dn
    return edges[hi_b + 1], edges[lo_b]                                          # VAH, VAL


def coin_rows(co, bar, ft, fr, oi, breadth_fn, trend_days, cid):
    ent, ext, ret, sf, rs, sd = LD.coin_rung(co, ft, fr, trend_days, B, with_side=True)
    H, L, Cc, O, Q, TB, z, a, t = bar["H"], bar["L"], bar["C"], bar["O"], bar["Q"], bar["TB"], bar["z"], bar["a"], bar["t"]
    n = len(Cc); rstart = run_start(z)
    csQ = np.concatenate([[0.0], np.cumsum(Q)])
    tp = (H + L + Cc) / 3.0
    with np.errstate(all="ignore"): v = np.where(tp > 0, Q / tp, 0.0)
    csv = np.concatenate([[0.0], np.cumsum(v)])
    atrp = a / Cc
    ema20 = C.ema(Cc, 20)
    rows = []; adds = {(l, s): [] for l in LEVELS for s in STOPS}
    for k in range(len(ent)):
        i = int((ent[k] - co.t0) // BMS) - 1; side = int(sd[k])
        if i < 600 or i + 1 >= n: continue
        b = int(rstart[i]); atr = a[i]
        avwap = (csQ[i + 1] - csQ[b]) / (csv[i + 1] - csv[b]) if csv[i + 1] > csv[b] else np.nan
        rng = (H[i - 59:i + 1].max() - L[i - 59:i + 1].min()) / atr
        if side == 1: brk = 2 if Cc[i] > H[i - 540:i].max() else (1 if Cc[i] > H[i - 180:i].max() else 0)
        else: brk = 2 if Cc[i] < L[i - 540:i].min() else (1 if Cc[i] < L[i - 180:i].min() else 0)
        vah, val = volume_profile(H, L, Cc, Q, i)
        vp = side * (Cc[i] - (vah if side == 1 else val)) / atr if np.isfinite(vah) else np.nan
        s_new = ((t[i] - co.t0) // 60000 + B) // 5 - 1; s_old = s_new - 6 * 48
        oi24 = np.nan
        if 0 <= s_old and s_new < len(oi) and np.isfinite(oi[s_new]) and np.isfinite(oi[s_old]) and oi[s_old] > 0 and oi[s_new] > 0:
            oi24 = np.log(oi[s_new] / oi[s_old])
        qs = Q[i - 5:i + 1].sum(); cvd = side * (2 * TB[i - 5:i + 1].sum() - qs) / qs if qs > 0 else np.nan
        volr = atrp[i] / np.median(atrp[i - 540:i])
        f = [i - b, side * (Cc[i] - Cc[b]) / atr, side * (Cc[i] - avwap) / atr if np.isfinite(avwap) else np.nan, rng, brk, vp, oi24, cvd,
             breadth_fn(i, side), volr, side * (z[i] - z[i - 1])]
        rows.append((cid, i, side, ent[k], ext[k], ret[k], sf[k], rs[k], b, *f))
        # ---- add-ons: first level touch and rejection inside the parent trade
        xb = int((ext[k] - co.t0) // BMS) - 1
        runmax = np.maximum.accumulate(H[b:]) if side == 1 else np.minimum.accumulate(L[b:])
        taken = set()                                                            # one add-on per parent per level
        for j in range(i + 2, min(xb - 1, n - 2)):
            if side == 1: rej = (Cc[j] > O[j])
            else: rej = (Cc[j] < O[j])
            if not rej: continue
            av = (csQ[j + 1] - csQ[b]) / (csv[j + 1] - csv[b]) if csv[j + 1] > csv[b] else np.nan
            ext_prev = runmax[j - 1 - b]
            lv = {"AVWAP": av, "EMA20": ema20[j], "FIB50": Cc[b] + 0.5 * (ext_prev - Cc[b])}
            for lname, level in lv.items():
                if not np.isfinite(level) or lname in taken: continue
                touch = (L[j] <= level and Cc[j] > level) if side == 1 else (H[j] >= level and Cc[j] < level)
                if not touch: continue
                taken.add(lname)
                for sname in STOPS:
                    r = add_on_trade(co, bar, ft, fr, j, side, sname)
                    if r is not None: adds[(lname, sname)].append((cid, k) + r)
    return rows, adds


def add_on_trade(co, bar, ft, fr, j, side, sname):
    """entry at the open of bar j+1; production exit rules (stop first, z crossing 0 -> next open, 120 bars)"""
    O, H, L, Cc, z, a, t = bar["O"], bar["H"], bar["L"], bar["C"], bar["z"], bar["a"], bar["t"]
    n = len(Cc)
    ep = O[j + 1]
    if sname == "S3":
        stop = Cc[j] - side * LD.STOP_ATR * a[j]
        if side * (ep - stop) < LD.MINSTOP * ep: stop = ep - side * LD.MINSTOP * ep
    else:
        stop = (min(L[j], L[j - 1]) - 0.5 * a[j]) if side == 1 else (max(H[j], H[j - 1]) + 0.5 * a[j])
        if side * (ep - stop) < LD.MINSTOP * ep: stop = ep - side * LD.MINSTOP * ep
        if side * (ep - stop) > 0.08 * ep: return None
    risk = side * (ep - stop)
    if risk <= 0: return None
    xt = None
    for q in range(j + 1, min(n, j + 1 + LD.HOLD)):
        if (side == 1 and L[q] <= stop) or (side == -1 and H[q] >= stop): xp, xt = stop, t[q] + BMS; break
        if (side == 1 and z[q] < 0) or (side == -1 and z[q] > 0):
            if q + 1 < n: xp, xt = O[q + 1], t[q + 1] + BMS
            break
    else:
        q = j + LD.HOLD
        if q <= n - 1: xp, xt = Cc[q], t[q] + BMS
    if xt is None: return None
    gross = side * (xp / ep - 1.0)
    te = t[j + 1]
    fnd = side * C.funding_paid(ft, fr, te, xt, 1)
    return (te, xt, gross - LD.FEE - fnd, risk / ep, side)


def build(group):
    names = r4.GROUPS[group]
    btc = C.load_coin("BTC"); trend_days = LD.btc_daily_trend(btc)
    coins = {nm: (btc if nm == "BTC" else C.load_coin(nm)) for nm in names}
    bars = {nm: bars4h(coins[nm]) for nm in names}
    n = min(len(b["z"]) for b in bars.values())
    Z = np.vstack([bars[nm]["z"][:n] for nm in names])
    def breadth_fn(i, side):
        col = Z[:, i]; ok = np.isfinite(col)
        return float(np.mean(side * col[ok] > 0)) if ok.sum() >= 3 else np.nan
    allrows = []; alladds = {(l, s): [] for l in LEVELS for s in STOPS}
    for cid, nm in enumerate(names):
        ft, fr = C.load_funding(nm); oi = C.load_oi(nm)
        rows, adds = coin_rows(coins[nm], bars[nm], ft, fr, oi, breadth_fn, trend_days, cid)
        allrows += rows
        for key, lst in adds.items(): alladds[key] += lst
        print(group, nm, len(rows), {f"{k[0]}/{k[1]}": len(v) for k, v in adds.items()}, flush=True)
    cols = ["coin", "i", "side", "ent", "ext", "ret", "sf", "rs", "birth"] + FEATS5
    arr = np.array(allrows, dtype=np.float64)
    tr = {c: arr[:, q] for q, c in enumerate(cols)}
    for c in ("coin", "i", "side", "ent", "ext", "rs", "birth"): tr[c] = tr[c].astype(np.int64)
    ad = {}
    for key, lst in alladds.items():
        if not lst: ad[key] = None; continue
        a_ = np.array(lst, dtype=np.float64)
        ad[key] = dict(coin=a_[:, 0].astype(np.int64), parent=a_[:, 1].astype(np.int64), ent=a_[:, 2].astype(np.int64), ext=a_[:, 3].astype(np.int64),
                       ret=a_[:, 4], sf=a_[:, 5], side=a_[:, 6].astype(np.int64))
    os.makedirs(OUT, exist_ok=True)
    pickle.dump(dict(trades=tr, addons=ad, names=names), open(os.path.join(OUT, f"r5_{group}.pkl"), "wb"))


if __name__ == "__main__":
    build(sys.argv[1])
