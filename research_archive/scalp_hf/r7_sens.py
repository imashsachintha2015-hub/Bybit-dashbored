"""Round 7 (PREREG_round7.md): a panel of nine highly sensitive estimators on 15 / 30 minute bars, voting trend score, production-style trades.
  python3 r7_sens.py test | build | select | judge | fees"""
import os, sys, math, json, pickle, itertools
import numpy as np
import numba as nb
import hf_core as C
import hf_ladder as LD
import r4
import r6_fast as R6

BARS = (15, 30); THETAS = (0.5, 1.0); KS = (4, 6, 8); XS = (0, 2, 99)
NAMES = ["KAL", "SS", "KAMA", "LAG", "HMA", "ZLEMA", "MAMA", "FISH", "FLOW"]
OUT = os.path.join(C.DATA, "r7"); HERE = os.path.dirname(os.path.abspath(__file__))
WARM = 260
SPAN, WARMUP, HOLD, FEE, MINSTOP, STOP_ATR = 100, 201, 120, 0.0014, 0.005, 3.0
FUND_CAP, FUND_WINDOW = 0.0003, 72 * 3600_000


@nb.njit(cache=True)
def rms_norm(x, span):
    n = len(x); out = np.full(n, np.nan); a = 2.0 / (span + 1); v = np.nan
    for i in range(n):
        if not np.isfinite(x[i]): continue
        v = x[i] * x[i] if np.isnan(v) else v + a * (x[i] * x[i] - v)
        if v > 0: out[i] = x[i] / math.sqrt(v)
    return out


@nb.njit(cache=True)
def slope(f):
    n = len(f); s = np.full(n, np.nan)
    for i in range(1, n):
        if np.isfinite(f[i]) and np.isfinite(f[i - 1]): s[i] = f[i] - f[i - 1]
    return s


@nb.njit(cache=True)
def supersmoother(x, period):
    n = len(x); f = x.copy()
    a1 = math.exp(-1.414 * math.pi / period); b1 = 2 * a1 * math.cos(1.414 * math.pi / period); c2 = b1; c3 = -a1 * a1; c1 = 1 - c2 - c3
    for i in range(2, n): f[i] = c1 * (x[i] + x[i - 1]) / 2 + c2 * f[i - 1] + c3 * f[i - 2]
    return f


@nb.njit(cache=True)
def kama(x, er_n, fast, slow):
    n = len(x); f = x.copy(); sf = 2.0 / (fast + 1); ss = 2.0 / (slow + 1)
    for i in range(er_n, n):
        ch = abs(x[i] - x[i - er_n]); vol = 0.0
        for k in range(i - er_n + 1, i + 1): vol += abs(x[k] - x[k - 1])
        er = ch / vol if vol > 0 else 0.0
        sc = (er * (sf - ss) + ss) ** 2
        f[i] = f[i - 1] + sc * (x[i] - f[i - 1])
    return f


@nb.njit(cache=True)
def laguerre(x, g):
    n = len(x); f = x.copy(); l0 = x[0]; l1 = x[0]; l2 = x[0]; l3 = x[0]
    for i in range(n):
        p0, p1, p2, p3 = l0, l1, l2, l3
        l0 = (1 - g) * x[i] + g * p0
        l1 = -g * l0 + p0 + g * p1
        l2 = -g * l1 + p1 + g * p2
        l3 = -g * l2 + p2 + g * p3
        f[i] = (l0 + 2 * l1 + 2 * l2 + l3) / 6.0
    return f


@nb.njit(cache=True)
def wma(x, p):
    n = len(x); f = np.full(n, np.nan); den = p * (p + 1) / 2.0
    for i in range(p - 1, n):
        s = 0.0; ok = True
        for k in range(p):
            v = x[i - k]
            if not np.isfinite(v): ok = False; break
            s += v * (p - k)
        if ok: f[i] = s / den
    return f


def hma(x, n):
    w1 = wma(x, n // 2); w2 = wma(x, n)
    return wma(2 * w1 - w2, int(round(math.sqrt(n))))


@nb.njit(cache=True)
def zlema(x, n):
    m = len(x); lag = (n - 1) // 2; a = 2.0 / (n + 1); f = x.copy()
    for i in range(1, m):
        src = x[i] + (x[i] - x[i - lag]) if i >= lag else x[i]
        f[i] = a * src + (1 - a) * f[i - 1]
    return f


@nb.njit(cache=True)
def mama_fama(p, fast_lim, slow_lim):
    n = len(p); mama = p.copy(); fama = p.copy()
    sm = np.zeros(n); det = np.zeros(n); q1 = np.zeros(n); i1 = np.zeros(n); i2 = np.zeros(n); q2 = np.zeros(n)
    re = np.zeros(n); im = np.zeros(n); per = np.zeros(n); sper = np.zeros(n); ph = np.zeros(n)
    for i in range(6, n):
        sm[i] = (4 * p[i] + 3 * p[i - 1] + 2 * p[i - 2] + p[i - 3]) / 10.0
        c = 0.075 * per[i - 1] + 0.54
        det[i] = (0.0962 * sm[i] + 0.5769 * sm[i - 2] - 0.5769 * sm[i - 4] - 0.0962 * sm[i - 6]) * c
        q1[i] = (0.0962 * det[i] + 0.5769 * det[i - 2] - 0.5769 * det[i - 4] - 0.0962 * det[i - 6]) * c
        i1[i] = det[i - 3]
        ji = (0.0962 * i1[i] + 0.5769 * i1[i - 2] - 0.5769 * i1[i - 4] - 0.0962 * i1[i - 6]) * c
        jq = (0.0962 * q1[i] + 0.5769 * q1[i - 2] - 0.5769 * q1[i - 4] - 0.0962 * q1[i - 6]) * c
        i2[i] = 0.2 * (i1[i] - jq) + 0.8 * i2[i - 1]
        q2[i] = 0.2 * (q1[i] + ji) + 0.8 * q2[i - 1]
        re[i] = 0.2 * (i2[i] * i2[i - 1] + q2[i] * q2[i - 1]) + 0.8 * re[i - 1]
        im[i] = 0.2 * (i2[i] * q2[i - 1] - q2[i] * i2[i - 1]) + 0.8 * im[i - 1]
        pr = per[i - 1]
        if im[i] != 0.0 and re[i] != 0.0: pr = 360.0 / (math.atan(im[i] / re[i]) * 180.0 / math.pi)
        if per[i - 1] > 0:
            if pr > 1.5 * per[i - 1]: pr = 1.5 * per[i - 1]
            if pr < 0.67 * per[i - 1]: pr = 0.67 * per[i - 1]
        if pr < 6: pr = 6.0
        if pr > 50: pr = 50.0
        per[i] = 0.2 * pr + 0.8 * per[i - 1]
        ph[i] = math.atan(q1[i] / i1[i]) * 180.0 / math.pi if i1[i] != 0.0 else ph[i - 1]
        dp = ph[i - 1] - ph[i]
        if dp < 1: dp = 1.0
        al = fast_lim / dp
        if al < slow_lim: al = slow_lim
        if al > fast_lim: al = fast_lim
        mama[i] = al * p[i] + (1 - al) * mama[i - 1]
        fama[i] = 0.5 * al * mama[i] + (1 - 0.5 * al) * fama[i - 1]
    return mama, fama


@nb.njit(cache=True)
def fisher(y, n):
    m = len(y); f = np.zeros(m); v = 0.0
    for i in range(n, m):
        hh = -1e300; ll = 1e300
        for k in range(i - n + 1, i + 1):
            if y[k] > hh: hh = y[k]
            if y[k] < ll: ll = y[k]
        x = 0.66 * ((y[i] - ll) / (hh - ll) - 0.5) if hh > ll else 0.0
        v = x + 0.67 * v
        if v > 0.999: v = 0.999
        if v < -0.999: v = -0.999
        f[i] = 0.5 * math.log((1 + v) / (1 - v)) + 0.5 * f[i - 1]
    return f


@nb.njit(cache=True)
def ema_arr(x, n):
    m = len(x); f = x.copy(); a = 2.0 / (n + 1)
    for i in range(1, m): f[i] = a * x[i] + (1 - a) * f[i - 1]
    return f


def estimator_z(O, H, L, Cc, Q, TB):
    y = np.log(Cc)
    zs = np.full((9, len(y)), np.nan)
    zs[0] = rms_norm(kalman_state(Cc), 200)
    zs[1] = rms_norm(slope(supersmoother(y, 12.0)), 200)
    zs[2] = rms_norm(slope(kama(y, 10, 2, 30)), 200)
    zs[3] = rms_norm(slope(laguerre(y, 0.4)), 200)
    zs[4] = rms_norm(slope(hma(y, 14)), 200)
    zs[5] = rms_norm(slope(zlema(y, 14)), 200)
    ma, fa = mama_fama(y, 0.5, 0.05); zs[6] = rms_norm(ma - fa, 200)
    zs[7] = rms_norm(fisher(y, 10), 200)
    with np.errstate(all="ignore"): imb = np.where(Q > 0, (2 * TB - Q) / Q, 0.0)
    zs[8] = rms_norm(ema_arr(imb, 8), 200)
    zs[:, :WARM] = np.nan
    return zs


@nb.njit(cache=True)
def kalman_state(c):
    """slope state of the Kalman local linear trend (drift variance 1e-3, span 100)"""
    n = len(c); out = np.full(n, np.nan); y = np.log(np.abs(c))
    x0 = y[0]; x1 = 0.0; p00 = 1.0; p01 = 0.0; p11 = 1.0; var = -1.0; a = 2.0 / 101.0; lam = 1e-3
    for i in range(1, n):
        r2 = (y[i] - y[i - 1]) ** 2
        var = r2 if var < 0 else var + a * (r2 - var)
        R = max(var, 1e-10)
        x0 = x0 + x1
        p00, p01, p11 = p00 + 2 * p01 + p11, p01 + p11, p11 + lam * R
        S = p00 + R; k0 = p00 / S; k1 = p01 / S; e = y[i] - x0
        x0 += k0 * e; x1 += k1 * e
        p00, p01, p11 = p00 - k0 * p00, p01 - k0 * p01, p11 - k1 * p01
        out[i] = x1
    return out


def prep(co, B):
    O, H, L, Cc, Q, NN, TB = C.aggregate(co.o, co.h, co.l, co.c, co.qv, co.n, co.tb, B)
    valid = ~(np.isnan(O) | np.isnan(H) | np.isnan(L) | np.isnan(Cc)); first = np.argmax(valid)
    O, _ = LD.ffill(O); H, _ = LD.ffill(H); L, _ = LD.ffill(L); Cc, _ = LD.ffill(Cc)
    O[:first] = Cc[:first] = H[:first] = L[:first] = Cc[first]; valid[:first] = False
    Q = np.where(np.isfinite(Q), Q, 0.0); TB = np.where(np.isfinite(TB), TB, 0.0)
    t = co.t0 + np.arange(len(O), dtype=np.int64) * B * 60000
    return t, O, H, L, Cc, Q, TB, valid, LD.atr14_rma(H, L, Cc)


@nb.njit(cache=True)
def votes(zs, theta):
    m, n = zs.shape; S = np.zeros(n, np.int64)
    for i in range(n):
        s = 0
        for k in range(m):
            v = zs[k, i]
            if np.isfinite(v):
                if v > theta: s += 1
                elif v < -theta: s -= 1
        S[i] = s
    return S


@nb.njit(cache=True)
def run_s(t, o, h, l, c, S, a, valid, trend_bar, ft, fr, B, K, X, zslow):
    n = len(c); cap = 80000
    ent = np.zeros(cap, np.int64); ext = np.zeros(cap, np.int64); ret = np.zeros(cap); sf = np.zeros(cap); rs = np.zeros(cap, np.int64)
    sdv = np.zeros(cap, np.int64); epv = np.zeros(cap); xpv = np.zeros(cap); fv = np.zeros(cap)
    k = 0; busy = -1
    for i in range(WARM, n - 1):
        if not valid[i]: continue
        sgn = 0
        if S[i] >= K and S[i - 1] < K: sgn = 1
        elif S[i] <= -K and S[i - 1] > -K: sgn = -1
        if sgn == 0: continue
        te = t[i + 1]
        if te < busy: continue
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
            if (X < 90 and ((sgn == 1 and S[j] <= X) or (sgn == -1 and S[j] >= -X))) or (X >= 90 and ((sgn == 1 and zslow[j] < 0) or (sgn == -1 and zslow[j] > 0))):
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
            ent[k] = t[i + 1]; ext[k] = xt; ret[k] = gross - FEE - f; sf[k] = risk / ep; rs[k] = reason; sdv[k] = sgn; epv[k] = ep; xpv[k] = xp; fv[k] = f; k += 1
        busy = xt
    return ent[:k], ext[:k], ret[:k], sf[:k], rs[:k], sdv[:k], epv[:k], xpv[:k], fv[:k]


def test():
    co = C.load_coin("BTC"); fails = 0
    for B in BARS:
        t, O, H, L, Cc, Q, TB, valid, a = prep(co, B)
        full = estimator_z(O, H, L, Cc, Q, TB)
        cut = int(len(Cc) * 0.6) + 7
        part = estimator_z(O[:cut], H[:cut], L[:cut], Cc[:cut], Q[:cut], TB[:cut])
        for k, nm in enumerate(NAMES):
            same = np.allclose(full[k, :cut], part[k], equal_nan=True, rtol=1e-9, atol=1e-12)
            share = np.mean(np.abs(full[k, WARM:][np.isfinite(full[k, WARM:])]) > 0.5)
            print(f"B={B} {nm:6s} no-repaint {'OK' if same else 'FAIL'}   share of bars with |z|>0.5: {share:.2f}   std {np.nanstd(full[k, WARM:]):.2f}")
            fails += 0 if same else 1
        S = votes(full, 0.5)
        print(f"B={B} score distribution (theta 0.5): " + " ".join(f"{v:+d}:{np.mean(S[WARM:] == v) * 100:.0f}%" for v in range(-9, 10, 3)))
    print("FAILS", fails); return fails


def build():
    os.makedirs(OUT, exist_ok=True); btc = C.load_coin("BTC"); td = LD.btc_daily_trend(btc)
    cells = list(itertools.product(THETAS, KS, XS))
    for group in ("design", "unseen", "holdout2"):
        res = {}
        for cid, nm in enumerate(r4.GROUPS[group]):
            co = btc if nm == "BTC" else C.load_coin(nm); ft, fr = C.load_funding(nm)
            for B in BARS:
                t, O, H, L, Cc, Q, TB, valid, a = prep(co, B)
                day = np.clip(((t + B * 60000) // C.DAY) - 1 - co.t0 // C.DAY, 0, len(td) - 1); tb = td[day]
                zs = estimator_z(O, H, L, Cc, Q, TB); zslow = R6.kalman_z_p(Cc, 1e-4)
                for th in THETAS:
                    S = votes(zs, th)
                    for (_, K, X) in [c for c in cells if c[0] == th]:
                        e, x, r, s, rs = run_s(t, O, H, L, Cc, S, a, valid, tb, ft, fr, B, K, X, zslow)[:5]
                        res.setdefault((B, th, K, X), []).append((e, x, r, s, np.full(len(e), cid)))
                # single-estimator reference
                for k, nm_ in enumerate(NAMES):
                    S1 = votes(zs[k:k + 1], 0.5)
                    e, x, r, s, rs = run_s(t, O, H, L, Cc, S1, a, valid, tb, ft, fr, B, 1, 0, zslow)[:5]
                    res.setdefault((B, "single", nm_), []).append((e, x, r, s, np.full(len(e), cid)))
            print(group, nm, flush=True)
        agg = {k: tuple(np.concatenate([p[q] for p in v]) for q in range(5)) for k, v in res.items()}
        pickle.dump(agg, open(os.path.join(OUT, f"r7_{group}.pkl"), "wb"))


inwin, cl, money = R6.inwin, R6.cl, R6.money
T_A, T_B, T_J1 = R6.T_A, R6.T_B, R6.T_J1


def gross_bps(r, s, e, x):
    return (r + FEE) * 1e4


def select():
    d = pickle.load(open(os.path.join(OUT, "r7_design.pkl"), "rb")); frozen = {}
    span = (T_B[1] - T_A[0]) / C.DAY
    print("# Single estimators alone (vote >= 1 at |z| > 0.5, exit when the vote ends), design coins A+B 2021-01..2025-06 (descriptive)")
    print(f"  {'bar':>3s} {'est':6s} {'n':>6s} {'/day':>5s} {'gross bps':>9s} {'gross R':>8s} {'net R':>7s} {'t':>6s}")
    for B in BARS:
        for nm in NAMES:
            e, x, r, s, cid = d[(B, "single", nm)]; m = inwin(e, (T_A[0], T_B[1])); st = cl(r[m] / s[m], e[m])
            print(f"  {B:3d} {nm:6s} {m.sum():6d} {m.sum() / span:5.2f} {np.mean(r[m] + FEE) * 1e4:+9.1f} {np.mean((r[m] + FEE) / s[m]):+8.3f} {st['mean']:+7.3f} {st['t']:+6.2f}")
    print("\n# Panel cells (THETA, K of 9 votes, exit when score <= X)")
    for B in BARS:
        print(f"\n## bar {B} min   (trades per day = 10 coins together)")
        print(f"  {'TH':>3s} {'K':>2s} {'X':>2s} | {'A n':>6s} {'A R':>7s} {'A t':>6s} | {'B n':>5s} {'B R':>7s} {'B t':>6s} | {'A+B t':>6s} {'/day':>5s} {'gross bps':>9s} {'hold h':>6s} {'$10 A+B':>8s} {'DD':>4s}")
        best = None
        for th, K, X in itertools.product(THETAS, KS, XS):
            e, x, r, s, cid = d[(B, th, K, X)]; R = r / s
            ma, mb = inwin(e, T_A), inwin(e, T_B); mab = ma | mb
            sa, sb, sab = cl(R[ma], e[ma]), cl(R[mb], e[mb]), cl(R[mab], e[mab])
            eq, tk, dd = money(e[mab], x[mab], r[mab], s[mab], cid[mab])
            print(f"  {th:3.1f} {K:2d} {X:2d} | {sa['n']:6d} {sa['mean']:+7.3f} {sa['t']:+6.2f} | {sb['n']:5d} {sb['mean']:+7.3f} {sb['t']:+6.2f} | {sab['t']:+6.2f} {mab.sum() / span:5.2f} "
                  f"{np.mean(r[mab] + FEE) * 1e4:+9.1f} {np.mean((x[mab] - e[mab]) / 3.6e6):6.1f} {eq:8.2f} {dd * 100:3.0f}%")
            if sab["n"] >= 300 and sa["mean"] > 0 and sb["mean"] > 0 and (best is None or sab["t"] > best[1]): best = ((th, K, X), sab["t"])
        frozen[str(B)] = list(best[0]) if best else None
        print(f"  -> frozen for {B} min: {frozen[str(B)]}")
    json.dump(frozen, open(os.path.join(HERE, "r7_frozen.json"), "w"), indent=1)


def judge():
    fz = json.load(open(os.path.join(HERE, "r7_frozen.json")))
    sets = {"J1 design FINAL": ("design", T_J1), "J2 UNSEEN": ("unseen", (0, 2 ** 62)), "J3 HOLDOUT2": ("holdout2", (0, 2 ** 62))}
    data = {g: pickle.load(open(os.path.join(OUT, f"r7_{g}.pkl"), "rb")) for g in ("design", "unseen", "holdout2")}
    days = {"J1 design FINAL": (T_J1[1] - T_J1[0]) / C.DAY}
    for g, nm in (("unseen", "J2 UNSEEN"), ("holdout2", "J3 HOLDOUT2")):
        e = data[g][(15, 0.5, 4, 0)][0]; days[nm] = (e.max() - e.min()) / C.DAY
    print("# JUDGES ($10, 0.5% risk, 8 open). Gross bps = per trade before the 14 bps round trip.")
    for B in BARS:
        print(f"\n## bar {B} min")
        cell = fz[str(B)]
        if cell is None: print("  no setting qualified on SELECTION"); continue
        key = (B, cell[0], cell[1], cell[2]); rows = []
        for nm, (g, w) in sets.items():
            e, x, r, s, cid = data[g][key]; m = inwin(e, w)
            st = cl(r[m] / s[m], e[m]); eq, tk, dd = money(e[m], x[m], r[m], s[m], cid[m])
            rows.append((nm, st, eq, tk, dd, m.sum() / days[nm], np.mean((r[m] - 0.0004) / s[m]), np.mean(r[m] + FEE) * 1e4, np.mean((x[m] - e[m]) / 3.6e6)))
        j1, j2, j3 = rows
        passed = (j3[1]["n"] >= 300 and j3[1]["mean"] > 0 and j3[1]["t"] >= 2.0 and j1[1]["mean"] > 0 and j2[1]["mean"] > 0
                  and all(r_[2] > 10 and r_[4] < 0.4 for r_ in rows) and j3[6] > 0)
        print(f"  frozen THETA {cell[0]}, K {cell[1]}, X {cell[2]}:  PASS: {'YES' if passed else 'no'}")
        for nm, st, eq, tk, dd, pd_, r2, gb, hold in rows:
            print(f"     {nm:16s} trades {st['n']:5d} ({pd_:4.2f}/day, hold {hold:4.1f}h)  gross {gb:+6.1f} bps  net R {st['mean']:+.3f} (t {st['t']:+.2f})  +2bps R {r2:+.3f}  $10 -> {eq:7.2f} ({tk} taken, DD {dd * 100:.0f}%)")
        if True:
            print("     same trades at a lower round-trip cost (optimistic): " + "  ".join(
                f"{rt}bps: " + "/".join(f"${money(data[g][key][0][inwin(data[g][key][0], w)], data[g][key][1][inwin(data[g][key][0], w)], data[g][key][2][inwin(data[g][key][0], w)] + (14 - rt) / 1e4, data[g][key][3][inwin(data[g][key][0], w)], data[g][key][4][inwin(data[g][key][0], w)])[0]:.2f}" for g, w in sets.values())
                for rt in (10, 6, 3)))


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "test": sys.exit(1 if test() else 0)
    {"build": build, "select": select, "judge": judge}[cmd]()
