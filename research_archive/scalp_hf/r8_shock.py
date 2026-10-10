"""Round 8, M3 (PREREG_round8.md): market-wide contagion shocks. usage: python3 r8_shock.py test | build | select | judge"""
import os, sys, json, pickle, itertools
import numpy as np
import numba as nb
import hf_core as C
import r3_common as R3
import r4
import r6_fast as R6

NS = (6, 8); RESP = ("CONT", "FADE", "LAG"); HS = (15, 60)
OUT = os.path.join(C.DATA, "r8"); HERE = os.path.dirname(os.path.abspath(__file__))
inwin, cl, money = R6.inwin, R6.cl, R6.money
T_A, T_B, T_J1 = R6.T_A, R6.T_B, R6.T_J1


@nb.njit(cache=True)
def ew_sigma_prev(r, span):
    """exponentially weighted std of r using only values BEFORE each bar"""
    n = len(r); out = np.full(n, np.nan); a = 2.0 / (span + 1); v = np.nan; cnt = 0
    for i in range(n):
        if cnt >= span // 4 and v > 0: out[i] = np.sqrt(v)
        if np.isfinite(r[i]):
            v = r[i] * r[i] if np.isnan(v) else v + a * (r[i] * r[i] - v); cnt += 1
    return out


@nb.njit(cache=True)
def find_events(up, dn, valid, N, cool):
    n = len(up); ev = np.zeros(n, np.int64); last = -10 ** 9
    for j in range(n):
        if valid[j] < 6 or j - last < cool: continue
        if up[j] >= N and dn[j] < N: ev[j] = 1; last = j
        elif dn[j] >= N and up[j] < N: ev[j] = -1; last = j
    return ev


def load_group(group):
    names = r4.GROUPS[group]; btc = C.load_coin("BTC")
    coins = [btc if nm == "BTC" else C.load_coin(nm) for nm in names]
    ft_fr = [C.load_funding(nm) for nm in names]
    b5 = []
    for co in coins:
        O, H, L, Cc, *_ = C.aggregate(co.o, co.h, co.l, co.c, co.qv, co.n, co.tb, 5)
        r = np.full(len(Cc), np.nan); r[1:] = np.log(Cc[1:] / Cc[:-1]); r[~np.isfinite(r)] = np.nan
        b5.append((C.atr_wilder(H, L, Cc, 14), Cc, ew_sigma_prev(r, 2016), r))
    return names, coins, ft_fr, b5


def shock_trades(names, coins, ft_fr, b5, tabs):
    n5 = len(b5[0][1])
    Z = np.vstack([b[3] / b[2] for b in b5])
    fin = np.isfinite(Z)
    up = np.where(fin, Z >= 2.5, False).sum(0); dn = np.where(fin, Z <= -2.5, False).sum(0); nv = fin.sum(0)
    summary = {}
    for N in NS:
        ev = find_events(up, dn, nv, N, 12)
        js = np.flatnonzero(ev)
        summary[N] = len(js)
        for resp in RESP:
            for H in HS:
                for cid, (co, (ft, fr), (atr5, Cc5, sg, r)) in enumerate(zip(coins, ft_fr, b5)):
                    sel = []
                    for j in js:
                        z = Z[cid, j]
                        if not np.isfinite(z) or not np.isfinite(atr5[j]): continue
                        d_ = int(ev[j])
                        if resp == "CONT": side = d_
                        elif resp == "FADE": side = -d_
                        else:
                            if abs(z) >= 1.0: continue
                            side = d_
                        sel.append((j, side))
                    if not sel: continue
                    jj = np.array([q[0] for q in sel]); sd = np.array([q[1] for q in sel], np.int64)
                    i1 = (jj + 1) * 5
                    ok = i1 + 1 < co.N; jj, sd, i1 = jj[ok], sd[ok], i1[ok]
                    pe = co.o[i1]; ok = np.isfinite(pe); jj, sd, i1, pe = jj[ok], sd[ok], i1[ok], pe[ok]
                    d = np.maximum(3.0 * atr5[jj], 0.003 * pe); stop = pe - sd * d
                    ent, ext, gr, fu, okk = R3.sim_list(co.o, co.h, co.l, co.c, i1, sd, stop, np.zeros(len(i1)), np.full(len(i1), H, np.int64), co.t0, ft, fr)
                    tabs.add(f"SHOCK|N{N}|{resp}|H{H}", ent[okk], ext[okk], gr[okk] - R3.COST - fu[okk], (d / pe)[okk], np.full(okk.sum(), cid))
    return summary


def build():
    os.makedirs(OUT, exist_ok=True)
    for group in ("design", "unseen", "holdout2"):
        names, coins, ft_fr, b5 = load_group(group)
        tabs = R3.Table(); summ = shock_trades(names, coins, ft_fr, b5, tabs)
        cells = tabs.finish()
        pickle.dump(cells, open(os.path.join(OUT, f"r8_shock_{group}.pkl"), "wb"))
        print(group, "events", summ, flush=True)


def arrs(d):
    return d["ent"], d["ext"], d["net"], d["sf"], d["slot"]


def test():
    """the z of a bar uses only earlier bars (sigma excludes the bar itself); breadth/event detection are causal: truncating the series
    leaves every earlier event unchanged"""
    names, coins, ft_fr, b5 = load_group("design")
    Z = np.vstack([b[3] / b[2] for b in b5]); fin = np.isfinite(Z)
    up = np.where(fin, Z >= 2.5, False).sum(0); dn = np.where(fin, Z <= -2.5, False).sum(0); nv = fin.sum(0)
    full = find_events(up, dn, nv, 6, 12); cut = len(full) * 6 // 10 + 3
    part = find_events(up[:cut], dn[:cut], nv[:cut], 6, 12)
    ok1 = np.array_equal(full[:cut], part)
    r = b5[0][3].copy(); s_full = ew_sigma_prev(r, 2016); s_cut = ew_sigma_prev(r[:cut], 2016)
    ok2 = np.allclose(s_full[:cut], s_cut, equal_nan=True)
    r2 = r.copy(); r2[cut - 1] = r2[cut - 1] * 50 + 1.0                       # a huge bar must not change its own sigma
    ok3 = np.isclose(ew_sigma_prev(r2, 2016)[cut - 1], s_full[cut - 1], equal_nan=True)
    print(f"events causal: {ok1}; sigma causal: {ok2}; sigma excludes own bar: {ok3}; events N=6 in design coins: {int((full != 0).sum())}, N=8: "
          f"{int((find_events(up, dn, nv, 8, 12) != 0).sum())}; z>=2.5 share of coin-bars {np.mean(Z[fin] >= 2.5) * 100:.2f}% (normal: 0.62%)")
    return 0 if (ok1 and ok2 and ok3) else 1


def select():
    d = pickle.load(open(os.path.join(OUT, "r8_shock_design.pkl"), "rb")); frozen = {}
    span = (T_B[1] - T_A[0]) / C.DAY
    print(f"# M3 SHOCK selection (design coins A 2021-01..2024-06, B 2024-07..2025-06)")
    print(f"  {'cell':26s} | {'A n':>5s} {'A R':>7s} {'A t':>6s} | {'B n':>5s} {'B R':>7s} {'B t':>6s} | {'A+B t':>6s} {'gross bps':>9s} {'$10 A+B':>8s} {'DD':>4s}")
    best = {}
    for N, resp, H in itertools.product(NS, RESP, HS):
        c = f"SHOCK|N{N}|{resp}|H{H}"
        if c not in d: print(f"  {c:26s} no trades"); continue
        e, x, r, s, cid = arrs(d[c]); R = r / s
        ma, mb = inwin(e, T_A), inwin(e, T_B); mab = ma | mb
        sa, sb, sab = cl(R[ma], e[ma]), cl(R[mb], e[mb]), cl(R[mab], e[mab])
        eq, tk, dd = money(e[mab], x[mab], r[mab], s[mab], cid[mab])
        print(f"  {c:26s} | {sa['n']:5d} {sa['mean']:+7.3f} {sa['t']:+6.2f} | {sb['n']:5d} {sb['mean']:+7.3f} {sb['t']:+6.2f} | {sab['t']:+6.2f} "
              f"{np.mean(r[mab] + R3.COST) * 1e4:+9.1f} {eq:8.2f} {dd * 100:3.0f}%")
        if sab["n"] >= 150 and sa["mean"] > 0 and sb["mean"] > 0 and (resp not in best or sab["t"] > best[resp][1]): best[resp] = (c, sab["t"])
    for resp in RESP: frozen[resp] = best[resp][0] if resp in best else None
    print("  -> frozen:", frozen)
    json.dump(frozen, open(os.path.join(HERE, "r8_shock_frozen.json"), "w"), indent=1)


def judge():
    fz = json.load(open(os.path.join(HERE, "r8_shock_frozen.json")))
    data = {g: pickle.load(open(os.path.join(OUT, f"r8_shock_{g}.pkl"), "rb")) for g in ("design", "unseen", "holdout2")}
    sets = {"J1 design FINAL": ("design", T_J1), "J2 UNSEEN": ("unseen", (0, 2 ** 62)), "J3 HOLDOUT2": ("holdout2", (0, 2 ** 62))}
    print("# M3 SHOCK judges ($10, 0.5% risk, 8 open)")
    for resp, c in fz.items():
        print(f"\n## {resp}: {c}")
        if c is None: print("  no cell qualified on SELECTION"); continue
        rows = []
        for nm, (g, w) in sets.items():
            if c not in data[g]: rows.append((nm, dict(n=0, mean=np.nan, t=np.nan), 10.0, 0, 0.0, np.nan, np.nan)); continue
            e, x, r, s, cid = arrs(data[g][c]); m = inwin(e, w)
            st = cl(r[m] / s[m], e[m]); eq, tk, dd = money(e[m], x[m], r[m], s[m], cid[m])
            rows.append((nm, st, eq, tk, dd, np.mean(r[m] + R3.COST) * 1e4, np.mean((r[m] - 0.0004) / s[m])))
        j1, j2, j3 = rows
        passed = (j3[1]["n"] >= 150 and j3[1]["mean"] > 0 and j3[1]["t"] >= 2.0 and j1[1]["n"] >= 30 and j1[1]["mean"] > 0 and j2[1]["n"] >= 30
                  and j2[1]["mean"] > 0 and all(q[2] > 10 and q[4] < 0.4 for q in rows) and j3[6] > 0)
        print(f"  PASS: {'YES' if passed else 'no'}")
        for nm, st, eq, tk, dd, gb, r2 in rows:
            print(f"     {nm:16s} trades {st['n']:5d}  gross {gb:+7.1f} bps  net R {st['mean']:+.3f} (t {st['t']:+.2f})  +2bps R {r2:+.3f}  $10 -> {eq:7.2f} ({tk} taken, DD {dd * 100:.0f}%)")


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "test": sys.exit(test())
    {"build": build, "select": select, "judge": judge}[cmd]()
