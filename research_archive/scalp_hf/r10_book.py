"""Round 10 (PREREG_round10.md): order-book imbalance from Binance bookDepth.
usage: python3 r10_book.py test | build | part1 | select | judge"""
import os, sys, json, glob, pickle
import numpy as np
import pandas as pd
import hf_core as C

BD = os.path.join(C.DATA, "bd"); OUT = os.path.join(C.DATA, "r10"); HERE = os.path.dirname(os.path.abspath(__file__))
os.makedirs(OUT, exist_ok=True)
DESIGN, UNSEEN = C.DESIGN, C.UNSEEN
FRESH = "AAVE BCH CRV ETC FIL HBAR ICP RUNE TRX UNI".split()
BIN = 300_000
LV = {0.2: (5, 6), 1.0: (4, 7), 5.0: (0, 11)}              # indices of the bid / ask notional columns for each level
KS, HS, LS = (1.5, 2.5), (30, 120), (0.2, 1.0, 5.0)
T_A = (C.ms(2023), C.ms(2024, 7)); T_B = (C.ms(2024, 7), C.ms(2025, 7)); T_J1 = (C.ms(2025, 7), C.ms(2026, 10)); T_ALL = (C.ms(2023), C.ms(2026, 10))
STOP_ATR, MINSTOP, WIN = 3.0, 0.005, 2016                  # 7 days of 5-minute bins


def inwin(e, w): return (e >= w[0]) & (e < w[1])


def cl(r, e): return C.cluster_stats(r, e // C.DAY) if len(r) > 1 else dict(n=len(r), mean=np.nan, se=np.nan, t=np.nan)


def load_bd(name):
    """5-minute grid (bin start ms) of bid/ask notional at the 12 levels; NaN where missing"""
    fs = sorted(glob.glob(os.path.join(BD, name, "*.npz")))
    if not fs: return None
    ts, ns = [], []
    for f in fs:
        d = np.load(f); ts.append(d["t"]); ns.append(d["notional"])
    t = np.concatenate(ts).astype(np.int64) * (1000 if np.concatenate(ts)[0] < 10 ** 11 else 1); N = np.concatenate(ns)
    return t, N


def features(name):
    """per coin: signal-bar index on the 1m grid, IMB_L, Z_L (L=0.2,1,5) and THIN, for every 5-minute bin. A bin starting at T is known at T+5min
    (signal at the close of the 1m bar T+4, entry at the open of T+5)."""
    r = load_bd(name)
    if r is None: return None
    t, N = r
    b = (t - C.GRID_T0) // BIN
    g = pd.RangeIndex(b.min(), b.max() + 1)
    out = {"bin": np.asarray(g, dtype=np.int64)}
    cols = {}
    for L, (kb, ka) in LV.items():
        bid = pd.Series(N[:, kb], index=b).groupby(level=0).last().reindex(g); ask = pd.Series(N[:, ka], index=b).groupby(level=0).last().reindex(g)
        imb = (bid - ask) / (bid + ask)
        sd = imb.rolling(WIN, min_periods=1000).std().shift(1)                  # standard deviation of the bins before this one
        out[f"imb{L}"] = imb.values; out[f"z{L}"] = (imb / sd).values
        if L == 1.0:
            dep = bid + ask; out["thin"] = (dep / dep.rolling(WIN, min_periods=1000).mean().shift(1)).values
    out["sig_i"] = out["bin"] * 5 + 4                   # 1m index (grid starts at GRID_T0) of the last minute of the bin
    return out


def atr30(co):
    O, H, L, Cc, Q, NN, TB = C.aggregate(co.o, co.h, co.l, co.c, co.qv, co.n, co.tb, 30)
    return C.to_1m(C.atr_wilder(H, L, Cc, 14), 30, co.N)


def cell_events(co, f, L, K, H, direction, ft, fr, atr, slip=0.0):
    """trades of one cell on one coin: entry ms, exit ms, net return, stop fraction"""
    z = f[f"z{L}"]; ok = np.isfinite(z) & (np.abs(z) >= K)
    idx = f["sig_i"][ok]; sgn = np.sign(z[ok]).astype(np.int64)
    good = (idx >= 0) & (idx + 1 < co.N)
    idx, sgn = idx[good], sgn[good]
    side = sgn if direction == "WITH" else -sgn
    keep = C.cooldown(idx, H); idx, side = idx[keep], side[keep]
    if len(idx) == 0: return np.zeros(0, np.int64), np.zeros(0, np.int64), np.zeros(0), np.zeros(0)
    ent, ext, ret, sf, rs = C.simulate_events(co.o, co.h, co.l, co.c, atr, idx, side, STOP_ATR, 0.0, H, False, 0, 0.0, MINSTOP, co.t0, ft, fr, slip)
    m = ent > 0
    return ent[m], ext[m], ret[m], sf[m]


def money(e, x, r, s, cid, risk=0.005):
    if len(e) < 1: return 10.0, 0, 0.0
    eq, tk, dd, _, _ = C.portfolio(e, x, r, s, cid, 10.0, risk, 8, 3.0, 6.0)
    return eq, tk, dd


# ------------------------------------------------------------------ build: features for every coin
def build():
    for nm in DESIGN + UNSEEN + FRESH:
        p = os.path.join(OUT, f"f_{nm}.pkl")
        if os.path.exists(p): continue
        f = features(nm)
        if f is None: print(nm, "no book data"); continue
        pickle.dump(f, open(p, "wb")); ok = np.isfinite(f["z1.0"])
        print(nm, "bins", len(f["bin"]), "with z", int(ok.sum()), flush=True)


# ------------------------------------------------------------------ test: causality, alignment
def test():
    f = features("BTC"); co = C.load_coin("BTC")
    # (1) known-at: the signal bar's close time is the bin start + 5 minutes
    i = f["sig_i"][1000]; assert co.t0 + (i + 1) * C.MIN == C.GRID_T0 + (f["bin"][1000] + 1) * BIN, "alignment"
    # (2) no look-ahead: cutting the book data after bin k leaves every value up to k unchanged
    r = load_bd("BTC"); t, N = r; cut = t[len(t) // 2]
    orig = {k: v.copy() for k, v in f.items()}
    import types
    g = types.SimpleNamespace()
    s = (t <= cut)
    bd_full = load_bd
    globals()["load_bd"] = lambda name: (t[s], N[s])
    try: f2 = features("BTC")
    finally: globals()["load_bd"] = bd_full
    n2 = len(f2["bin"])
    for k in ("z0.2", "z1.0", "z5.0", "thin"):
        a, b = orig[k][:n2], f2[k]
        assert np.array_equal(np.isnan(a), np.isnan(b)) and np.allclose(a[~np.isnan(a)], b[~np.isnan(b)], rtol=1e-6), "look-ahead in " + k
    # (3) sanity: imbalance of a hand-made snapshot
    assert abs(((3.0 - 1.0) / (3.0 + 1.0)) - 0.5) < 1e-12
    print("tests passed: alignment, no look-ahead (z0.2, z1.0, z5.0, thin unchanged when later data is cut)")


# ------------------------------------------------------------------ Part 1: signal quality
def part1():
    rows = {w: [] for w in ("A", "B")}
    H = (30, 120, 240)
    for nm in DESIGN:
        f = pickle.load(open(os.path.join(OUT, f"f_{nm}.pkl"), "rb")); co = C.load_coin(nm)
        i = f["sig_i"]; good = (i >= 0) & (i + 1 < co.N); idx = i[good]
        fw = C.fixed_horizon(co.o, co.c, idx, np.ones(len(idx), np.int64), H)
        ent = co.t0 + (idx + 1) * C.MIN
        rows_i = dict(ent=ent, fw=fw, z={L: f[f"z{L}"][good] for L in LS}, z1=f["z1.0"][good], thin=f["thin"][good], nm=nm)
        for w, T in (("A", T_A), ("B", T_B)):
            m = inwin(ent, T); rows[w].append({k: (v[m] if isinstance(v, np.ndarray) else ({L: a[m] for L, a in v.items()} if isinstance(v, dict) else v)) for k, v in rows_i.items()})
    lines = ["# Part 1: forward return (bps, long) by quintile of Z_L (pooled design coins), top-minus-bottom with day-clustered t"]
    for w in ("A", "B"):
        ent = np.concatenate([r["ent"] for r in rows[w]]); fw = np.vstack([r["fw"] for r in rows[w]])
        lines.append(f"\n## Set {w} ({'2023-01..2024-06' if w == 'A' else '2024-07..2025-06'}), n bins = {len(ent):,}")
        for L in LS:
            z = np.concatenate([r["z"][L] for r in rows[w]]); ok = np.isfinite(z)
            if ok.sum() < 5000: lines.append(f"L={L}%: no book data in this set (Binance has the 0.2% level only from 2026)"); continue
            q = np.digitize(z, np.nanquantile(z[ok], [0.2, 0.4, 0.6, 0.8]))
            for k, h in enumerate(H):
                means = [np.nanmean(fw[ok & (q == j), k]) for j in range(5)]
                d = np.where(ok & (q == 4), fw[:, k], np.where(ok & (q == 0), -fw[:, k], np.nan))   # top quintile long, bottom quintile short
                st = cl(d[np.isfinite(d)], ent[np.isfinite(d)])
                lines.append(f"L={L}% H={h:>3}m  Q1..Q5: " + " ".join(f"{m:+6.2f}" for m in means) + f"   top-bottom {means[4] - means[0]:+6.2f} bps; traded (long top, short bottom) mean {st['mean']:+.2f} bps, t {st['t']:+.2f}")
        # volatility: does |Z_1| predict the next 30 minutes' absolute return?
        z = np.abs(np.concatenate([r["z1"] for r in rows[w]])); ok = np.isfinite(z) & np.isfinite(fw[:, 0])
        q = np.digitize(z, np.nanquantile(z[ok], [0.2, 0.4, 0.6, 0.8])); ab = np.abs(fw[:, 0])
        lines.append("|Z_1| quintile -> mean |next 30m return| (bps): " + " ".join(f"{np.nanmean(ab[ok & (q == j)]):6.2f}" for j in range(5)))
        th = np.concatenate([r["thin"] for r in rows[w]]); ok = np.isfinite(th) & np.isfinite(fw[:, 0])
        q = np.digitize(th, np.nanquantile(th[ok], [0.2, 0.4, 0.6, 0.8]))
        lines.append("THIN quintile (low=thin book) -> mean |next 30m return| (bps): " + " ".join(f"{np.nanmean(ab[ok & (q == j)]):6.2f}" for j in range(5)))
    txt = "\n".join(lines); print(txt); open(os.path.join(HERE, "r10_part1_out.txt"), "w").write(txt + "\n")


# ------------------------------------------------------------------ Part 2: trading cells
def run_set(names, T, L, K, H, d, slip=0.0):
    E, X, R, S, ID = [], [], [], [], []
    for ci, nm in enumerate(names):
        p = os.path.join(OUT, f"f_{nm}.pkl")
        if not os.path.exists(p): continue
        f = pickle.load(open(p, "rb")); co = C.load_coin(nm); ft, fr = C.load_funding(nm); atr = atr30(co)
        e, x, r, s = cell_events(co, f, L, K, H, d, ft, fr, atr, slip); m = inwin(e, T)
        E.append(e[m]); X.append(x[m]); R.append(r[m]); S.append(s[m]); ID.append(np.full(m.sum(), ci, np.int64))
    cat = lambda a, dt: np.concatenate(a) if a else np.zeros(0, dt)
    return cat(E, np.int64), cat(X, np.int64), cat(R, float), cat(S, float), cat(ID, np.int64)


def summ(e, x, r, s, cid, days):
    if len(r) < 2: return dict(n=len(r), netR=np.nan, t=np.nan, gross=np.nan, eq=10.0, tk=0, dd=0.0, perday=0.0)
    R = r / s; st = cl(R, e); eq, tk, dd = money(e, x, r, s, cid)
    return dict(n=len(r), netR=st["mean"], t=st["t"], gross=(r.mean() + 0.0014) * 1e4, eq=eq, tk=tk, dd=dd, perday=len(r) / days)


def collect(names, cells, slip=0.0):
    """coin-outer loop (each coin loaded once): {cell: [(coin index, entry, exit, ret, stopfrac), ...]}"""
    out = {c: [] for c in cells}
    for ci, nm in enumerate(names):
        p = os.path.join(OUT, f"f_{nm}.pkl")
        if not os.path.exists(p): continue
        f = pickle.load(open(p, "rb")); co = C.load_coin(nm); ft, fr = C.load_funding(nm); atr = atr30(co)
        for c in cells:
            L = c[0]
            if np.isfinite(f[f"z{L}"]).sum() < 1000: continue
            out[c].append((ci,) + cell_events(co, f, *c, ft, fr, atr, slip))
        print("  coin", nm, flush=True)
    return out


def pack(parts, T):
    E = [p[1][inwin(p[1], T)] for p in parts]
    if not parts: return (np.zeros(0, np.int64), np.zeros(0, np.int64), np.zeros(0), np.zeros(0), np.zeros(0, np.int64))
    M = [inwin(p[1], T) for p in parts]
    return (np.concatenate([p[1][m] for p, m in zip(parts, M)]), np.concatenate([p[2][m] for p, m in zip(parts, M)]), np.concatenate([p[3][m] for p, m in zip(parts, M)]),
            np.concatenate([p[4][m] for p, m in zip(parts, M)]), np.concatenate([np.full(m.sum(), p[0], np.int64) for p, m in zip(parts, M)]))


def select():
    cells = [(L, K, H, d) for L in LS for K in KS for H in HS for d in ("WITH", "AGAINST")]
    col = collect(DESIGN, cells)
    res = {}; lines = ["# Part 2 SELECTION on design coins: A 2023-01..2024-06, B 2024-07..2025-06 (24 cells; the 12 cells at L=0.2% have no book data before 2026 and cannot qualify)"]
    for c in cells:
        a = pack(col[c], T_A); b = pack(col[c], T_B); ab = pack(col[c], (T_A[0], T_B[1]))
        sa, sb, sab = summ(*a, 540), summ(*b, 365), summ(*ab, 905)
        res[c] = (sa, sb, sab)
        lines.append(f"L={c[0]} K={c[1]} H={c[2]:>3} {c[3]:8s} A: n={sa['n']:5d} netR={sa['netR']:+.3f} gross={sa['gross']:+6.1f}bps | B: n={sb['n']:5d} netR={sb['netR']:+.3f} gross={sb['gross']:+6.1f} | A+B t={sab['t']:+.2f} n={sab['n']}")
    frozen = {}
    for d in ("WITH", "AGAINST"):
        ok = [(c, v[2]["t"]) for c, v in res.items() if c[3] == d and v[2]["n"] >= 300 and v[0]["netR"] > 0 and v[1]["netR"] > 0]
        if ok:
            c = max(ok, key=lambda q: q[1])[0]; frozen[d] = list(c); lines.append(f"FROZEN {d}: {c}")
        else: lines.append(f"FROZEN {d}: none qualified (n>=300 and net R>0 in A and in B)")
    txt = "\n".join(lines); print(txt)
    open(os.path.join(HERE, "r10_select_out.txt"), "w").write(txt + "\n"); json.dump(frozen, open(os.path.join(HERE, "r10_frozen.json"), "w"))


def judge():
    frozen = json.load(open(os.path.join(HERE, "r10_frozen.json"))); lines = ["# Part 2 JUDGES (run once on the frozen cells)"]
    sets = (("J1 design coins FINAL", DESIGN, T_J1, 456), ("J2 unseen coins", UNSEEN, T_ALL, 1369 if False else 1369), ("J3 fresh coins", FRESH, T_ALL, 1369))
    if not frozen: lines.append("nothing frozen: no cell qualified in selection, so no judge was run."); print("\n".join(lines))
    for d, c in frozen.items():
        c = tuple(c); ok = True
        for nm, names, T, days in sets:
            s = summ(*run_set(names, T, *c), days); lines.append(f"{d} {c} {nm}: n={s['n']} ({s['perday']:.2f}/day) gross={s['gross']:+.1f}bps netR={s['netR']:+.3f} t={s['t']:+.2f}  $10 -> ${s['eq']:.2f} (taken {s['tk']}, dd {s['dd']*100:.0f}%)")
            if nm.startswith("J3"):
                s2 = summ(*run_set(names, T, *c, 0.0002), days); lines.append(f"    J3 with +2 bps slippage per side: netR={s2['netR']:+.3f}")
    txt = "\n".join(lines); print(txt); open(os.path.join(HERE, "r10_judge_out.txt"), "w").write(txt + "\n")


# ------------------------------------------------------------------ Part 3: the imbalance as a filter on the two systems that work
def z1_at(f, e_ms):
    """book Z_1 known at an entry time: the last bin whose start + 5 minutes is at or before the entry"""
    b = (e_ms - C.GRID_T0) // BIN - 1 - f["bin"][0]
    ok = (b >= 0) & (b < len(f["bin"])); z = np.full(len(e_ms), np.nan); z[ok] = f["z1.0"][b[ok]]
    return z


def system_trades(nm, btc, td):
    """{'panel': (e, x, ret, sf, side, R), 'kalman': (...)} for one coin, from 2023-01 on"""
    import sys as _s
    _s.path.insert(0, os.path.join(HERE, "..", ".."))
    import hf_ladder as LD, r6_fast as R6, r7_sens as R7
    from backend_lib import kalman_trend as K
    co = btc if nm == "BTC" else C.load_coin(nm); ft, fr = C.load_funding(nm); out = {}
    B = 30
    t, O, H, L, Cc, Q, TB, valid, a = R7.prep(co, B)
    day = np.clip(((t + B * 60000) // C.DAY) - 1 - co.t0 // C.DAY, 0, len(td) - 1); tb = td[day]
    zs = R7.estimator_z(O, H, L, Cc, Q, TB); zslow = R6.kalman_z_p(Cc, 1e-4); S = R7.votes(zs, 1.0)
    e, x, r, s, rs, sd = R7.run_s(t, O, H, L, Cc, S, a, valid, tb, ft, fr, B, 8, 99, zslow)[:6]
    out["panel"] = (e, x, r, s, sd, r / s)
    def bars(c_):
        o_, h_, l_, c_c, *_ = C.aggregate(c_.o, c_.h, c_.l, c_.c, c_.qv, c_.n, c_.tb, 240)
        tt = c_.t0 + np.arange(len(o_), dtype=np.int64) * 240 * 60000; ok = ~np.isnan(c_c); return tt[ok], o_[ok], h_[ok], l_[ok], c_c[ok]
    tk, ok_, hk, lk, ck = [q.tolist() for q in bars(co)]; tbb, _, _, _, cbb = bars(btc); tbb = tbb.tolist(); cbb = cbb.tolist()
    trend = dict(zip(tbb, K.daily_trend(tbb, cbb))); z, _ = K.kalman(ck); ak = K.atr14(hk, lk, ck); ftl, frl = ft.tolist(), fr.tolist()
    tr = [q for q in K.coin_trades(tk, ok_, hk, lk, ck, z, ak, lambda ti: trend.get(ti, 0), lambda tc: K.funding_avg(ftl, frl, tc)) if q["exit_t"] is not None]
    if tr:
        R_ = np.array([K.net_r(q, ftl, frl) for q in tr]); sf_ = np.array([q["risk"] / abs(q["entry_p"]) for q in tr])
        out["kalman"] = (np.array([q["entry_t"] for q in tr], np.int64), np.array([q["exit_t"] for q in tr], np.int64), R_ * sf_, sf_,
                         np.array([1 if q["side"] == "L" else -1 for q in tr]), R_)
    else: out["kalman"] = tuple(np.zeros(0) for _ in range(6))
    return out


def part3():
    import hf_ladder as LD
    btc = C.load_coin("BTC"); td = LD.btc_daily_trend(btc)
    sets = {"J1": (DESIGN, T_J1), "J2": (UNSEEN, T_ALL), "J3": (FRESH, T_ALL)}
    lines = ["# Part 3: book imbalance as a filter. Skip a trade when side * Z_1 <= -thr (the book leans against the trade). Sets use 2023-01 on (book data)."]
    data = {}
    for sn, (names, T) in sets.items():
        for sysn in ("panel", "kalman"): data[(sn, sysn)] = []
        for ci, nm in enumerate(names):
            f = pickle.load(open(os.path.join(OUT, f"f_{nm}.pkl"), "rb")); st = system_trades(nm, btc, td)
            for sysn, (e, x, r, s, sd, R) in st.items():
                m = inwin(e, T) & (e >= C.ms(2023, 1, 8))
                z = z1_at(f, e[m]); data[(sn, sysn)].append((np.full(m.sum(), ci, np.int64), e[m], x[m], r[m], s[m], sd[m] * z, R[m]))
            print("  ", sn, nm, flush=True)
    for sysn in ("panel", "kalman"):
        for thr in (1.0, 2.0):
            pooled_k, pooled_r = [], []; wins = 0; row = []
            for sn in sets:
                P = [np.concatenate([p[q] for p in data[(sn, sysn)]]) for q in range(7)]
                cid, e, x, r, s, sz, R = P; against = np.isfinite(sz) & (sz <= -thr); keep = ~against
                kk, rr = cl(R[keep], e[keep]), cl(R[against], e[against])
                eq_all = money(e, x, r, s, cid)[0]; eq_f = money(e[keep], x[keep], r[keep], s[keep], cid[keep])[0]
                wins += eq_f > eq_all; pooled_k.append((R[keep], e[keep])); pooled_r.append((R[against], e[against]))
                row.append(f"{sn}: n={len(R)} removed {against.sum()} ({against.mean()*100:.0f}%) meanR kept {kk['mean']:+.3f} removed {rr['mean']:+.3f} | $10 all ${eq_all:.2f} filtered ${eq_f:.2f}")
            Rk = np.concatenate([a for a, _ in pooled_k]); ek = np.concatenate([b for _, b in pooled_k]); Rr = np.concatenate([a for a, _ in pooled_r]); er = np.concatenate([b for _, b in pooled_r])
            a_, b_ = cl(Rk, ek), cl(Rr, er); td_ = (a_["mean"] - b_["mean"]) / np.hypot(a_["se"], b_["se"]) if b_["n"] > 1 else float("nan")
            lines.append(f"\n{sysn} thr={thr}: pooled kept {a_['mean']:+.3f}R (n={a_['n']}) vs removed {b_['mean']:+.3f}R (n={b_['n']}), difference t {td_:+.2f}; filtered beats unfiltered in {wins} of 3 $10 portfolios")
            lines += ["   " + q for q in row]
    txt = "\n".join(lines); print(txt); open(os.path.join(HERE, "r10_part3_out.txt"), "w").write(txt + "\n")


if __name__ == "__main__":
    {"test": test, "build": build, "part1": part1, "select": select, "judge": judge, "part3": part3}[sys.argv[1]]()
