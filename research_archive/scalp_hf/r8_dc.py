"""Round 8, M1 (PREREG_round8.md): a volume clock. Dollar bars + the round-7 estimator panel with the slow Kalman exit.
usage: python3 r8_dc.py test | build | select | judge"""
import os, sys, json, pickle, itertools
import numpy as np
import numba as nb
import hf_core as C
import hf_ladder as LD
import r4
import r6_fast as R6
import r7_sens as R7

DS = (48, 96); KS = (6, 8); THETA = 1.0
OUT = os.path.join(C.DATA, "r8"); HERE = os.path.dirname(os.path.abspath(__file__))
inwin, cl, money = R6.inwin, R6.cl, R6.money
T_A, T_B, T_J1 = R6.T_A, R6.T_B, R6.T_J1
WARM, HOLD, FEE, MINSTOP, STOP_ATR = 260, 120, 0.0014, 0.005, 3.0
FUND_CAP, FUND_WINDOW = 0.0003, 72 * 3600_000


@nb.njit(cache=True)
def day_thresholds(qv, D):
    nd = len(qv) // 1440; dsum = np.zeros(nd); dok = np.zeros(nd)
    for d in range(nd):
        for i in range(d * 1440, (d + 1) * 1440):
            if np.isfinite(qv[i]): dsum[d] += qv[i]; dok[d] += 1
    thr = np.full(nd, np.nan)
    for d in range(20, nd):
        s = 0.0; k = 0
        for q in range(d - 20, d):
            if dok[q] >= 1300: s += dsum[q]; k += 1
        if k >= 15: thr[d] = s / k / D
    return thr


@nb.njit(cache=True)
def dollar_bars(o, h, l, c, qv, tb, thr):
    n = len(c); cap = n // 3 + 10
    S = np.zeros(cap, np.int64); E = np.zeros(cap, np.int64)
    O = np.zeros(cap); H = np.zeros(cap); L = np.zeros(cap); Cc = np.zeros(cap); Q = np.zeros(cap); TB = np.zeros(cap)
    k = 0; cum = 0.0; started = False; s0 = 0; ho = 0.0; hh = 0.0; ll = 0.0; tbs = 0.0
    for i in range(n):
        th = thr[i // 1440] if i // 1440 < len(thr) else np.nan
        if not np.isfinite(th) or not np.isfinite(c[i]) or not np.isfinite(qv[i]): continue
        if not started:
            started = True; s0 = i; ho = o[i]; hh = h[i]; ll = l[i]; cum = 0.0; tbs = 0.0
        if h[i] > hh: hh = h[i]
        if l[i] < ll: ll = l[i]
        cum += qv[i]; tbs += tb[i] if np.isfinite(tb[i]) else 0.0
        if cum >= th:
            if k < cap:
                S[k] = s0; E[k] = i; O[k] = ho; H[k] = hh; L[k] = ll; Cc[k] = c[i]; Q[k] = cum; TB[k] = tbs; k += 1
            started = False
    return S[:k], E[:k], O[:k], H[:k], L[:k], Cc[:k], Q[:k], TB[:k]


@nb.njit(cache=True)
def run_dc(tstart, tend, o, h, l, c, S, a, valid, trend_bar, ft, fr, K, zslow):
    n = len(c); cap = 80000
    ent = np.zeros(cap, np.int64); ext = np.zeros(cap, np.int64); ret = np.zeros(cap); sf = np.zeros(cap)
    k = 0; busy = -1
    for i in range(WARM, n - 1):
        if not valid[i]: continue
        sgn = 0
        if S[i] >= K and S[i - 1] < K: sgn = 1
        elif S[i] <= -K and S[i - 1] > -K: sgn = -1
        if sgn == 0: continue
        if tstart[i + 1] - tend[i] > 600000 or not valid[i + 1]: continue
        te = tstart[i + 1]
        if te < busy: continue
        tr = trend_bar[i]; tc = tend[i]
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
        ep = o[i + 1]; stop = c[i] - sgn * STOP_ATR * a[i]
        if sgn * (ep - stop) < MINSTOP * ep: stop = ep - sgn * MINSTOP * ep
        risk = sgn * (ep - stop)
        xt = -1; xp = 0.0; reason = -1
        for j in range(i + 1, min(n, i + 1 + HOLD)):
            if (sgn == 1 and l[j] <= stop) or (sgn == -1 and h[j] >= stop):
                xt, xp, reason = tend[j], stop, 1; break
            if (sgn == 1 and zslow[j] < 0) or (sgn == -1 and zslow[j] > 0):
                if j + 1 < n: xt, xp, reason = tend[j + 1], o[j + 1], 3
                break
        else:
            j = i + HOLD
            if j <= n - 1: xt, xp, reason = tend[j], c[j], 0
        if reason < 0: continue
        gross = sgn * (xp / ep - 1.0)
        f = 0.0
        a0 = np.searchsorted(ft, tstart[i + 1], side="right"); b0 = np.searchsorted(ft, xt, side="right")
        for q in range(a0, b0): f += fr[q]
        f *= sgn
        if k < cap:
            ent[k] = tstart[i + 1]; ext[k] = xt; ret[k] = gross - FEE - f; sf[k] = risk / ep; k += 1
        busy = xt
    return ent[:k], ext[:k], ret[:k], sf[:k]


def coin_bars(co, D):
    thr = day_thresholds(co.qv, D)
    S, E, O, H, L, Cc, Q, TB = dollar_bars(co.o, co.h, co.l, co.c, co.qv, co.tb, thr)
    tstart = co.t0 + S * 60000; tend = co.t0 + (E + 1) * 60000
    valid = ((E - S + 1) <= 1440) & np.isfinite(O) & np.isfinite(H) & np.isfinite(L) & np.isfinite(Cc)
    return tstart, tend, O, H, L, Cc, Q, TB, valid


def test():
    co = C.load_coin("BTC"); fails = 0
    ts, te, O, H, L, Cc, Q, TB, valid = coin_bars(co, 48)
    n = len(Cc); days = (te[-1] - ts[0]) / C.DAY
    dur = (te - ts) / 60000
    print(f"BTC D=48: {n} bars over {days:.0f} days = {n / days:.1f}/day; duration median {np.median(dur):.0f} min, p5 {np.percentile(dur, 5):.0f}, p95 {np.percentile(dur, 95):.0f}, max {dur.max():.0f}; invalid {np.mean(~valid) * 100:.2f}%")
    cut = C.ms(2024, 3, 1) + 7 * 60000
    c2 = C.load_coin("BTC")
    m = c2.times >= cut
    for arr in (c2.o, c2.h, c2.l, c2.c, c2.qv, c2.n, c2.tb): arr[m] = np.nan
    ts2, te2, O2, H2, L2, Cc2, Q2, TB2, v2 = coin_bars(c2, 48)
    k = np.searchsorted(te, cut, side="right") - 1                            # bars that closed before the cut
    same = np.array_equal(ts[:k], ts2[:k]) and np.array_equal(te[:k], te2[:k]) and np.allclose(Cc[:k], Cc2[:k])
    print(f"bars before a cut are identical on cut data: {same} ({k} bars)"); fails += 0 if same else 1
    z = R7.estimator_z(O, H, L, Cc, Q, TB); z2 = R7.estimator_z(O2, H2, L2, Cc2, Q2, TB2)
    ok = all(np.allclose(z[q, :k], z2[q, :k], equal_nan=True) for q in range(9))
    print(f"estimator scores before the cut identical: {ok}"); fails += 0 if ok else 1
    return fails


def build():
    os.makedirs(OUT, exist_ok=True); btc = C.load_coin("BTC"); td = LD.btc_daily_trend(btc)
    for group in ("design", "unseen", "holdout2"):
        res = {}
        for cid, nm in enumerate(r4.GROUPS[group]):
            co = btc if nm == "BTC" else C.load_coin(nm); ft, fr = C.load_funding(nm)
            for D in DS:
                ts, te, O, H, L, Cc, Q, TB, valid = coin_bars(co, D)
                day = np.clip((te // C.DAY) - 1 - co.t0 // C.DAY, 0, len(td) - 1)
                a = LD.atr14_rma(H, L, Cc); zs = R7.estimator_z(O, H, L, Cc, Q, TB); S = R7.votes(zs, THETA); zslow = R6.kalman_z_p(Cc, 1e-4)
                for K in KS:
                    e, x, r, s = run_dc(ts, te, O, H, L, Cc, S, a, valid, td[day], ft, fr, K, zslow)
                    res.setdefault((D, K), []).append((e, x, r, s, np.full(len(e), cid)))
            print(group, nm, flush=True)
        pickle.dump({k: tuple(np.concatenate([p[q] for p in v]) for q in range(5)) for k, v in res.items()}, open(os.path.join(OUT, f"r8_dc_{group}.pkl"), "wb"))


def select():
    d = pickle.load(open(os.path.join(OUT, "r8_dc_design.pkl"), "rb")); span = (T_B[1] - T_A[0]) / C.DAY
    print("# M1 DOLLAR-CLOCK selection (design coins A 2021-01..2024-06, B 2024-07..2025-06)")
    print(f"  {'D':>3s} {'K':>2s} | {'A n':>6s} {'A R':>7s} {'A t':>6s} | {'B n':>5s} {'B R':>7s} {'B t':>6s} | {'A+B t':>6s} {'/day':>5s} {'gross bps':>9s} {'hold h':>6s} {'$10 A+B':>8s} {'DD':>4s}")
    best = None
    for D, K in itertools.product(DS, KS):
        e, x, r, s, cid = d[(D, K)]; R = r / s
        ma, mb = inwin(e, T_A), inwin(e, T_B); mab = ma | mb
        sa, sb, sab = cl(R[ma], e[ma]), cl(R[mb], e[mb]), cl(R[mab], e[mab])
        eq, tk, dd = money(e[mab], x[mab], r[mab], s[mab], cid[mab])
        print(f"  {D:3d} {K:2d} | {sa['n']:6d} {sa['mean']:+7.3f} {sa['t']:+6.2f} | {sb['n']:5d} {sb['mean']:+7.3f} {sb['t']:+6.2f} | {sab['t']:+6.2f} {mab.sum() / span:5.2f} "
              f"{np.mean(r[mab] + FEE) * 1e4:+9.1f} {np.mean((x[mab] - e[mab]) / 3.6e6):6.1f} {eq:8.2f} {dd * 100:3.0f}%")
        if sab["n"] >= 300 and sa["mean"] > 0 and sb["mean"] > 0 and (best is None or sab["t"] > best[1]): best = ((D, K), sab["t"])
    print("  -> frozen:", best[0] if best else None)
    json.dump(list(best[0]) if best else None, open(os.path.join(HERE, "r8_dc_frozen.json"), "w"))


def judge():
    cell = json.load(open(os.path.join(HERE, "r8_dc_frozen.json")))
    sets = {"J1 design FINAL": ("design", T_J1), "J2 UNSEEN": ("unseen", (0, 2 ** 62)), "J3 HOLDOUT2": ("holdout2", (0, 2 ** 62))}
    data = {g: pickle.load(open(os.path.join(OUT, f"r8_dc_{g}.pkl"), "rb")) for g in ("design", "unseen", "holdout2")}
    print("# M1 DOLLAR-CLOCK judges ($10, 0.5% risk, 8 open); reference = the same panel on fixed-time bars (round 7)")
    if cell is None: print("  no cell qualified on SELECTION"); return
    D, K = cell; days = {}
    for nm, (g, w) in sets.items():
        e = data[g][(D, K)][0]; days[nm] = (T_J1[1] - T_J1[0]) / C.DAY if g == "design" else (e.max() - e.min()) / C.DAY
    rows = []
    for nm, (g, w) in sets.items():
        e, x, r, s, cid = data[g][(D, K)]; m = inwin(e, w)
        st = cl(r[m] / s[m], e[m]); eq, tk, dd = money(e[m], x[m], r[m], s[m], cid[m])
        rows.append((nm, st, eq, tk, dd, m.sum() / days[nm], np.mean(r[m] + FEE) * 1e4, np.mean((r[m] - 0.0004) / s[m]), np.mean((x[m] - e[m]) / 3.6e6)))
    j1, j2, j3 = rows
    passed = (j3[1]["n"] >= 300 and j3[1]["mean"] > 0 and j3[1]["t"] >= 2.0 and j1[1]["mean"] > 0 and j2[1]["mean"] > 0
              and all(q[2] > 10 and q[4] < 0.4 for q in rows) and j3[7] > 0)
    print(f"  frozen D={D} bars/day, K={K}:  PASS: {'YES' if passed else 'no'}")
    for nm, st, eq, tk, dd, pd_, gb, r2, hold in rows:
        print(f"     {nm:16s} trades {st['n']:5d} ({pd_:4.2f}/day, hold {hold:4.1f}h)  gross {gb:+6.1f} bps  net R {st['mean']:+.3f} (t {st['t']:+.2f})  +2bps R {r2:+.3f}  $10 -> {eq:7.2f} ({tk} taken, DD {dd * 100:.0f}%)")
    print("  reference, fixed-time 30m (round 7 frozen): J1 +0.020R $10.01 | J2 +0.039R $10.25 | J3 +0.059R $23.87 (DD 50/41/58%)")
    print("  reference, fixed-time 15m (round 7 frozen): J1 -0.102R $2.79 | J2 -0.073R $4.04 | J3 -0.005R $3.00")


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "test": sys.exit(1 if test() else 0)
    {"build": build, "select": select, "judge": judge}[cmd]()
