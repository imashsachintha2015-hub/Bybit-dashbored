"""Round 3, idea C (PREREG_round3.md): a better entry for the production 4H Kalman signals (limit orders below/above the signal close).
Everything is walked on 1m bars. The signal set is the baseline's (the trades hf_ladder.py takes); each variant is evaluated on the same
signals, so the comparison is paired.
  python3 r3_kentry.py build            -> per-signal results of baseline and 12 variants, design + unseen coins; parity vs hf_ladder
  python3 r3_kentry.py screen | final"""
import os, sys, json, pickle, itertools
import numpy as np
import numba as nb
import hf_core as C
import hf_ladder as LD
import r3_common as R3

B = 240
VARIANTS = [(p, W, mode) for p in (0.25, 0.5, 1.0) for W in (1, 3) for mode in ("skip", "market")]
HERE = os.path.dirname(os.path.abspath(__file__))


def vname(v): return f"KENTRY|X|p{v[0]:g}|W{v[1]}|{v[2]}"


@nb.njit(cache=True)
def signals(t, o, h, l, c, z, a, valid, trend_bar, ft, fr):
    """the baseline's taken signals (exactly hf_ladder.run_coin's entry logic with its busy rule); returns signal bar indices"""
    n = len(c); out = np.zeros(20000, np.int64); k = 0; busy = -1
    for i in range(max(LD.WARMUP, 1), n - 1):
        if not valid[i] or np.isnan(z[i]) or np.isnan(z[i - 1]): continue
        sgn = 0
        if z[i] > LD.Z_IN and z[i - 1] <= LD.Z_IN: sgn = 1
        elif z[i] < -LD.Z_IN and z[i - 1] >= -LD.Z_IN: sgn = -1
        if sgn == 0: continue
        if t[i + 1] < busy: continue
        tr = trend_bar[i]; tc = t[i] + B * 60000
        lo = np.searchsorted(ft, tc - LD.FUND_WINDOW, side="right"); hi = np.searchsorted(ft, tc, side="right")
        have = len(ft) > 0 and ft[0] <= tc - LD.FUND_WINDOW + 8 * 3600_000 and hi - lo >= 3
        favg = 0.0
        if have:
            for q in range(lo, hi): favg += fr[q]
            favg /= 9.0
        if sgn == 1:
            if tr != 1: continue
            if have and favg > LD.FUND_CAP: continue
        else:
            if tr != -1: continue
            if not have or not favg > 0: continue
        if not valid[i + 1]: continue
        # baseline exit time decides busy (4H-bar walk as in hf_ladder)
        ep = o[i + 1]; stop = c[i] - sgn * LD.STOP_ATR * a[i]
        if sgn * (ep - stop) < LD.MINSTOP * ep: stop = ep - sgn * LD.MINSTOP * ep
        xt = -1
        for j in range(i + 1, min(n, i + 1 + LD.HOLD)):
            if (sgn == 1 and l[j] <= stop) or (sgn == -1 and h[j] >= stop): xt = t[j] + B * 60000; break
            if (sgn == 1 and z[j] < 0) or (sgn == -1 and z[j] > 0):
                if j + 1 < n: xt = t[j + 1] + B * 60000
                break
        else:
            j = i + LD.HOLD
            if j <= n - 1: xt = t[j] + B * 60000
        if xt < 0: continue
        out[k] = i; k += 1
        busy = xt
    return out[:k]


@nb.njit(cache=True)
def walk_trade(o1, h1, l1, c1, z, a, c4, i, sgn, p_off, W, mode, base):
    """one signal on 1m bars. returns (filled, entry 1m index, entry price, maker, exit 1m index (exclusive end), exit price)"""
    N = len(o1); n4 = len(c4)
    m0 = (i + 1) * B
    if m0 >= N or np.isnan(o1[m0]): return False, -1, 0.0, False, -1, 0.0
    fill = -1; px = 0.0; maker = False
    if base:
        fill = m0; px = o1[m0]
    else:
        Lp = c4[i] - sgn * p_off * a[i]
        if sgn * (o1[m0] - Lp) <= 0:
            fill = m0; px = o1[m0]
        else:
            for j in range(i + 1, min(i + 1 + W, n4)):
                for m in range(j * B, min((j + 1) * B, N)):
                    if np.isnan(l1[m]): continue
                    if (sgn == 1 and l1[m] <= Lp * (1 - 1e-4)) or (sgn == -1 and h1[m] >= Lp * (1 + 1e-4)):
                        fill = m; px = Lp; maker = True; break
                if fill >= 0: break
                if (sgn == 1 and z[j] < 0) or (sgn == -1 and z[j] > 0): return False, -1, 0.0, False, -1, 0.0   # signal died: cancel
            if fill < 0:
                if mode == 0: return False, -1, 0.0, False, -1, 0.0
                mm = (i + 1 + W) * B
                if mm >= N or np.isnan(o1[mm]) or i + 1 + W > i + LD.HOLD: return False, -1, 0.0, False, -1, 0.0
                fill = mm; px = o1[mm]
    stop = c4[i] - sgn * LD.STOP_ATR * a[i]
    if sgn * (px - stop) < LD.MINSTOP * px: stop = px - sgn * LD.MINSTOP * px
    jf = fill // B
    last_bar = min(i + LD.HOLD, n4 - 1)
    for j in range(jf, last_bar + 1):
        for m in range(max(j * B, fill), min((j + 1) * B, N)):
            if np.isnan(l1[m]): continue
            if m > fill and ((sgn == 1 and o1[m] <= stop) or (sgn == -1 and o1[m] >= stop)): return True, fill, px, maker, m + 1, o1[m]
            if (sgn == 1 and l1[m] <= stop) or (sgn == -1 and h1[m] >= stop): return True, fill, px, maker, m + 1, stop
        if (sgn == 1 and z[j] < 0) or (sgn == -1 and z[j] > 0):
            mx = (j + 1) * B
            if mx < N and np.isfinite(o1[mx]): return True, fill, px, maker, mx + B, o1[mx]          # ladder books the exit at the bar end
            break
    j = last_bar
    return True, fill, px, maker, (j + 1) * B, c4[j]


def coin_build(co, ft, fr, trend_days):
    O, H, L, Cc, *_ = C.aggregate(co.o, co.h, co.l, co.c, co.qv, co.n, co.tb, B)
    valid = ~(np.isnan(O) | np.isnan(H) | np.isnan(L) | np.isnan(Cc)); first = np.argmax(valid)
    O, _ = LD.ffill(O); H, _ = LD.ffill(H); L, _ = LD.ffill(L); Cc, _ = LD.ffill(Cc)
    O[:first] = Cc[:first] = H[:first] = L[:first] = Cc[first]; valid[:first] = False
    t = co.t0 + np.arange(len(O), dtype=np.int64) * B * 60000
    z = LD.kalman_z(Cc); a = LD.atr14_rma(H, L, Cc)
    day = np.clip(((t + B * 60000) // C.DAY) - 1 - co.t0 // C.DAY, 0, len(trend_days) - 1)
    tb = trend_days[day]
    sig = signals(t, O, H, L, Cc, z, a, valid, tb, ft, fr)
    sides = np.where(z[sig] > 0, 1, -1)
    res = {}
    for v in [None] + VARIANTS:
        rows = []
        for i, sgn in zip(sig, sides):
            if v is None: f, e, pe, mk, x, px = walk_trade(co.o, co.h, co.l, co.c, z, a, Cc, i, sgn, 0.0, 0, 0, True)
            else: f, e, pe, mk, x, px = walk_trade(co.o, co.h, co.l, co.c, z, a, Cc, i, sgn, v[0], v[1], 0 if v[2] == "skip" else 1, False)
            sig_t = t[i] + B * 60000
            if not f:
                rows.append((sig_t, 0, 0, 0.0, 0.01, 0)); continue
            te = co.t0 + e * 60000; tx = co.t0 + x * 60000
            stop = Cc[i] - sgn * LD.STOP_ATR * a[i]
            if sgn * (pe - stop) < LD.MINSTOP * pe: stop = pe - sgn * LD.MINSTOP * pe
            gross = sgn * (px / pe - 1.0)
            net = gross - (C.MAKER if mk else C.TAKER) - C.TAKER - C.funding_paid(ft, fr, te, tx, sgn)
            rows.append((sig_t, te, tx, net, sgn * (pe - stop) / pe, 1))
        res["BASE" if v is None else vname(v)] = np.array(rows, dtype=float).reshape(-1, 6)
    return res


def build():
    btc = C.load_coin("BTC"); trend_days = LD.btc_daily_trend(btc)
    for which, names in (("design", C.DESIGN), ("unseen", C.UNSEEN)):
        allres = {}
        for cid, nm in enumerate(names):
            co = btc if nm == "BTC" else C.load_coin(nm); ft, fr = C.load_funding(nm)
            res = coin_build(co, ft, fr, trend_days)
            for k, arr in res.items():
                arr = np.column_stack([arr, np.full(len(arr), cid)])
                allres.setdefault(k, []).append(arr)
            # parity of the 1m-walked baseline with hf_ladder (4H walk)
            e, x, r, s, rs = LD.coin_rung(co, ft, fr, trend_days, B)
            bb = res["BASE"]
            same_n = len(e) == len(bb)
            d = np.abs(bb[:, 3] - r) if same_n else np.array([np.nan])
            print(f"{which} {nm}: signals {len(bb)} vs ladder {len(e)}; |net diff| median {np.median(d) * 1e4:.2f} bps, "
                  f"share within 1 bp {np.mean(d < 1e-4) * 100:.0f}%, max {np.max(d) * 1e4:.0f} bps", flush=True)
        allres = {k: np.concatenate(v) for k, v in allres.items()}
        pickle.dump(allres, open(os.path.join(R3.OUT, f"kentry_{which}.pkl"), "wb"))


def seg(arr, s):
    return C.seg_mask(arr[:, 0].astype(np.int64), "FINAL" if s == "UNSEEN" else s)


def R_of(arr): return np.where(arr[:, 5] > 0, arr[:, 3] / arr[:, 4], 0.0)


def paired(base, var, s):
    m = seg(base, s)
    if m.sum() < 2: return dict(n=int(m.sum()), mean=np.nan, t=np.nan)
    dR = R_of(var)[m] - R_of(base)[m]
    st = C.cluster_stats(dR, base[m, 0].astype(np.int64) // C.DAY)
    st["fill"] = float(var[m, 5].mean()); st["base_R"] = float(R_of(base)[m].mean()); st["var_R"] = float(R_of(var)[m].mean())
    return st


def money(arr, s, risk=0.005):
    m = seg(arr, s) & (arr[:, 5] > 0)
    if m.sum() < 1: return 10.0, 0, 0.0
    a = arr[m]
    eq, taken, mdd, _, _ = C.portfolio(a[:, 1].astype(np.int64), a[:, 2].astype(np.int64), a[:, 3], a[:, 4], a[:, 6].astype(np.int64), 10.0, risk, 8, 3.0, 6.0)
    return eq, taken, mdd


def screen():
    d = pickle.load(open(os.path.join(R3.OUT, "kentry_design.pkl"), "rb"))
    base = d["BASE"]
    print(f"# KENTRY DEV: baseline signals {seg(base, 'DEV').sum()}, baseline mean R {R_of(base)[seg(base, 'DEV')].mean():+.3f}")
    rows = []
    for v in VARIANTS:
        st = paired(base, d[vname(v)], "DEV"); rows.append((vname(v), st))
        print(f"  {vname(v):28s} fill {st['fill'] * 100:4.0f}%  variant R {st['var_R']:+.3f}  delta R {st['mean']:+.3f} t {st['t']:+.2f}")
    surv = [(c, st) for c, st in rows if st["mean"] > 0 and st["t"] >= 2.0]
    conf = []
    print(f"# DEV screen survivors: {len(surv)}")
    for c, st in surv:
        sv = paired(base, d[c], "VAL")
        ok = sv["mean"] > 0 and sv["t"] >= 1.5
        print(f"  {c} VAL delta R {sv['mean']:+.3f} t {sv['t']:+.2f} {'CONFIRMED' if ok else ''}")
        if ok: conf.append(c)
    best = max(rows, key=lambda r: r[1]["t"])[0]
    json.dump(dict(confirmed=conf, report=[best]), open(os.path.join(HERE, "r3_frozen_KENTRY.json"), "w"), indent=1)


def final():
    fz = json.load(open(os.path.join(HERE, "r3_frozen_KENTRY.json")))
    d = pickle.load(open(os.path.join(R3.OUT, "kentry_design.pkl"), "rb")); u = pickle.load(open(os.path.join(R3.OUT, "kentry_unseen.pkl"), "rb"))
    for c in list(dict.fromkeys(fz["confirmed"] + fz["report"])):
        lab = "CONFIRMED" if c in fz["confirmed"] else "best DEV variant (reporting rule)"
        print(f"\n## {lab}: {c}  (paired against the market-entry baseline on the same signals; $10 at 0.5% risk, max 8 open)")
        for s in ("DEV", "VAL", "FINAL", "OLD", "UNSEEN"):
            dd = u if s == "UNSEEN" else d
            st = paired(dd["BASE"], dd[c], s)
            eb, nb_, mb = money(dd["BASE"], s); ev, nv, mv = money(dd[c], s)
            print(f"  {s:7s} signals {st['n']:4d} fill {st['fill'] * 100:4.0f}% | baseline R {st['base_R']:+.3f} $10 -> {eb:6.2f} ({nb_} tr, DD {mb * 100:.0f}%) | "
                  f"variant R {st['var_R']:+.3f} $10 -> {ev:6.2f} ({nv} tr, DD {mv * 100:.0f}%) | delta R {st['mean']:+.3f} t {st['t']:+.2f}")


if __name__ == "__main__":
    {"build": build, "screen": screen, "final": final}[sys.argv[1]]()
