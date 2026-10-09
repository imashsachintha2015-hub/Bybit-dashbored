"""Causal trend-line engine for the trend-line / reversal study. 4H bars built from the joined OKX 1H series (d5 + d4, 2021-06 .. 2026-09).
Noise filter: a swing exists only after price has reversed k x ATR(14) from it; the bar where that happens is its confirmation bar.
Lines (long orientation; shorts = mirrored prices):
  SUP: ascending support through the last two confirmed swing lows (higher low); RES: descending resistance through the last two swing
       highs (lower high). A line exists from the bar after its second pivot is confirmed until a close beyond it by DELTA x ATR.
       A later swing within TOUCH x ATR of the line is a touch; a higher low above SUP (lower high below RES) redraws a steeper line
       and the old slope is kept (acceleration). Test = a bar reaching the line's zone without closing beyond it.
  OPT (optimized, no-violation): over bars t-N .. t-1, the line through the most extreme point (vs an OLS fit of closes) whose slope is
       the least-squares slope clipped so that no bar in the window crosses it. Evaluated at bar t.
  KAL: local linear trend Kalman filter on log close; z = slope / its standard error (noise-free trend direction and strength)."""
import os, json, math
import numpy as np
HR = 3600000; H4 = 4 * HR; DAY = 86400000
DELTA, TOUCH, ZONE = 0.25, 0.35, 0.25

WITH_D6 = os.environ.get("TL_WITH_D6") == "1"     # prepend d6 (2019-12 .. 2021-05, used only by the 2020-21 pre-registered test)

def series_1h(sym):
    a = json.load(open(f"d5/{sym}_1H.json")) if os.path.exists(f"d5/{sym}_1H.json") else []
    if WITH_D6 and os.path.exists(f"d6/{sym}_1H.json"):
        z = json.load(open(f"d6/{sym}_1H.json")); first = a[0][0] if a else float("inf")
        a = [r for r in z if r[0] < first] + a
    b = json.load(open(f"d4/{sym}_1H.json")) if os.path.exists(f"d4/{sym}_1H.json") else []
    last = a[-1][0] if a else -1
    return a + [r for r in b if r[0] > last]

def load4h(sym, inv=False):
    rows = series_1h(sym)
    if len(rows) < 400: return None
    d = np.array(rows, dtype=float); t = d[:, 0].astype(np.int64); bid = t // H4
    ub, st, cnt = np.unique(bid, return_index=True, return_counts=True); keep = cnt == 4; ub, st = ub[keep], st[keep]
    o = d[st, 1].copy(); h = np.array([d[s:s + 4, 2].max() for s in st]); l = np.array([d[s:s + 4, 3].min() for s in st])
    c = d[st + 3, 4].copy(); v = np.array([d[s:s + 4, 5].sum() for s in st])
    if inv: o, h, l, c = -o, -l, -h, -c
    return ub * H4, o, h, l, c, v

def rma(x, n):
    o = np.empty_like(x); o[0] = x[0]
    for i in range(1, len(x)): o[i] = o[i - 1] + (x[i] - o[i - 1]) / n
    return o

def ema(x, n):
    a = 2 / (n + 1); o = np.empty_like(x); o[0] = x[0]
    for i in range(1, len(x)): o[i] = o[i - 1] + a * (x[i] - o[i - 1])
    return o

def atr14(h, l, c):
    pc = np.concatenate([[c[0]], c[:-1]]); tr = np.maximum(h - l, np.maximum(abs(h - pc), abs(l - pc))); return rma(tr, 14)

def zigzag(h, l, atr, k):
    """causal swings: list of (kind 'H'/'L', pivot index, price, confirmation index)"""
    n = len(h); sw = []; d = 0; hi, hi_i, lo, lo_i = h[0], 0, l[0], 0
    for i in range(1, n):
        a = k * atr[i]
        if d == 0:
            if h[i] > hi: hi, hi_i = h[i], i
            if l[i] < lo: lo, lo_i = l[i], i
            if hi_i < i and hi - l[i] >= a: sw.append(("H", hi_i, hi, i)); d = -1; lo, lo_i = l[i], i
            elif lo_i < i and h[i] - lo >= a: sw.append(("L", lo_i, lo, i)); d = 1; hi, hi_i = h[i], i
        elif d == 1:
            if h[i] > hi: hi, hi_i = h[i], i
            elif hi - l[i] >= a: sw.append(("H", hi_i, hi, i)); d = -1; lo, lo_i = l[i], i
        else:
            if l[i] < lo: lo, lo_i = l[i], i
            elif h[i] - lo >= a: sw.append(("L", lo_i, lo, i)); d = 1; hi, hi_i = h[i], i
    return sw

class Line:
    __slots__ = ("i1", "p1", "i2", "p2", "s", "touches", "tests", "born", "prev_s", "in_test", "atr0")
    def __init__(self, i1, p1, i2, p2, born, atr0, prev_s=None):
        self.i1, self.p1, self.i2, self.p2 = i1, p1, i2, p2; self.s = (p2 - p1) / (i2 - i1); self.touches = 2; self.tests = 0
        self.born = born; self.prev_s = prev_s; self.in_test = False; self.atr0 = atr0
    def at(self, i): return self.p2 + self.s * (i - self.i2)

def pivot_lines(o, h, l, c, atr, k):
    """per-bar arrays for the SUP and RES lines and their events (all known at the close of each bar)"""
    n = len(c); sw = zigzag(h, l, atr, k); by_conf = {}
    for s in sw: by_conf.setdefault(s[3], []).append(s)
    nan = np.full(n, np.nan)
    F = {f"{p}_{x}": nan.copy() for p in ("sup", "res") for x in ("y", "raw", "touches", "tests", "slope", "age", "accel", "span")}
    for p in ("sup", "res"):
        F[f"{p}_break"] = np.zeros(n, bool); F[f"{p}_test"] = np.zeros(n, bool)
    sup = res = None; lastL = lastH = None
    for i in range(n):
        a = atr[i]
        for p, ln in (("sup", sup), ("res", res)):
            if ln is None or ln.born >= i: continue
            y = ln.at(i); sg = 1 if p == "sup" else -1
            F[f"{p}_y"][i] = y; F[f"{p}_raw"][i] = ln.s; F[f"{p}_touches"][i] = ln.touches; F[f"{p}_tests"][i] = ln.tests
            F[f"{p}_slope"][i] = sg * ln.s / ln.atr0; F[f"{p}_age"][i] = i - ln.born; F[f"{p}_span"][i] = ln.i2 - ln.i1
            F[f"{p}_accel"][i] = (ln.s / ln.prev_s) if ln.prev_s not in (None, 0) else np.nan
            if sg * (c[i] - y) < -DELTA * a:                       # close beyond the line: broken
                F[f"{p}_break"][i] = True
                if p == "sup": sup = None
                else: res = None
                continue
            near = (l[i] <= y + ZONE * a) if p == "sup" else (h[i] >= y - ZONE * a)
            if near and not ln.in_test:
                F[f"{p}_test"][i] = True; ln.in_test = True
            elif ln.in_test and sg * (c[i] - y) > 1.0 * a:         # moved 1 ATR away: the test episode is over
                ln.in_test = False; ln.tests += 1
        for kind, idx, price, conf in by_conf.get(i, []):           # swings confirmed at this bar -> lines usable from i + 1
            if kind == "L":
                if sup is not None and sup.born < i:
                    y = sup.at(idx)
                    if abs(price - y) <= TOUCH * atr[idx]: sup.touches += 1
                    elif price > y: sup = Line(sup.i2, sup.p2, idx, price, i, atr[i], prev_s=sup.s)
                    else: sup = None
                if sup is None and lastL is not None and price > lastL[1]: sup = Line(lastL[0], lastL[1], idx, price, i, atr[i])
                lastL = (idx, price)
            else:
                if res is not None and res.born < i:
                    y = res.at(idx)
                    if abs(price - y) <= TOUCH * atr[idx]: res.touches += 1
                    elif price < y: res = Line(res.i2, res.p2, idx, price, i, atr[i], prev_s=res.s)
                    else: res = None
                if res is None and lastH is not None and price < lastH[1]: res = Line(lastH[0], lastH[1], idx, price, i, atr[i])
                lastH = (idx, price)
    return F, sw

def opt_lines(h, l, c, atr, N):
    """optimized no-violation support / resistance lines over bars t-N .. t-1, evaluated at t, plus touch counts"""
    n = len(c); sup = np.full(n, np.nan); res = np.full(n, np.nan); ts = np.zeros(n); tr = np.zeros(n)
    x = np.arange(N, dtype=float); xm = x.mean(); sxx = ((x - xm) ** 2).sum()
    for t in range(N, n):
        cc = c[t - N:t]; b = ((x - xm) * (cc - cc.mean())).sum() / sxx; fit = cc.mean() + b * (x - xm)
        for side in (0, 1):
            y = l[t - N:t] if side == 0 else h[t - N:t]
            p = int(np.argmin(y - fit)) if side == 0 else int(np.argmax(y - fit))
            dx = x - x[p]; dy = y - y[p]; m = dx != 0
            s_ls = (dx[m] * dy[m]).sum() / (dx[m] ** 2).sum()
            left = dx < 0; right = dx > 0; r = dy / np.where(m, dx, 1)
            if side == 0: lo_ = r[left].max() if left.any() else -np.inf; hi_ = r[right].min() if right.any() else np.inf
            else: lo_ = r[right].max() if right.any() else -np.inf; hi_ = r[left].min() if left.any() else np.inf
            s = min(max(s_ls, lo_), hi_) if lo_ <= hi_ else lo_
            line = y[p] + s * dx; val = y[p] + s * (N - x[p])
            touch = (np.abs(y - line) <= TOUCH * atr[t - 1]).sum()
            if side == 0: sup[t] = val; ts[t] = touch
            else: res[t] = val; tr[t] = touch
    return sup, res, ts, tr

def kalman_trend(c, lam=1e-4, span=100):
    """local linear trend on log price; observation noise = EWMA variance of 1-bar log returns; slope noise = lam x that"""
    y = np.log(np.abs(c)) * np.sign(c); n = len(y); z = np.full(n, np.nan)
    x = np.array([y[0], 0.0]); P = np.eye(2) * 1.0; var = None; a = 2 / (span + 1)
    for i in range(1, n):
        r2 = (y[i] - y[i - 1]) ** 2; var = r2 if var is None else var + a * (r2 - var); R = max(var, 1e-10)
        x = np.array([x[0] + x[1], x[1]]); P = np.array([[P[0, 0] + 2 * P[0, 1] + P[1, 1], P[0, 1] + P[1, 1]], [P[0, 1] + P[1, 1], P[1, 1] + lam * R]])
        S = P[0, 0] + R; K = np.array([P[0, 0] / S, P[1, 0] / S]); e = y[i] - x[0]
        x = x + K * e; P = P - np.outer(K, [P[0, 0], P[0, 1]])
        if i > span: z[i] = x[1] / math.sqrt(max(P[1, 1], 1e-18))
    return z

def daily_trend(t, c):
    """+1 / -1 / 0 trend of the last COMPLETE UTC day at each 4H close (close vs EMA50 and EMA20 vs EMA50 on daily closes)"""
    day = t // DAY; ud, st, cnt = np.unique(day, return_index=True, return_counts=True); keep = cnt == 6; ud, st = ud[keep], st[keep]
    C = c[st + 5]; e20, e50 = ema(C, 20), ema(C, 50)
    tr = np.where((C > e50) & (e20 > e50), 1, np.where((C < e50) & (e20 < e50), -1, 0)); tr[:50] = 0
    idx = np.searchsorted((ud + 1) * DAY, t + H4, side="right") - 1
    return np.where(idx >= 0, tr[np.maximum(idx, 0)], 0)

def features(t, o, h, l, c, v, k=3.0):
    atr = atr14(h, l, c); F, sw = pivot_lines(o, h, l, c, atr, k)
    F["atr"] = atr; F["tr1d"] = daily_trend(t, c); F["kal"] = kalman_trend(c)
    cv = np.concatenate([[0.0], np.cumsum(v)]); n = len(c)
    F["relvol"] = np.array([v[i] / max((cv[i] - cv[max(0, i - 30)]) / max(1, min(30, i)), 1e-12) if i > 0 else 1.0 for i in range(n)])
    for N in (48, 96):
        F[f"opt{N}_sup"], F[f"opt{N}_res"], F[f"opt{N}_tsup"], F[f"opt{N}_tres"] = opt_lines(h, l, c, atr, N)
    return F, sw

if __name__ == "__main__":
    import random, time
    rnd = random.Random(11); bad = 0; done = 0; t0 = time.time()
    keys = None
    for sym, inv in (("BTC", False), ("ETH", True), ("SOL", False), ("FIL", True)):
        t, o, h, l, c, v = load4h(sym, inv); F, _ = features(t, o, h, l, c, v); keys = [k_ for k_ in F if not k_.startswith("opt96")]
        for _ in range(12):
            i = rnd.randrange(800, len(t) - 1)
            G, _ = features(t[:i + 1], o[:i + 1], h[:i + 1], l[:i + 1], c[:i + 1], v[:i + 1])
            for k_ in keys:
                a_, b_ = F[k_][i], G[k_][i]
                if not (a_ == b_ or (isinstance(a_, float) and math.isnan(a_) and math.isnan(b_))):
                    bad += 1; print("REPAINT", sym, inv, i, k_, a_, b_)
            done += 1
    print(f"no-repaint test: {done} truncation points x {len(keys)} features, {bad} mismatches ({time.time()-t0:.0f}s)")
