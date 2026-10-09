"""Round 4 (PREREG_round4.md): loss anatomy, causes and re-simulated fixes for the 10 candidates, judged on HOLDOUT2.
  python3 r4.py events GROUP        -> base events + entry features for every candidate (GROUP = design | unseen | holdout2)
  python3 r4.py anatomy             -> Truth 1 on DEV (all families + candidates)
  python3 r4.py fix                 -> Truths 2-3 on DEV/VAL: exit x width x execution grid, causes, frozen rules -> r4_frozen.json
  python3 r4.py judge               -> Truth 4: frozen rules once on HOLDOUT2 (+ seen-before FINAL / UNSEEN, RANDOM control)"""
import os, sys, json, pickle, itertools
import numpy as np
import numba as nb
import hf_core as C
import hf_fam as F
import hf2_feat as FT
import hf2_setups as S2
import r3_common as R3
import r3_liq as LQ

HOLDOUT2 = "AAVE BCH CRV ETC FIL HBAR ICP RUNE TRX UNI".split()
GROUPS = {"design": C.DESIGN, "unseen": C.UNSEEN, "holdout2": HOLDOUT2}
OUT = os.path.join(C.DATA, "r4")
HERE = os.path.dirname(os.path.abspath(__file__))
# (name, kind, round-2 setup id / params, side, tf, own exit)
CANDIDATES = [("MAGNET_S", "magnet", None, -1, 5, "own"),
              ("FAKE_PD_L_5", "r2", 7, 1, 5, "LVL"), ("FAKE_PD_S_5", "r2", 7, -1, 5, "LVL"),
              ("FAKE_PD_L_15", "r2", 7, 1, 15, "LVL"), ("FAKE_PD_S_15", "r2", 7, -1, 15, "LVL"),
              ("BB_FADE_L_5", "r2", 24, 1, 5, "TRAIL"), ("BB_FADE_S_5", "r2", 24, -1, 5, "TRAIL"),
              ("CVD_DIV_L_15", "r2", 19, 1, 15, "TRAIL"), ("CVD_DIV_S_15", "r2", 19, -1, 15, "TRAIL"),
              ("TRENDDAY_L", "trendday", None, 1, 5, "none"),
              ("RANDOM_L_15", "r2", 25, 1, 15, "LVL"), ("RANDOM_S_15", "r2", 25, -1, 15, "LVL")]
FEATS = ["stop_pct", "regime1h", "volratio", "hour", "weekday", "coin24h_s", "btc24h_s", "coin4h_s", "fund_s", "relvol1h", "vwap_dist_s",
         "ema50d_dist_s", "trend1d_s"]
CATEG = {"regime1h", "hour", "weekday", "trend1d_s"}
TARGETS = ["own", "1", "1.5", "2", "3", "none"]
SEGS4 = ("OLD", "DEV", "VAL", "FINAL")


# ------------------------------------------------------------------ entry features (13 causes)
def coin_context(co, btc_c, ft, fr):
    ctx = {}
    O1, H1, L1, C1, *_ = C.aggregate(co.o, co.h, co.l, co.c, co.qv, co.n, co.tb, 60)
    e50 = C.ema(C1, 50); e200 = C.ema(C1, 200); ax = F.adx(H1, L1, C1, 14); a1 = C.atr_wilder(H1, L1, C1, 14)
    rise = e50 - F.shift(e50, 5)
    reg = np.where((C1 > e200) & (e50 > e200) & (rise > 0) & (ax >= 20), 0, np.where((C1 < e200) & (e50 < e200) & (rise < 0) & (ax >= 20), 1,
                   np.where(ax < 20, 2, 3)))
    reg[:200] = 3; ctx["reg"] = reg
    with np.errstate(all="ignore"):
        atrp = a1 / C1; ctx["volr"] = atrp / F.daily_quantile(atrp, 24, 720, 0.5)
    Od, Hd, Ld, Cd, *_ = C.aggregate(co.o, co.h, co.l, co.c, co.qv, co.n, co.tb, 1440)
    ctx["atrd"] = C.atr_wilder(Hd, Ld, Cd, 14); ctx["e50d"] = C.ema(Cd, 50); ctx["e20d"] = C.ema(Cd, 20); ctx["Cd"] = Cd
    vw, _ = FT.anchored_vwap(co.h, co.l, co.c, co.qv, 1440, 0); ctx["vwap"] = vw
    q = np.where(np.isfinite(co.qv), co.qv, 0.0)
    cs = np.concatenate([[0.0], np.cumsum(q)]); ctx["cs"] = cs
    ctx["c"] = co.c; ctx["btc"] = btc_c; ctx["ft"] = ft; ctx["fr"] = fr
    return ctx


def features(ctx, i1, side, d_pct):
    i = i1 - 1                                                                 # last closed minute before the entry
    hb = np.clip(i1 // 60 - 1, 0, len(ctx["reg"]) - 1); db = np.clip(i1 // 1440 - 1, 0, len(ctx["Cd"]) - 1)
    c = ctx["c"]
    def back(arr, m):
        j = np.clip(i - m, 0, len(arr) - 1)
        with np.errstate(all="ignore"): return arr[i] / arr[j] - 1
    with np.errstate(all="ignore"):
        cs = ctx["cs"]
        rv = ((cs[i1] - cs[np.clip(i1 - 60, 0, None)]) / 60) / ((cs[i1] - cs[np.clip(i1 - 1440, 0, None)]) / 1440)
        k = np.searchsorted(ctx["ft"], C.GRID_T0 + i1 * 60000, side="right") - 1
        fund = np.where(k >= 0, ctx["fr"][np.clip(k, 0, len(ctx["fr"]) - 1)], np.nan)
        A = ctx["atrd"][db]
        tr = np.where((ctx["Cd"][db] > ctx["e50d"][db]) & (ctx["e20d"][db] > ctx["e50d"][db]), 1,
                      np.where((ctx["Cd"][db] < ctx["e50d"][db]) & (ctx["e20d"][db] < ctx["e50d"][db]), -1, 0))
        X = np.column_stack([d_pct, ctx["reg"][hb], ctx["volr"][hb], (i1 // 60) % 24, ((i1 // 1440) + 4) % 7,
                             side * back(c, 1440), side * back(ctx["btc"], 1440), side * back(c, 240), side * fund, rv,
                             side * (c[i] - ctx["vwap"][i]) / A, side * (c[i] - ctx["e50d"][db]) / A, side * tr])
    return X.astype(np.float64)


# ------------------------------------------------------------------ base events per candidate
def r2_events(name, k, sid, side):
    path = os.path.join(S2.OUT, f"ev_{name}_{k}.npz")
    if not os.path.exists(path):
        S2.run_coin(name, C.load_coin("BTC").c, ks=(k,))
    d = np.load(path)
    m = (d["sid"] == sid) & (d["side"] == side)
    j = d["j"][m]; i1 = (j + 1) * k
    pe = d["sf"][m] * 0 + 1                                                    # placeholder, real pe filled by caller from 1m opens
    return dict(i1=i1, side=np.full(m.sum(), side), d0_frac=d["sf"][m], datr=d["datr"][m], T=d["T"][m], tmax=np.full(m.sum(), 48 * k), k=k)


def magnet_events(co, oi):
    O, H, L, Cc, Q, NN, TB = C.aggregate(co.o, co.h, co.l, co.c, co.qv, co.n, co.tb, 5)
    n = len(Cc); fin = Cc[np.isfinite(Cc)]
    p0 = fin.min() * 0.5; nbins = int((np.log(fin.max() * 2) - np.log(p0)) / LQ.STEP) + 2
    v24 = F.rolling_sum(np.where(np.isfinite(Q), Q, 0.0), 288); atr5 = C.atr_wilder(H, L, Cc, 14)
    oi = oi[:n] if len(oi) >= n else np.concatenate([oi, np.full(n - len(oi), np.nan)])
    clrL, clrS, A, B, pa, pb = LQ.engine(O, H, L, Cc, Q, TB, oi, 72.0, p0, nbins)
    with np.errstate(all="ignore"): XB = B / v24
    dec = np.arange(2, n, 3); thr = np.full(n, np.nan); thr[dec] = F.daily_quantile(XB[dec], 96, 30 * 96, 0.8)
    cond = np.isfinite(XB) & np.isfinite(thr) & (XB > thr) & (B > 0) & (B >= 4 * A) & np.isfinite(pb) & np.isfinite(atr5)
    j = np.flatnonzero(cond); i1 = (j + 1) * 5
    ok = i1 < len(co.o); j, i1 = j[ok], i1[ok]
    pe = co.o[i1]; dist = -(pb[j] - pe)
    ok = np.isfinite(pe) & (dist > 0.003 * pe); j, i1, pe, dist = j[ok], i1[ok], pe[ok], dist[ok]
    return dict(i1=i1, side=np.full(len(j), -1), d0_frac=dist / pe, atr=atr5[j], T=pb[j], tmax=np.full(len(j), 1440), k=5)


def trendday_events(co):
    nd = co.N // 1440
    O, H, L, Cc, *_ = C.aggregate(co.o, co.h, co.l, co.c, co.qv, co.n, co.tb, 1440)
    atr = C.atr_wilder(H, L, Cc, 14); A = np.full(nd, np.nan); A[1:] = atr[:nd - 1]
    h2 = co.h[:nd * 1440].reshape(nd, 1440); l2 = co.l[:nd * 1440].reshape(nd, 1440)
    T = 480
    with np.errstate(all="ignore"):
        hi = np.nanmax(h2[:, :T], 1); lo = np.nanmin(l2[:, :T], 1); valid = np.isfinite(h2[:, :T]).sum(1) >= 0.95 * T
        op = co.o[:nd * 1440].reshape(nd, 1440)[:, 0]; P = co.c[:nd * 1440].reshape(nd, 1440)[:, T - 1]
        cond = valid & np.isfinite(op) & np.isfinite(P) & np.isfinite(A) & (hi > lo) & (P - op >= 0.5 * A) & ((P - lo) / (hi - lo) >= 0.75)
    d = np.flatnonzero(cond); i1 = d * 1440 + T; pe = co.o[i1]
    stop = lo[d] - 0.1 * A[d]; dist = pe - stop
    ok = np.isfinite(pe) & (dist > 0) & (dist <= 0.06 * pe)
    O5, H5, L5, C5, *_ = C.aggregate(co.o, co.h, co.l, co.c, co.qv, co.n, co.tb, 5); a5 = C.atr_wilder(H5, L5, C5, 14)
    return dict(i1=i1[ok], side=np.ones(ok.sum(), np.int64), d0_frac=(dist / pe)[ok], atr=a5[np.clip(i1[ok] // 5 - 1, 0, None)],
                T=np.full(ok.sum(), np.nan), tmax=np.full(ok.sum(), 1440 - T), k=5)


def build_events(group):
    names = GROUPS[group]; btc = C.load_coin("BTC")
    os.makedirs(OUT, exist_ok=True)
    allev = {c[0]: [] for c in CANDIDATES}
    for cid, nm in enumerate(names):
        co = btc if nm == "BTC" else C.load_coin(nm); ft, fr = C.load_funding(nm)
        ctx = coin_context(co, btc.c, ft, fr)
        for cname, kind, sid, side, k, own in CANDIDATES:
            if kind == "r2": e = r2_events(nm, k, sid, side)
            elif kind == "magnet": e = magnet_events(co, C.load_oi(nm))
            else: e = trendday_events(co)
            i1 = e["i1"]; ok = i1 < co.N
            e = {kk: (v[ok] if isinstance(v, np.ndarray) else v) for kk, v in e.items()}
            i1 = e["i1"]; pe = co.o[i1]; okp = np.isfinite(pe)
            e = {kk: (v[okp] if isinstance(v, np.ndarray) else v) for kk, v in e.items()}; i1 = e["i1"]; pe = co.o[i1]
            if "atr" not in e: e["atr"] = e["d0_frac"] * pe / e["datr"]               # signal-timeframe ATR from the stored d/ATR
            e["P"] = co.c[i1 - 1]; e["pe"] = pe; e["coin"] = np.full(len(i1), cid)
            e["X"] = features(ctx, i1, e["side"], e["d0_frac"])
            allev[cname].append(e)
        print(group, nm, flush=True)
    out = {}
    for cname, parts in allev.items():
        out[cname] = {kk: np.concatenate([p[kk] for p in parts]) for kk in parts[0] if kk != "k"}
        out[cname]["k"] = parts[0]["k"]
    pickle.dump(out, open(os.path.join(OUT, f"events_{group}.pkl"), "wb"))


# ------------------------------------------------------------------ trade walk with every fix option
@nb.njit(cache=True)
def walk(o, h, l, c, f, last, side, pe, d, tgt, trail, be):
    """from fill minute f: stop at pe - side*d, target price tgt (0 none), chandelier trail distance (0 none), breakeven after +be*d (0 off).
    returns exit minute, exit price, reason (0 time 1 stop 2 target), MFE R, MAE R"""
    st = pe - side * d; ext = pe; mfe = 0.0; mae = 0.0; armed = False
    for b in range(f, last + 1):
        if np.isnan(h[b]) or np.isnan(l[b]) or np.isnan(o[b]): continue
        if b > f and side * (o[b] - st) <= 0: return b, o[b], 1, mfe, mae
        adv = side * ((l[b] if side == 1 else h[b]) - pe) / d
        if -adv > mae: mae = -adv
        if side * ((l[b] if side == 1 else h[b]) - st) <= 0: return b, st, 1, mfe, max(mae, side * (pe - st) / d)
        if tgt > 0 and side * ((h[b] if side == 1 else l[b]) - tgt) >= 0: return b, tgt, 2, max(mfe, side * (tgt - pe) / d), mae
        fav = side * ((h[b] if side == 1 else l[b]) - pe) / d
        if fav > mfe: mfe = fav
        if side == 1 and h[b] > ext: ext = h[b]
        if side == -1 and l[b] < ext: ext = l[b]
        if trail > 0: st = max(st, ext - trail) if side == 1 else min(st, ext + trail)
        if be > 0 and not armed and mfe >= be: armed = True; st = max(st, pe) if side == 1 else min(st, pe)
    for b in range(last, f - 1, -1):
        if not np.isnan(c[b]): return b, c[b], 0, mfe, mae
    return -1, 0.0, -1, mfe, mae


@nb.njit(cache=True)
def simulate(o, h, l, c, i1s, sides, coin_off, P, pe0, d0f, atr, T, tmax, k, tgt_mode, width, be, tmult, maker, trail_mode, t0, ncoin):
    """tgt_mode: -1 own level/LVL rule, -2 none, >0 target in R. trail_mode: 1 = chandelier 3 ATR (signal timeframe).
    o/h/l/c are concatenated 1m arrays of all coins; coin_off maps coin -> offset. returns ent, ext, net, sf, ok, reason, mfe, mae"""
    n = len(i1s)
    ent = np.zeros(n, np.int64); ext = np.zeros(n, np.int64); net = np.zeros(n); sf = np.zeros(n); ok = np.zeros(n, np.bool_)
    rs = np.full(n, -1, np.int64); mf = np.zeros(n); ma = np.zeros(n)
    for e in range(n):
        off = coin_off[e]; side = sides[e]
        f = off + i1s[e]; px = pe0[e]; isM = False
        if maker:
            pen = max(0.05 * atr[e] * (5.0 / k) ** 0.5, 1e-4 * P[e])
            f = -1
            for b in range(off + i1s[e], off + i1s[e] + 3):
                if np.isnan(l[b]): continue
                if (side == 1 and l[b] <= P[e] - pen) or (side == -1 and h[b] >= P[e] + pen): f = b; break
            if f < 0: continue
            px = P[e]; isM = True
        d = width * d0f[e] * px
        tg = 0.0
        if tgt_mode == -1:
            if np.isfinite(T[e]) and side * (T[e] - px) >= d: tg = T[e]
            elif np.isfinite(T[e]): tg = px + side * d
            else: tg = px + side * 2 * d
        elif tgt_mode > 0: tg = px + side * tgt_mode * d
        tr = 3.0 * atr[e] if trail_mode == 1 else 0.0
        last = min(f + int(tmax[e] * tmult) - 1, off + ncoin - 1)          # never walk into the next coin's minutes
        xb, xp, r, mfe, mae = walk(o, h, l, c, f, last, side, px, d, tg, tr, be)
        if r < 0: continue
        te = t0 + (f - off) * 60000; tx = t0 + (xb - off + 1) * 60000
        gross = side * (xp / px - 1.0)
        net[e] = gross - (C.MAKER if isM else C.TAKER) - C.TAKER
        ent[e] = te; ext[e] = tx; sf[e] = d / px; ok[e] = True; rs[e] = r; mf[e] = mfe; ma[e] = mae
    return ent, ext, net, sf, ok, rs, mf, ma


class Market:
    """concatenated 1m arrays of a coin group and their funding"""
    def __init__(self, group):
        names = GROUPS[group]; self.names = names
        os_, hs, ls, cs, offs = [], [], [], [], []; off = 0; self.fund = []
        for nm in names:
            co = C.load_coin(nm)
            os_.append(co.o); hs.append(co.h); ls.append(co.l); cs.append(co.c); offs.append(off); off += co.N
            self.fund.append(C.load_funding(nm))
        self.o = np.concatenate(os_); self.h = np.concatenate(hs); self.l = np.concatenate(ls); self.c = np.concatenate(cs)
        self.off = np.array(offs, np.int64)

    def funding(self, coin, ent, ext, side):
        out = np.zeros(len(ent))
        for cid in np.unique(coin):
            m = coin == cid; ft, fr = self.fund[cid]
            lo = np.searchsorted(ft, ent[m], side="right"); hi = np.searchsorted(ft, ext[m], side="right")
            cum = np.concatenate([[0.0], np.cumsum(fr)])
            out[m] = side[m] * (cum[hi] - cum[lo])
        return out


def run_rule(mk, ev, own, rule, mask=None):
    """re-simulate every event of one candidate under one rule; then one position per coin. returns dict of trades"""
    tgt = rule["target"]
    tmode = (-1 if own == "LVL" or own == "own" else -2) if tgt == "own" else (-2 if tgt == "none" else float(tgt))
    trail = 1 if (tgt == "own" and own == "TRAIL") else 0
    if tgt == "own" and own == "none": tmode = -2
    sel = np.ones(len(ev["i1"]), bool) if mask is None else mask
    idx = np.flatnonzero(sel)
    ent, ext, net, sf, ok, rs, mf, ma = simulate(mk.o, mk.h, mk.l, mk.c, ev["i1"][idx], ev["side"][idx], mk.off[ev["coin"][idx]], ev["P"][idx],
                                                 ev["pe"][idx], ev["d0_frac"][idx], ev["atr"][idx], ev["T"][idx], ev["tmax"][idx].astype(np.float64),
                                                 ev["k"], tmode, rule["width"], 1.0 if rule["be"] else 0.0, rule["tmult"], rule["maker"], trail,
                                                 C.GRID_T0, C.GRID_N)
    idx = idx[ok]; ent, ext, net, sf, rs, mf, ma = ent[ok], ext[ok], net[ok], sf[ok], rs[ok], mf[ok], ma[ok]
    net = net - mk.funding(ev["coin"][idx], ent, ext, ev["side"][idx])
    coin = ev["coin"][idx]
    o = np.lexsort((ent, coin)); keep = R3.nonoverlap(coin[o], ent[o], ext[o]); o = o[keep]
    return dict(idx=idx[o], ent=ent[o], ext=ext[o], net=net[o], net2=net[o] - 0.0004, sf=sf[o], slot=coin[o], rs=rs[o], mfe=mf[o], mae=ma[o])


def segstats(tr, seg):
    m = C.seg_mask(tr["ent"], seg)
    if m.sum() < 2: return dict(n=int(m.sum()), mean=np.nan, t=np.nan)
    R = tr["net"][m] / tr["sf"][m]
    st = C.cluster_stats(R, tr["ent"][m] // C.DAY); st["win"] = float(np.mean(R > 0))
    return st


BASE = dict(target="own", width=1.0, be=False, tmult=1.0, maker=False)


# ------------------------------------------------------------------ Truth 1
def anatomy():
    print("# Truth 1a: all round-2 families, DEV, design coins, regime ALL, no filter, exit LVL: where the R goes")
    import hf2_eval as E2
    ev = E2.load(C.DESIGN); cells = E2.Cells(ev)
    print(f"  {'setup':12s} {'tf':>3s} {'trades':>7s} {'net R':>7s} = {'gross':>7s} - {'fees':>6s} - {'funding':>7s} | {'loss left at zero fees':>22s}")
    for sid in range(26):
        for tf in (5, 15):
            I = np.concatenate([cells.trades(sid, s, tf, "ALL", "NONE", 2)[0] for s in (1, -1)])
            I = I[C.seg_mask(ev["ent"][I], "DEV")]
            sf = ev["sf"][I]; g = ev["gr"][I, 2] / sf; fee = 0.0014 / sf; fu = ev["fu"][I, 2] / sf
            print(f"  {S2.SETUPS[sid]:12s} {tf:3d} {len(I):7d} {np.mean(g - fee - fu):+7.3f} = {np.mean(g):+7.3f} - {np.mean(fee):6.3f} - {np.mean(fu):+7.3f} | "
                  f"{np.mean(g - fu):+22.3f}")
    print("\n# Truth 1b: the 10 candidates (+ RANDOM), base rule, DEV: loss anatomy")
    evs = pickle.load(open(os.path.join(OUT, "events_design.pkl"), "rb")); mk = Market("design")
    print(f"  {'candidate':13s} {'n':>5s} {'net R':>6s} {'gross':>6s} {'fee R':>6s} {'stop%':>5s} | {'straight-to-stop':>16s} {'gave-back':>9s} {'timeout':>7s} "
          f"{'win':>4s} {'winners near stop':>17s} | {'MFE median (losers)':>19s}")
    for cname, kind, sid, side, k, own in CANDIDATES:
        ev = evs[cname]; tr = run_rule(mk, ev, own, BASE)
        m = C.seg_mask(tr["ent"], "DEV"); R = tr["net"][m] / tr["sf"][m]; fee = (0.0014 / tr["sf"][m])
        los = R <= 0; win = R > 0
        s2s = np.mean((tr["mfe"][m] < 0.3) & los); gb = np.mean((tr["mfe"][m] >= 1.0) & los); to = np.mean((tr["rs"][m] == 0) & los)
        near = np.mean(tr["mae"][m][win] >= 0.8) if win.sum() else np.nan
        print(f"  {cname:13s} {m.sum():5d} {R.mean():+6.3f} {np.mean(R + fee):+6.3f} {fee.mean():6.3f} {np.median(tr['sf'][m]) * 100:5.2f} | {s2s * 100:15.0f}% {gb * 100:8.0f}% "
              f"{to * 100:6.0f}% {win.mean() * 100:3.0f}% {near * 100:16.0f}% | {np.median(tr['mfe'][m][los]):19.2f}")


# ------------------------------------------------------------------ Truths 2-3
def grid():
    for tgt, w, be, tm, mkr in itertools.product(TARGETS, (1.0, 1.5, 2.0), (False, True), (0.5, 1.0, 2.0), (False, True)):
        yield dict(target=tgt, width=w, be=be, tmult=tm, maker=mkr)


def rname(r): return f"tgt={r['target']} w={r['width']:g} be={'on' if r['be'] else 'off'} t={r['tmult']:g}x {'maker' if r['maker'] else 'taker'}"


def cuts(x, cat):
    if cat: return None
    return np.nanpercentile(x, [100 / 3, 200 / 3])


def groups_of(x, cat, cp):
    if cat: return x
    return np.where(np.isnan(x), -1, np.digitize(x, cp))


def fix(only):
    evs = pickle.load(open(os.path.join(OUT, "events_design.pkl"), "rb")); mk = Market("design")
    frozen = {}
    for cname, kind, sid, side, k, own in CANDIDATES:
        if cname.startswith("RANDOM") or cname not in only: continue
        ev = evs[cname]
        et = C.GRID_T0 + ev["i1"] * 60000
        dv = (et >= C.SEG["DEV"][0]) & (et < C.SEG["VAL"][1])                  # selection sees DEV and VAL only
        base = run_rule(mk, ev, own, BASE, dv); sb, svb = segstats(base, "DEV"), segstats(base, "VAL")
        print(f"\n## {cname}: base rule  DEV n {sb['n']} R {sb['mean']:+.3f} t {sb['t']:+.2f} | VAL n {svb['n']} R {svb['mean']:+.3f} t {svb['t']:+.2f}")
        res = []
        for r in grid():
            tr = run_rule(mk, ev, own, r, dv)
            sd, sv = segstats(tr, "DEV"), segstats(tr, "VAL")
            m = C.seg_mask(tr["ent"], "DEV") | C.seg_mask(tr["ent"], "VAL")
            pooled = float(np.mean(tr["net"][m] / tr["sf"][m])) if m.sum() else np.nan
            res.append((r, sd, sv, pooled))
        okr = [x for x in res if x[1]["n"] >= 100 and x[2]["n"] >= 100 and x[1]["mean"] > 0 and x[2]["mean"] > 0]
        top = sorted(res, key=lambda x: -x[3] if np.isfinite(x[3]) else 1e9)[:5]
        print(f"  grid: {len(res)} rules; positive in both DEV and VAL (n >= 100): {len(okr)}")
        for r, sd, sv, p in top:
            print(f"    top pooled: {rname(r):45s} DEV n {sd['n']:5d} R {sd['mean']:+.3f} t {sd['t']:+.2f} | VAL n {sv['n']:5d} R {sv['mean']:+.3f} t {sv['t']:+.2f}")
        if not okr:
            print("  -> not fixable by exits / stop width / execution"); frozen[cname] = None; continue
        r, sd, sv, p = max(okr, key=lambda x: x[3])
        print(f"  chosen: {rname(r)}  DEV R {sd['mean']:+.3f} (t {sd['t']:+.2f}) VAL R {sv['mean']:+.3f} (t {sv['t']:+.2f})")
        # Truth 2: causes on top of the chosen rule
        tr = run_rule(mk, ev, own, r, dv)
        X = ev["X"][tr["idx"]]; R = tr["net"] / tr["sf"]
        dev = C.seg_mask(tr["ent"], "DEV"); val = C.seg_mask(tr["ent"], "VAL")
        accepted = []
        print(f"  causes (net R per group, DEV / VAL):")
        cand = []
        for q, fn in enumerate(FEATS):
            cat = fn in CATEG
            cp = cuts(X[dev, q], cat)
            g = groups_of(X[:, q], cat, cp)
            gs = sorted(set(g[dev].tolist()))
            line = []
            worst = None
            for gv in gs:
                md = dev & (g == gv); mv = val & (g == gv)
                if md.sum() < 20: continue
                st = C.cluster_stats(R[md], tr["ent"][md] // C.DAY)
                rv = R[mv].mean() if mv.sum() else np.nan
                line.append(f"{gv}:{st['mean']:+.2f}/{rv:+.2f}")
                if worst is None or st["t"] < worst[1]: worst = (gv, st["t"], rv)
            if not cat or fn in ("regime1h", "trend1d_s"): print(f"    {fn:14s} " + "  ".join(line))
            if worst and worst[1] <= -2 and worst[2] < 0:
                keep = g != worst[0]
                gain_d = R[dev & keep].mean() - R[dev].mean(); gain_v = R[val & keep].mean() - R[val].mean()
                if gain_d > 0 and gain_v > 0: cand.append((worst[1], fn, worst[0], None if cat else cp.tolist(), gain_d, gain_v))
        for t_, fn, gv, cp, gd, gvv in sorted(cand)[:2]:
            accepted.append(dict(feature=fn, group=int(gv), cuts=cp))
            print(f"  ACCEPTED cause: {fn} group {gv} (DEV t {t_:+.2f}); removing it: DEV +{gd:.3f}R, VAL +{gvv:.3f}R")
        if not cand: print("  no cause passes the DEV t <= -2 / VAL negative / both-improve rule")
        frozen[cname] = dict(rule=r, causes=accepted)
        if accepted:
            mask = filter_mask(ev, accepted)
            tr2 = run_rule(mk, ev, own, r, mask & dv)
            print(f"  with filters: DEV {segstats(tr2, 'DEV')['mean']:+.3f} (n {segstats(tr2, 'DEV')['n']}), VAL {segstats(tr2, 'VAL')['mean']:+.3f} (n {segstats(tr2, 'VAL')['n']})")
    json.dump(frozen, open(os.path.join(OUT, f"frozen_{'_'.join(only)}.json"), "w"), indent=1)


def filter_mask(ev, causes):
    keep = np.ones(len(ev["i1"]), bool)
    for c_ in causes:
        q = FEATS.index(c_["feature"]); x = ev["X"][:, q]
        g = x if c_["cuts"] is None else np.where(np.isnan(x), -1, np.digitize(x, c_["cuts"]))
        keep &= g != c_["group"]
    return keep


# ------------------------------------------------------------------ Truth 4
def money(tr, seg, risk=0.005):
    m = C.seg_mask(tr["ent"], seg) if seg != "ALL" else np.ones(len(tr["ent"]), bool)
    if m.sum() < 1: return 10.0, 0, 0.0
    eq, taken, mdd, _, _ = C.portfolio(tr["ent"][m], tr["ext"][m], tr["net"][m], tr["sf"][m], tr["slot"][m], 10.0, risk, 5, 3.0, 6.0)
    return eq, taken, mdd


def judge():
    frozen = json.load(open(os.path.join(HERE, "r4_frozen.json")))                 # merged by `python3 r4.py merge`
    mks = {g: Market(g) for g in ("design", "unseen", "holdout2")}
    evs = {g: pickle.load(open(os.path.join(OUT, f"events_{g}.pkl"), "rb")) for g in mks}
    summary = []
    for cname, kind, sid, side, k, own in CANDIDATES:
        if cname.startswith("RANDOM"): continue
        fz = frozen.get(cname)
        if fz is None: print(f"\n## {cname}: NOT FIXABLE (no rule positive in both DEV and VAL)"); continue
        filt = ", ".join(f"skip {c_['feature']} group {c_['group']}" for c_ in fz["causes"]) or "none"
        print(f"\n## {cname}: {rname(fz['rule'])} | filters: {filt}")
        rule, causes = fz["rule"], fz["causes"]
        rows = {}
        for g in ("holdout2", "design", "unseen"):
            ev = evs[g][cname]; mask = filter_mask(ev, causes) if causes else None
            tr = run_rule(mks[g], ev, own, rule, mask)
            trb = run_rule(mks[g], ev, own, BASE)
            segs = SEGS4 if g == "holdout2" else (("FINAL",) if g == "unseen" else ("FINAL",))
            for s in segs:
                st = segstats(tr, s); stb = segstats(trb, s)
                eq, nt, mdd = money(tr, s); eqb, ntb, _ = money(trb, s)
                lab = f"{'HOLDOUT2' if g == 'holdout2' else ('design (seen)' if g == 'design' else 'UNSEEN (seen)')} {s}"
                print(f"  {lab:22s} fixed: n {st['n']:5d} R {st['mean']:+.3f} t {st['t']:+.2f} $10 -> {eq:7.2f} ({nt} tr, DD {mdd * 100:.0f}%) | base: R {stb['mean']:+.3f} $10 -> {eqb:7.2f} ({ntb} tr)")
                rows[(g, s)] = (st, eq, nt, mdd)
            if g == "holdout2":
                m = np.ones(len(tr["ent"]), bool)
                R = tr["net"] / tr["sf"]; st = C.cluster_stats(R, tr["ent"] // C.DAY)
                R2 = tr["net2"] / tr["sf"]
                eq, nt, mdd = money(tr, "ALL")
                pos = sum(rows[("holdout2", s)][0]["mean"] > 0 for s in SEGS4 if rows[("holdout2", s)][0]["n"] > 1)
                passed = st["n"] >= 150 and st["mean"] > 0 and st["t"] >= 2.0 and pos >= 3 and R2.mean() > 0 and eq > 10 and mdd < 0.4
                print(f"  HOLDOUT2 2021-2026 pooled: n {st['n']} R {st['mean']:+.3f} t {st['t']:+.2f}, +2bps R {R2.mean():+.3f}, $10 -> {eq:.2f} "
                      f"({nt} trades, DD {mdd * 100:.0f}%), positive periods {pos}/4 -> PASS: {'YES' if passed else 'no'}")
                summary.append((cname, st, eq, nt, mdd, pos, passed))
        # RANDOM control with the same rule (15m random entries, same side)
        rc = "RANDOM_L_15" if side == 1 else "RANDOM_S_15"
        ev = evs["holdout2"][rc]; tr = run_rule(mks["holdout2"], ev, "LVL", rule, filter_mask(ev, causes) if causes else None)
        R = tr["net"] / tr["sf"]; st = C.cluster_stats(R, tr["ent"] // C.DAY)
        print(f"  RANDOM control, same fixes, HOLDOUT2: n {st['n']} R {st['mean']:+.3f} t {st['t']:+.2f}")
    print("\n# Summary (HOLDOUT2, 10 fresh coins, 2021-01..2026-09)")
    for cname, st, eq, nt, mdd, pos, passed in summary:
        print(f"  {cname:13s} trades {st['n']:6d} net R {st['mean']:+.3f} t {st['t']:+.2f}  $10 -> {eq:7.2f} ({nt} taken, DD {mdd * 100:.0f}%)  periods +{pos}/4  {'PASS' if passed else 'fail'}")


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "events": build_events(sys.argv[2])
    elif cmd == "fix": fix(sys.argv[2].split(","))
    elif cmd == "merge":
        import glob
        fz = {}
        for f in sorted(glob.glob(os.path.join(OUT, "frozen_*.json"))): fz.update(json.load(open(f)))
        json.dump(fz, open(os.path.join(HERE, "r4_frozen.json"), "w"), indent=1); print(len(fz), "frozen candidates")
    else: {"anatomy": anatomy, "judge": judge}[cmd]()
