"""Round 6 (PREREG_round6.md): the Kalman trend rule at 15/30/60/120-minute bars with tunable LAM, Z_IN, STOP_ATR.
  python3 r6_fast.py parity | build | select | judge"""
import os, sys, math, json, pickle, itertools
import numpy as np
import numba as nb
import hf_core as C
import hf_ladder as LD
import r4

SPAN, WARMUP, HOLD, FEE, MINSTOP = 100, 201, 120, 0.0014, 0.005
FUND_CAP, FUND_WINDOW = 0.0003, 72 * 3600_000
BARS = (15, 30, 60, 120)
LAMS = (1e-4, 1e-3); ZINS = (1.0, 1.5, 2.0); STOPS = (2.0, 3.0)
REF = (1e-4, 1.0, 3.0)
OUT = os.path.join(C.DATA, "r6"); HERE = os.path.dirname(os.path.abspath(__file__))
T_A = (C.ms(2021), C.ms(2024, 7)); T_B = (C.ms(2024, 7), C.ms(2025, 7)); T_J1 = (C.ms(2025, 7), C.ms(2026, 10))


@nb.njit(cache=True)
def kalman_z_p(c, lam):
    n = len(c); z = np.full(n, np.nan); y = np.log(np.abs(c))
    x0 = y[0]; x1 = 0.0; p00 = 1.0; p01 = 0.0; p11 = 1.0; var = -1.0; a = 2.0 / (SPAN + 1)
    for i in range(1, n):
        r2 = (y[i] - y[i - 1]) ** 2
        var = r2 if var < 0 else var + a * (r2 - var)
        R = max(var, 1e-10)
        x0 = x0 + x1
        p00, p01, p11 = p00 + 2 * p01 + p11, p01 + p11, p11 + lam * R
        S = p00 + R; k0 = p00 / S; k1 = p01 / S; e = y[i] - x0
        x0 += k0 * e; x1 += k1 * e
        p00, p01, p11 = p00 - k0 * p00, p01 - k0 * p01, p11 - k1 * p01
        if i > SPAN: z[i] = x1 / math.sqrt(max(p11, 1e-18))
    return z


@nb.njit(cache=True)
def run_p(t, o, h, l, c, z, a, valid, trend_bar, ft, fr, B, z_in, stop_atr):
    n = len(c); cap = 60000
    ent = np.zeros(cap, np.int64); ext = np.zeros(cap, np.int64); ret = np.zeros(cap); sf = np.zeros(cap); rs = np.zeros(cap, np.int64); sd = np.zeros(cap, np.int64)
    k = 0; busy = -1
    for i in range(max(WARMUP, 1), n - 1):
        if not valid[i] or np.isnan(z[i]) or np.isnan(z[i - 1]): continue
        sgn = 0
        if z[i] > z_in and z[i - 1] <= z_in: sgn = 1
        elif z[i] < -z_in and z[i - 1] >= -z_in: sgn = -1
        if sgn == 0: continue
        te = t[i + 1]
        if te < busy: continue
        tr = trend_bar[i]
        tc = t[i] + B * 60000
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
        ep = o[i + 1]; stop = c[i] - sgn * stop_atr * a[i]
        if sgn * (ep - stop) < MINSTOP * ep: stop = ep - sgn * MINSTOP * ep
        risk = sgn * (ep - stop)
        xi = -1; xt = 0; xp = 0.0; reason = -1
        for j in range(i + 1, min(n, i + 1 + HOLD)):
            if (sgn == 1 and l[j] <= stop) or (sgn == -1 and h[j] >= stop):
                xi, xt, xp, reason = j, t[j] + B * 60000, stop, 1; break
            if (sgn == 1 and z[j] < 0) or (sgn == -1 and z[j] > 0):
                if j + 1 < n: xi, xt, xp, reason = j + 1, t[j + 1] + B * 60000, o[j + 1], 3
                break
        else:
            j = i + HOLD
            if j <= n - 1: xi, xt, xp, reason = j, t[j] + B * 60000, c[j], 0
        if reason < 0: continue
        gross = sgn * (xp / ep - 1.0)
        f = 0.0
        a0 = np.searchsorted(ft, t[i + 1], side="right"); b0 = np.searchsorted(ft, xt, side="right")
        for q in range(a0, b0): f += fr[q]
        f *= sgn
        if k < cap:
            ent[k] = t[i + 1]; ext[k] = xt; ret[k] = gross - FEE - f; sf[k] = risk / ep; rs[k] = reason; sd[k] = sgn; k += 1
        busy = xt
    return ent[:k], ext[:k], ret[:k], sf[:k], rs[:k], sd[:k]


def prep_bars(co, B):
    O, H, L, Cc, *_ = C.aggregate(co.o, co.h, co.l, co.c, co.qv, co.n, co.tb, B)
    valid = ~(np.isnan(O) | np.isnan(H) | np.isnan(L) | np.isnan(Cc)); first = np.argmax(valid)
    O, _ = LD.ffill(O); H, _ = LD.ffill(H); L, _ = LD.ffill(L); Cc, _ = LD.ffill(Cc)
    O[:first] = Cc[:first] = H[:first] = L[:first] = Cc[first]; valid[:first] = False
    t = co.t0 + np.arange(len(O), dtype=np.int64) * B * 60000
    return t, O, H, L, Cc, valid, LD.atr14_rma(H, L, Cc)


def coin_cells(co, ft, fr, trend_days, B, cells):
    t, O, H, L, Cc, valid, a = prep_bars(co, B)
    day = np.clip(((t + B * 60000) // C.DAY) - 1 - co.t0 // C.DAY, 0, len(trend_days) - 1); tb = trend_days[day]
    out = {}
    for lam in sorted(set(c[0] for c in cells)):
        z = kalman_z_p(Cc, lam)
        for c in cells:
            if c[0] != lam: continue
            out[c] = run_p(t, O, H, L, Cc, z, a, valid, tb, ft, fr, B, c[1], c[2])
    return out


def parity():
    btc = C.load_coin("BTC"); td = LD.btc_daily_trend(btc); ft, fr = C.load_funding("BTC")
    ok = True
    for B in (240, 60):
        mine = coin_cells(btc, ft, fr, td, B, [REF])[REF]
        ref = LD.coin_rung(btc, ft, fr, td, B, with_side=True)
        same = all(np.array_equal(np.asarray(x), np.asarray(y)) for x, y in zip(mine, ref))
        print(f"B={B}: trades {len(mine[0])} vs {len(ref[0])}; identical: {same}"); ok &= same
    return ok


def build():
    os.makedirs(OUT, exist_ok=True); btc = C.load_coin("BTC"); td = LD.btc_daily_trend(btc)
    cells = list(itertools.product(LAMS, ZINS, STOPS))
    for group in ("design", "unseen", "holdout2"):
        res = {(B, c): [] for B in BARS for c in cells}
        for cid, nm in enumerate(r4.GROUPS[group]):
            co = btc if nm == "BTC" else C.load_coin(nm); ft, fr = C.load_funding(nm)
            for B in BARS:
                o = coin_cells(co, ft, fr, td, B, cells)
                for c, (e, x, r, s, rs, sd) in o.items(): res[(B, c)].append((e, x, r, s, np.full(len(e), cid)))
            print(group, nm, flush=True)
        agg = {k: tuple(np.concatenate([p[q] for p in v]) for q in range(5)) for k, v in res.items()}
        pickle.dump(agg, open(os.path.join(OUT, f"r6_{group}.pkl"), "wb"))


def inwin(e, w): return (e >= w[0]) & (e < w[1])


def cl(r, e):
    return C.cluster_stats(r, e // C.DAY) if len(r) > 1 else dict(n=len(r), mean=np.nan, t=np.nan)


def money(e, x, r, s, cid, risk=0.005):
    if len(e) < 1: return 10.0, 0, 0.0
    eq, tk, dd, _, _ = C.portfolio(e, x, r, s, cid, 10.0, risk, 8, 3.0, 6.0)
    return eq, tk, dd


def select():
    d = pickle.load(open(os.path.join(OUT, "r6_design.pkl"), "rb")); frozen = {}
    print(f"# SELECTION on design coins: A 2021-01..2024-06, B 2024-07..2025-06 (cell = LAM, Z_IN, STOP_ATR)")
    for B in BARS:
        print(f"\n## bar {B} min   (trades per day = all 10 coins together, over A+B = {(T_B[1] - T_A[0]) / C.DAY:.0f} days)")
        print(f"  {'LAM':>7s} {'Z_IN':>4s} {'STOP':>4s} | {'A n':>6s} {'A R':>7s} {'A t':>6s} | {'B n':>6s} {'B R':>7s} {'B t':>6s} | {'A+B t':>6s} {'/day':>5s} {'$10 A+B':>8s} {'DD':>4s}")
        best = None
        for c in itertools.product(LAMS, ZINS, STOPS):
            e, x, r, s, cid = d[(B, c)]; R = r / s
            ma, mb = inwin(e, T_A), inwin(e, T_B)
            sa, sb, sab = cl(R[ma], e[ma]), cl(R[mb], e[mb]), cl(R[ma | mb], e[ma | mb])
            eq, tk, dd = money(e[ma | mb], x[ma | mb], r[ma | mb], s[ma | mb], cid[ma | mb])
            per_day = (ma | mb).sum() / ((T_B[1] - T_A[0]) / C.DAY)
            tag = "  <- reference" if c == REF else ""
            print(f"  {c[0]:7.0e} {c[1]:4.1f} {c[2]:4.0f} | {sa['n']:6d} {sa['mean']:+7.3f} {sa['t']:+6.2f} | {sb['n']:6d} {sb['mean']:+7.3f} {sb['t']:+6.2f} | {sab['t']:+6.2f} {per_day:5.2f} {eq:8.2f} {dd * 100:3.0f}%{tag}")
            if sab["n"] >= 300 and sa["mean"] > 0 and sb["mean"] > 0 and (best is None or sab["t"] > best[1]): best = (c, sab["t"])
        frozen[str(B)] = list(best[0]) if best else None
        print(f"  -> frozen for {B} min: {frozen[str(B)]}")
    json.dump(frozen, open(os.path.join(HERE, "r6_frozen.json"), "w"), indent=1)


def judge():
    fz = json.load(open(os.path.join(HERE, "r6_frozen.json")))
    sets = {"J1 design FINAL": ("design", T_J1), "J2 UNSEEN": ("unseen", (0, 2 ** 62)), "J3 HOLDOUT2": ("holdout2", (0, 2 ** 62))}
    data = {g: pickle.load(open(os.path.join(OUT, f"r6_{g}.pkl"), "rb")) for g in ("design", "unseen", "holdout2")}
    days = {"J1 design FINAL": (T_J1[1] - T_J1[0]) / C.DAY}
    for g, nm in (("unseen", "J2 UNSEEN"), ("holdout2", "J3 HOLDOUT2")):
        e = np.concatenate([data[g][(240 if False else B, REF)][0] for B in BARS]); days[nm] = (e.max() - e.min()) / C.DAY
    print("# JUDGES: frozen cell and production reference per bar size ($10, 0.5% risk, 8 open)")
    for B in BARS:
        print(f"\n## bar {B} min")
        for lab, cell in (("frozen", fz[str(B)]), ("reference", list(REF))):
            if cell is None: print(f"  {lab}: no setting qualified on SELECTION"); continue
            c = tuple(cell); passed = True; rows = []
            for nm, (g, w) in sets.items():
                e, x, r, s, cid = data[g][(B, c)]; m = inwin(e, w)
                R = r[m] / s[m]; st = cl(R, e[m]); eq, tk, dd = money(e[m], x[m], r[m], s[m], cid[m])
                R2 = (r[m] - 0.0004) / s[m]
                per_day = m.sum() / days[nm]
                rows.append((nm, st, eq, tk, dd, per_day, R2.mean()))
            j1, j2, j3 = rows
            passed = (j3[1]["n"] >= 300 and j3[1]["mean"] > 0 and j3[1]["t"] >= 2.0 and j1[1]["mean"] > 0 and j2[1]["mean"] > 0
                      and all(r_[2] > 10 and r_[4] < 0.4 for r_ in rows) and j3[6] > 0)
            print(f"  {lab} (LAM {c[0]:.0e}, Z_IN {c[1]}, STOP {c[2]:g}):  PASS: {'YES' if passed else 'no'}")
            for nm, st, eq, tk, dd, pd_, r2 in rows:
                print(f"     {nm:16s} trades {st['n']:5d} ({pd_:4.2f}/day)  net R {st['mean']:+.3f} (t {st['t']:+.2f})  +2bps R {r2:+.3f}  $10 -> {eq:7.2f} ({tk} taken, DD {dd * 100:.0f}%)")


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "parity": sys.exit(0 if parity() else 1)
    {"build": build, "select": select, "judge": judge}[cmd]()
