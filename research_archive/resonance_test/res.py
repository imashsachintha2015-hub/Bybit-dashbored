"""Rebuild of the 'Quantum AI - Resonance Engine' idea (TradingView promo): trade 15m structure breaks only when the
15m / 30m / 1H / 4H / 1D trends agree ('resonance'), optional ADX strength filter, TP1/TP2/TP3 ladder like the video.
Causal: a higher-timeframe bar is used only after it closes; a pivot only after it is confirmed (2 bars later).
Long logic; shorts = mirrored prices (o,h,l,c -> -o,-l,-h,-c). Output res_ev.pkl: one record per event with every exit variant.
  python3 res.py check   -> no-repaint test, 1D parity with scen.daily_trend, ladder hand-check
  python3 res.py         -> build all events"""
import json, os, sys, pickle, random, zlib
import numpy as np
from multiprocessing import Pool
REPO = os.environ.get("REPO", "/home/user/Bybit-dashbored")
sys.path.insert(0, f"{REPO}/research_archive/strategy_library")
from lib import ema, rma

FOLDER = os.environ.get("RES_DATA", "s15"); BAR = 900000; HR = 3600000; DAY = 86400000
TFS = (("15m", BAR), ("30m", 2 * BAR), ("1H", HR), ("4H", 4 * HR), ("1D", DAY))
WARM = 50                      # bars of a timeframe needed before its trend counts
HOLD = 96; MINSTOP = 0.003; BUF = 0.1
LADDER = ((1.0, 1 / 3), (1.5, 1 / 3), (2.25, 1 / 3))   # TP1 / TP2 / TP3 in R, share of the position
EXITS = ("L", "S1", "S2")      # ladder + breakeven after TP1 | all at 1.5R | all at 2.25R
RAND_P = 1 / 16                # share of bars sampled for the random-entry control

def load(sym, inv):
    d = np.array(json.load(open(f"{FOLDER}/{sym}_15m.json")), dtype=float)
    t = d[:, 0].astype(np.int64); o, h, l, c, v = [d[:, k].copy() for k in range(1, 6)]
    if inv: o, h, l, c = -o, -l, -h, -c
    return t, o, h, l, c, v

def tf_trend(t, c, P):
    """trend of the last CLOSED bar of period P at the close of each 15m bar (+1 / -1 / 0) and whether it is warmed up."""
    n = len(t)
    if P == BAR:
        C = c; idx = np.arange(n); days = None
    else:
        k = P // BAR; bid = t // P
        ub, st, cnt = np.unique(bid, return_index=True, return_counts=True)
        keep = cnt == k; ub = ub[keep]; st = st[keep]          # complete periods only
        C = c[st + k - 1]; ends = (ub + 1) * P
        idx = np.searchsorted(ends, t + BAR, side="right") - 1  # last period closed by the close of 15m bar i
        days = ub
    if len(C) == 0: return np.zeros(n, int), np.zeros(n, bool), None, None
    e20 = ema(C, 20); e50 = ema(C, 50)
    b = np.where((C > e50) & (e20 > e50), 1, np.where((C < e50) & (e20 < e50), -1, 0)); b[:WARM] = 0
    out = np.where(idx >= 0, b[np.maximum(idx, 0)], 0)
    return out, idx >= WARM, b, days

def adx14(h, l, c):
    """ADX(14) and ATR(14), same formula as strategy_library/lib.py indicators()."""
    pc = np.concatenate([[c[0]], c[:-1]]); tr = np.maximum(h - l, np.maximum(abs(h - pc), abs(l - pc)))
    up = h - np.concatenate([[h[0]], h[:-1]]); dn = np.concatenate([[l[0]], l[:-1]]) - l
    pdm = np.where((up > dn) & (up > 0), up, 0.0); mdm = np.where((dn > up) & (dn > 0), dn, 0.0); a = rma(tr, 14)
    pdi = 100 * rma(pdm, 14) / np.maximum(a, 1e-12); mdi = 100 * rma(mdm, 14) / np.maximum(a, 1e-12)
    dx = 100 * abs(pdi - mdi) / np.maximum(pdi + mdi, 1e-12)
    return rma(dx, 14), a

def ffill_conf(piv, vals, n):
    """value of the latest pivot confirmed by each bar (pivot at j is confirmed at j+2)."""
    out = np.full(n, np.nan); j = np.where(piv)[0]; out[j + 2] = vals[j]
    m = ~np.isnan(out); ix = np.where(m, np.arange(n), 0); np.maximum.accumulate(ix, out=ix)
    res = out[ix]; res[: (np.argmax(m) if m.any() else n)] = np.nan
    return res

def features(t, o, h, l, c, v):
    n = len(t); F = {}
    res = np.zeros(n, int); ready = np.ones(n, bool)
    for name, P in TFS:
        b, rdy, _, _ = tf_trend(t, c, P); F["tr_" + name] = b; res += (b == 1); ready &= rdy
    F["res"] = res; F["ready"] = ready
    F["adx"], F["atr"] = adx14(h, l, c)
    ph = np.zeros(n, bool); pl = np.zeros(n, bool)
    if n >= 5:
        ph[2:-2] = (h[2:-2] > h[1:-3]) & (h[2:-2] > h[:-4]) & (h[2:-2] > h[3:-1]) & (h[2:-2] > h[4:])
        pl[2:-2] = (l[2:-2] < l[1:-3]) & (l[2:-2] < l[:-4]) & (l[2:-2] < l[3:-1]) & (l[2:-2] < l[4:])
    F["last_ph"] = ffill_conf(ph, h, n); F["last_pl"] = ffill_conf(pl, l, n)
    prev_c = np.concatenate([[np.nan], c[:-1]])
    F["choch"] = (~np.isnan(F["last_ph"])) & (c > F["last_ph"]) & (prev_c <= F["last_ph"])
    e20 = ema(c, 20); prev_e = np.concatenate([[np.nan], e20[:-1]])
    F["emax"] = (c > e20) & (prev_c <= prev_e)
    lo8 = np.array([l[max(0, i - 7): i + 1].min() for i in range(n)])
    base = np.where(np.isnan(F["last_pl"]), lo8, np.minimum(F["last_pl"], lo8))
    F["stop_base"] = base - BUF * F["atr"]
    return F

def sim_fixed(h, l, c, e, ep, stop, risk, k):
    n = len(c); tp = ep + k * risk
    for j in range(e, min(n, e + HOLD)):
        if l[j] <= stop: return -1.0, j
        if h[j] >= tp: return k, j
    j = min(n - 1, e + HOLD - 1); return (c[j] - ep) / risk, j

def sim_ladder(h, l, c, e, ep, stop, risk):
    """TP1/TP2/TP3 thirds; stop to breakeven from the bar after TP1; stop counts first inside a bar."""
    n = len(c); R = 0.0; rem = 1.0; st = stop; hit = 0
    for j in range(e, min(n, e + HOLD)):
        if l[j] <= st: return R + rem * (st - ep) / risk, j
        while hit < 3 and h[j] >= ep + LADDER[hit][0] * risk:
            R += LADDER[hit][1] * LADDER[hit][0]; rem -= LADDER[hit][1]; hit += 1
        if hit == 3: return R, j
        if hit >= 1: st = max(st, ep)
    j = min(n - 1, e + HOLD - 1); return R + rem * (c[j] - ep) / risk, j

def outcomes(t, h, l, c, e, ep, stop, risk):
    out = []
    for x in EXITS:
        R, j = sim_ladder(h, l, c, e, ep, stop, risk) if x == "L" else sim_fixed(h, l, c, e, ep, stop, risk, 1.5 if x == "S1" else 2.25)
        out.append((R, (j - e + 1) * 0.25, int(t[e]), int(t[j]) + BAR))
    return tuple(out)

def worker(args):
    sym, inv = args
    t, o, h, l, c, v = load(sym, inv); n = len(t)
    if n < 5000: return []
    F = features(t, o, h, l, c, v)
    fo, fh, fl, fc = -o, -l, -h, -c                                    # opposite direction, same bars
    rng = random.Random(zlib.crc32(f"{sym}{inv}".encode()))
    ev = []
    for i in range(1, n - 2):
        if not F["ready"][i]: continue
        kinds = []
        if F["choch"][i]: kinds.append("choch")
        if F["emax"][i]: kinds.append("ema")
        if F["res"][i] > F["res"][i - 1] and F["ready"][i - 1]: kinds.append("resx")
        if rng.random() < RAND_P: kinds.append("rand")
        if not kinds: continue
        e = i + 1; ep = o[e]; stop = F["stop_base"][i]
        if ep - stop < MINSTOP * abs(ep): stop = ep - MINSTOP * abs(ep)
        risk = ep - stop; rp = risk / abs(ep)
        out = outcomes(t, h, l, c, e, ep, stop, risk)
        flip = None
        if "choch" in kinds or "ema" in kinds:
            fep = fo[e]; flip = outcomes(t, fh, fl, fc, e, fep, fep - risk, risk)
        for k in kinds:
            ev.append(dict(kind=k, sym=sym, side="S" if inv else "L", t=int(t[i]), res=int(F["res"][i]), res_prev=int(F["res"][i - 1]),
                           adx=float(F["adx"][i]), rp=rp, out=out, flip=flip if k in ("choch", "ema") else None))
    return ev

# ---------------------------------------------------------------- checks
def check():
    import scen
    ok = True
    # 1) no-repaint: features computed on data cut at bar i must equal the full-run features at bar i
    rnd = random.Random(3); syms = sorted(f.split("_")[0] for f in os.listdir(FOLDER) if f.endswith("_15m.json")); bad = 0; done = 0
    full = {}
    for _ in range(300):
        sym = rnd.choice(syms); inv = rnd.random() < 0.5
        if (sym, inv) not in full: full[(sym, inv)] = (load(sym, inv), None)
        (t, o, h, l, c, v), F = full[(sym, inv)]
        if F is None: F = features(t, o, h, l, c, v); full[(sym, inv)] = ((t, o, h, l, c, v), F)
        i = rnd.randrange(6000, len(t) - 1)
        G = features(t[: i + 1], o[: i + 1], h[: i + 1], l[: i + 1], c[: i + 1], v[: i + 1])
        for k in ("res", "ready", "adx", "atr", "choch", "emax", "stop_base", "last_ph", "last_pl") + tuple("tr_" + nm for nm, _ in TFS):
            a, b = F[k][i], G[k][i]
            if not ((a == b) or (isinstance(a, float) and np.isnan(a) and np.isnan(b))):
                bad += 1; print("REPAINT", sym, inv, i, k, a, b); break
        done += 1
    print(f"no-repaint test: {done} random bars, {bad} mismatches"); ok &= bad == 0
    # 2) parity: 1D trend from resampled 15m == scen.daily_trend's day trend on the same complete days
    for sym in ("BTC", "ETH", "SOL"):
        t, o, h, l, c, v = load(sym, False)
        s = np.argmax(t % DAY == 0); last_day_start = (t[-1] // DAY) * DAY; e_ = np.searchsorted(t, last_day_start)
        t, o, h, l, c = t[s:e_], o[s:e_], h[s:e_], l[s:e_], c[s:e_]
        _, _, b, days = tf_trend(t, c, DAY)
        _, tr = scen.daily_trend(list(t), list(o), list(h), list(l), list(c))     # its per-day dict (keys = UTC day number)
        mism = sum(1 for d, x in zip(days, b) if tr.get(int(d)) != x)
        print(f"1D parity {sym}: {len(days)} days, {mism} differ"); ok &= mism == 0
    # 3) ladder hand-check on synthetic paths (entry 100, risk 1 -> TP1 101, TP2 101.5, TP3 102.25, stop 99)
    T = np.arange(10) * BAR
    def run(hs, ls):
        hh = np.array(hs, float); ll = np.array(ls, float); cc = (hh + ll) / 2
        return sim_ladder(hh, ll, cc, 0, 100.0, 99.0, 1.0)[0]
    a = run([100.5, 101.1, 100.4, 100.2], [99.5, 100.2, 99.9, 99.8])      # TP1, then back to entry -> +1/3
    b = run([100.5, 101.6, 102.3], [99.5, 100.5, 101.0])                  # all three targets -> (1+1.5+2.25)/3
    c_ = run([100.5, 100.8], [99.5, 98.9])                                # straight stop -> -1
    d = run([101.2], [98.9])                                              # stop and TP1 in one bar -> stop first -> -1
    print(f"ladder: TP1+BE {a:+.4f} (want +0.3333) | all TPs {b:+.4f} (want +1.5833) | stop {c_:+.4f} (want -1) | same-bar {d:+.4f} (want -1)")
    ok &= abs(a - 1 / 3) < 1e-9 and abs(b - 4.75 / 3) < 1e-9 and c_ == -1 and d == -1
    print("ALL CHECKS PASSED" if ok else "CHECKS FAILED"); return ok

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "check":
        sys.exit(0 if check() else 1)
    syms = sorted(f.split("_")[0] for f in os.listdir(FOLDER) if f.endswith("_15m.json"))
    with Pool(4) as p: parts = p.map(worker, [(s, inv) for s in syms for inv in (False, True)])
    ev = [e for P_ in parts for e in P_]
    pickle.dump(dict(ev=ev, syms=syms), open(os.environ.get("RES_OUT", "res_ev.pkl"), "wb"))
    import collections
    print("events", len(ev), dict(collections.Counter(e["kind"] for e in ev)))
