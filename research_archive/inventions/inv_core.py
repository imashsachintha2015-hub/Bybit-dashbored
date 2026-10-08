"""Invention lab estimators (all causal: the value at bar i uses bars 0..i only). See PREREG_inventions.md.
Prices may be mirrored (negative); log transforms use sign(c) * log|c| so a mirrored series gives an exactly mirrored trend."""
import math
import numpy as np

def slog(c): return np.sign(c) * np.log(np.abs(c))

def ewvar(y, span=100):
    """EWMA of squared 1-bar changes, value at i includes the change into bar i"""
    a = 2 / (span + 1); out = np.empty(len(y)); out[0] = 1e-10; v = None      # same start as tl_core.kalman_trend
    for i in range(1, len(y)):
        r2 = (y[i] - y[i - 1]) ** 2
        v = r2 if v is None else v + a * (r2 - v); out[i] = max(v, 1e-10)
    return out

def llt(c, lam=1e-4, span=100):
    """local linear trend Kalman: returns z (slope / se), level, residual sd units (price vs level)"""
    y = slog(c); n = len(y); R = ewvar(y, span); z = np.full(n, np.nan); lev = np.full(n, np.nan); dev = np.full(n, np.nan)
    x0, x1 = y[0], 0.0; p00, p01, p11 = 1.0, 0.0, 1.0; e2 = None; a = 2 / (span + 1)
    for i in range(1, n):
        r = R[i]
        x0, x1 = x0 + x1, x1; p00, p01, p11 = p00 + 2 * p01 + p11, p01 + p11, p11 + lam * r
        S = p00 + r; k0, k1 = p00 / S, p01 / S; e = y[i] - x0
        x0 += k0 * e; x1 += k1 * e; p00, p01, p11 = p00 - k0 * p00, p01 - k0 * p01, p11 - k1 * p01
        res = y[i] - x0; e2 = res * res if e2 is None else e2 + a * (res * res - e2)
        if i > span: z[i] = x1 / math.sqrt(max(p11, 1e-18)); lev[i] = x0; dev[i] = res / math.sqrt(max(e2, 1e-18))
    return z, lev, dev

def newton(c, lam_a=1e-6, span=100):
    """constant-acceleration Kalman (level, velocity, acceleration); returns velocity z and acceleration z"""
    y = slog(c); n = len(y); R = ewvar(y, span); vz = np.full(n, np.nan); az = np.full(n, np.nan)
    F = np.array([[1.0, 1.0, 0.5], [0.0, 1.0, 1.0], [0.0, 0.0, 1.0]]); x = np.array([y[0], 0.0, 0.0]); P = np.eye(3)
    for i in range(1, n):
        r = R[i]; x = F @ x; P = F @ P @ F.T; P[2, 2] += lam_a * r
        S = P[0, 0] + r; K = P[:, 0] / S; e = y[i] - x[0]; x = x + K * e; P = P - np.outer(K, P[0, :])
        if i > span: vz[i] = x[1] / math.sqrt(max(P[1, 1], 1e-18)); az[i] = x[2] / math.sqrt(max(P[2, 2], 1e-18))
    return vz, az

def imm(c, lams=(3e-5, 3e-4), stay=0.98, span=100):
    """interacting multiple model: two local-linear-trend filters mixed by Bayesian model probabilities; returns mixed slope z"""
    y = slog(c); n = len(y); R = ewvar(y, span); z = np.full(n, np.nan)
    Pi = np.array([[stay, 1 - stay], [1 - stay, stay]]); mu = np.array([0.5, 0.5])
    X = [np.array([y[0], 0.0]), np.array([y[0], 0.0])]; Ps = [np.eye(2), np.eye(2)]
    for i in range(1, n):
        r = R[i]; cbar = Pi.T @ mu; mix = (Pi * mu[:, None]) / np.maximum(cbar[None, :], 1e-300)
        X0 = []; P0 = []
        for j in range(2):
            xj = mix[0, j] * X[0] + mix[1, j] * X[1]
            Pj = sum(mix[m, j] * (Ps[m] + np.outer(X[m] - xj, X[m] - xj)) for m in range(2)); X0.append(xj); P0.append(Pj)
        L = np.zeros(2)
        for j in range(2):
            x = np.array([X0[j][0] + X0[j][1], X0[j][1]]); P = P0[j]
            P = np.array([[P[0, 0] + 2 * P[0, 1] + P[1, 1], P[0, 1] + P[1, 1]], [P[0, 1] + P[1, 1], P[1, 1] + lams[j] * r]])
            S = P[0, 0] + r; K = P[:, 0] / S; e = y[i] - x[0]; X[j] = x + K * e; Ps[j] = P - np.outer(K, P[0, :])
            L[j] = math.exp(-0.5 * e * e / S) / math.sqrt(2 * math.pi * S)
        w = cbar * L; mu = w / w.sum() if w.sum() > 0 else np.array([0.5, 0.5])
        xm = mu[0] * X[0] + mu[1] * X[1]; Pm = sum(mu[j] * (Ps[j] + np.outer(X[j] - xm, X[j] - xm)) for j in range(2))
        if i > span: z[i] = xm[1] / math.sqrt(max(Pm[1, 1], 1e-18))
    return z

def volclock(c, v, lam=1e-4, span=100, win=180):
    """local linear trend where time runs with activity: dt = volume / its previous-180-bar mean (clipped 0.2 .. 5)"""
    y = slog(c); n = len(y); z = np.full(n, np.nan); cv = np.concatenate([[0.0], np.cumsum(v)])
    x0, x1 = y[0], 0.0; p00, p01, p11 = 1.0, 0.0, 1.0; s2 = None; a = 2 / (span + 1)
    for i in range(1, n):
        lo = max(0, i - win); m = (cv[i] - cv[lo]) / max(1, i - lo)
        act = min(5.0, max(0.2, v[i] / m)) if m > 0 else 1.0
        r2 = (y[i] - y[i - 1]) ** 2 / act; s2 = r2 if s2 is None else s2 + a * (r2 - s2); s2 = max(s2, 1e-10)
        x0, x1 = x0 + act * x1, x1
        p00, p01, p11 = p00 + 2 * act * p01 + act * act * p11, p01 + act * p11, p11 + lam * s2 * act
        r = s2 * act; S = p00 + r; k0, k1 = p00 / S, p01 / S; e = y[i] - x0
        x0 += k0 * e; x1 += k1 * e; p00, p01, p11 = p00 - k0 * p00, p01 - k0 * p01, p11 - k1 * p01
        if i > span: z[i] = x1 / math.sqrt(max(p11, 1e-18))
    return z

def variance_ratio(c, q=6, win=120):
    y = slog(c); n = len(y); vr = np.full(n, np.nan)
    r1 = np.diff(y, prepend=y[0]); rq = np.concatenate([np.zeros(q), y[q:] - y[:-q]])
    for i in range(win + q, n):
        a = r1[i - win + 1:i + 1]; b = rq[i - win + 1:i + 1]; v1 = a.var()
        if v1 > 0: vr[i] = b.var() / (q * v1)
    return vr

def perm_entropy(c, m=4, win=120, ref=500, pct=30):
    """normalized permutation entropy of the last `win` ordinal patterns; gate = below the pct-th percentile of its previous `ref` values"""
    n = len(c); pe = np.full(n, np.nan); gate = np.zeros(n, bool)
    pat = np.full(n, -1)
    for i in range(m - 1, n):
        w = c[i - m + 1:i + 1]; order = tuple(np.argsort(w, kind="stable")); pat[i] = hash(order) % 1000003
    from collections import Counter
    cnt = Counter(); norm = math.log(math.factorial(m))
    for i in range(m - 1, n):
        cnt[pat[i]] += 1
        if i - win >= m - 1: cnt[pat[i - win]] -= 1
        if i >= m - 1 + win:
            tot = win; h = -sum((k / tot) * math.log(k / tot) for k in cnt.values() if k > 0); pe[i] = h / norm
            past = pe[max(0, i - ref):i]; past = past[~np.isnan(past)]
            if len(past) >= 100: gate[i] = pe[i] < np.percentile(past, pct)
    return pe, gate

def barrier_lighter(h, l, c, v, atr, i, win=180, k=3.0):
    """at bar i: is the volume traded (last `win` bars, by typical price) in (c, c + k ATR] smaller than in [c - k ATR, c) ?"""
    lo = max(0, i - win + 1); tp = (h[lo:i + 1] + l[lo:i + 1] + c[lo:i + 1]) / 3; vv = v[lo:i + 1]; p = c[i]; a = k * atr[i]
    up = vv[(tp > p) & (tp <= p + a)].sum(); dn = vv[(tp < p) & (tp >= p - a)].sum()
    return up < dn

if __name__ == "__main__":
    # no-repaint check: values at bar i must not change when the series is cut at i
    import os, random, time
    os.environ["TL_WITH_D6"] = "1"
    import tl_core as T
    rnd = random.Random(21); bad = 0; done = 0; t0 = time.time()
    for sym, inv in (("BTC", False), ("ETH", True), ("DOGE", False), ("LINK", True)):
        t, o, h, l, c, v = T.load4h(sym, inv)
        full = dict(llt=llt(c), newton=newton(c), imm=imm(c), vol=volclock(c, v), vr=variance_ratio(c), pe=perm_entropy(c))
        for _ in range(6):
            i = rnd.randrange(1500, len(c) - 1); cc, vv = c[:i + 1], v[:i + 1]
            cut = dict(llt=llt(cc), newton=newton(cc), imm=imm(cc), vol=volclock(cc, vv), vr=variance_ratio(cc), pe=perm_entropy(cc))
            for k_ in full:
                A_ = full[k_] if isinstance(full[k_], tuple) else (full[k_],); B_ = cut[k_] if isinstance(cut[k_], tuple) else (cut[k_],)
                for a_, b_ in zip(A_, B_):
                    x, y_ = a_[i], b_[i]
                    if not (x == y_ or (isinstance(x, float) and math.isnan(x) and math.isnan(y_))): bad += 1; print("REPAINT", sym, inv, i, k_, x, y_)
            done += 1
    print(f"no-repaint test (inventions): {done} cut points x 6 estimators, {bad} mismatches ({time.time()-t0:.0f}s)")
    # mirror check: a mirrored series must give an exactly mirrored trend; parity with the frozen benchmark
    t, o, h, l, c, v = T.load4h("SOL", False); zL = llt(c)[0]; zS = llt(-c)[0]
    print(f"mirror check (Kalman z of -price == -z): max abs difference {np.nanmax(np.abs(zL + zS)):.2e}")
    zT = T.kalman_trend(c)
    print(f"parity with tl_core.kalman_trend (benchmark KALMAN1): max abs z difference {np.nanmax(np.abs(zL - zT)):.2e}, "
          f"same NaN pattern {bool((np.isnan(zL) == np.isnan(zT)).all())}")
