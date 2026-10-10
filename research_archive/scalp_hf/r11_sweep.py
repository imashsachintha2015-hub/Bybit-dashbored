"""Round 11 (PREREG_round11.md): wait for the liquidity sweep to finish (reclaim + taker-flow confirmation), then enter.
usage: python3 r11_sweep.py test | select | judge"""
import os, sys, json
import numpy as np
import numba as nb
import hf_core as C
import hf_ladder as LD
import r6_fast as R6
import r7_sens as R7

B = 15
DESIGN, UNSEEN = C.DESIGN, C.UNSEEN
FRESH = "AAVE BCH CRV ETC FIL HBAR ICP RUNE TRX UNI".split()
HERE = os.path.dirname(os.path.abspath(__file__))
inwin, cl, money = R6.inwin, R6.cl, R6.money
T_A, T_B, T_J1 = R6.T_A, R6.T_B, R6.T_J1; T_ALL = (C.ms(2021), C.ms(2026, 10))
WARM, FEE, HOLD, MINSTOP = 300, 0.0014, 192, 0.005
SWEEP_ATR, RECLAIM_BARS, EXT_ATR = 0.1, 3, 0.1
VOL_MULT, VOL_WIN, D_SWEEP, D_RECLAIM = 1.8, 50, 0.25, 0.15
NS, TMS, TGS, CONFS = (96, 288), (2.0, 4.0), (0, 1), (0, 1, 2, 3)


@nb.njit(cache=True)
def run(t, o, h, l, c, v, tb, a, valid, trend, ft, fr, N, conf, tm, tg, slip):
    """sweep -> reclaim within RECLAIM_BARS -> confirmation -> market entry at the next open. returns entry ms, exit ms, net return, stop fraction, side"""
    n = len(c); cap = 60000
    ent = np.zeros(cap, np.int64); ext = np.zeros(cap, np.int64); ret = np.zeros(cap); sf = np.zeros(cap); sd = np.zeros(cap, np.int64)
    k = 0; busy = -1
    for j in range(max(WARM, N + 1), n - RECLAIM_BARS - 2):
        if not valid[j] or not (a[j - 1] > 0): continue
        for side in (1, -1):
            if side == 1:
                lvl = l[j - N]
                for q in range(j - N + 1, j):
                    if l[q] < lvl: lvl = l[q]
                if not (l[j] < lvl - SWEEP_ATR * a[j - 1]): continue
            else:
                lvl = h[j - N]
                for q in range(j - N + 1, j):
                    if h[q] > lvl: lvl = h[q]
                if not (h[j] > lvl + SWEEP_ATR * a[j - 1]): continue
            kk = -1; ex = l[j] if side == 1 else h[j]
            for q in range(j, j + RECLAIM_BARS):
                if side == 1:
                    if l[q] < ex: ex = l[q]
                    if c[q] > lvl: kk = q; break
                else:
                    if h[q] > ex: ex = h[q]
                    if c[q] < lvl: kk = q; break
            if kk < 0 or not valid[kk] or not valid[kk + 1]: continue
            if tg == 1 and trend[kk] != side: continue
            # confirmation
            ok1 = v[kk] > 0 and side * (2 * tb[kk] - v[kk]) / v[kk] >= D_RECLAIM
            ok2 = False
            if v[j] > 0:
                s = 0.0; m = np.sort(v[j - VOL_WIN:j]); med = 0.5 * (m[VOL_WIN // 2 - 1] + m[VOL_WIN // 2])
                ok2 = med > 0 and v[j] >= VOL_MULT * med and side * (2 * tb[j] - v[j]) / v[j] <= -D_SWEEP
            if conf == 1 and not ok1: continue
            if conf == 2 and not ok2: continue
            if conf == 3 and not (ok1 and ok2): continue
            te = t[kk + 1]
            if te < busy: continue
            ep = o[kk + 1]; stop = ex - side * EXT_ATR * a[kk]
            if side * (ep - stop) < MINSTOP * ep: stop = ep - side * MINSTOP * ep
            risk = side * (ep - stop)
            if risk <= 0: continue
            tp = ep + side * tm * risk
            xt = -1; xp = 0.0
            for q in range(kk + 1, min(n, kk + 1 + HOLD)):
                if (side == 1 and o[q] <= stop) or (side == -1 and o[q] >= stop): xt, xp = t[q] + B * 60000, o[q]; break
                if (side == 1 and l[q] <= stop) or (side == -1 and h[q] >= stop): xt, xp = t[q] + B * 60000, stop; break
                if (side == 1 and h[q] >= tp) or (side == -1 and l[q] <= tp): xt, xp = t[q] + B * 60000, tp; break
            else:
                q = kk + HOLD
                if q <= n - 1: xt, xp = t[q] + B * 60000, c[q]
            if xt < 0: continue
            gross = side * (xp / ep - 1.0); f = 0.0
            a0 = np.searchsorted(ft, te, side="right"); b0 = np.searchsorted(ft, xt, side="right")
            for q in range(a0, b0): f += fr[q]
            f *= side
            if k < cap:
                ent[k] = te; ext[k] = xt; ret[k] = gross - FEE - 2 * slip - f; sf[k] = risk / ep; sd[k] = side; k += 1
            busy = xt
    return ent[:k], ext[:k], ret[:k], sf[:k], sd[:k]


def coin_inputs(nm, btc, td):
    co = btc if nm == "BTC" else C.load_coin(nm); ft, fr = C.load_funding(nm)
    t, O, H, L, Cc, Q, TB, valid, a = R7.prep(co, B)
    day = np.clip(((t + B * 60000) // C.DAY) - 1 - co.t0 // C.DAY, 0, len(td) - 1)
    return t, O, H, L, Cc, Q, TB, a, valid, td[day], ft, fr


def cells():
    return [(N, cf, tm, tg) for N in NS for cf in CONFS for tm in TMS for tg in TGS]


def collect(names, which, slip=0.0):
    """coin-outer loop: {cell: [(coin index, entry, exit, ret, sf, side)]}"""
    btc = C.load_coin("BTC"); td = LD.btc_daily_trend(btc); out = {c: [] for c in which}
    for ci, nm in enumerate(names):
        x = coin_inputs(nm, btc, td)
        for c in which:
            N, cf, tm, tg = c
            e, xx, r, s, sd = run(*x[:10], x[10], x[11], N, cf, tm, tg, slip)
            out[c].append((ci, e, xx, r, s, sd))
        print("  coin", nm, flush=True)
    return out


def pack(parts, T):
    M = [inwin(p[1], T) for p in parts]
    if not parts: return tuple(np.zeros(0) for _ in range(5))
    return (np.concatenate([p[1][m] for p, m in zip(parts, M)]), np.concatenate([p[2][m] for p, m in zip(parts, M)]), np.concatenate([p[3][m] for p, m in zip(parts, M)]),
            np.concatenate([p[4][m] for p, m in zip(parts, M)]), np.concatenate([np.full(m.sum(), p[0], np.int64) for p, m in zip(parts, M)]))


def summ(e, x, r, s, cid, days):
    if len(r) < 2: return dict(n=len(r), netR=np.nan, t=np.nan, gross=np.nan, eq=10.0, tk=0, dd=0.0, perday=0.0)
    st = cl(r / s, e); eq, tk, dd = money(e, x, r, s, cid)
    return dict(n=len(r), netR=st["mean"], t=st["t"], gross=(r.mean() + FEE) * 1e4, eq=eq, tk=tk, dd=dd, perday=len(r) / days)


def name(c): return f"N={c[0]:3d} CONF=C{c[1]} TM={c[2]:.0f}R TG={'BTC' if c[3] else 'none'}"


def test():
    """no look-ahead: signals before a cut are identical on cut data; a hand-made sweep gives the expected trade"""
    btc = C.load_coin("BTC"); td = LD.btc_daily_trend(btc); x = coin_inputs("ETH", btc, td)
    for cf in (0, 3):
        full = run(*x[:10], x[10], x[11], 96, cf, 2.0, 0, 0.0)
        cut_i = len(x[0]) // 2; xc = [a[:cut_i] if hasattr(a, "__len__") and len(a) == len(x[0]) else a for a in x]
        part = run(*xc[:10], x[10], x[11], 96, cf, 2.0, 0, 0.0)
        tcut = x[0][cut_i - 2 * HOLD]                                   # trades that finished well before the cut are the same
        f = [(int(a), int(b)) for a, b in zip(full[0], full[1]) if b < x[0][cut_i - 1]]; p = [(int(a), int(b)) for a, b in zip(part[0], part[1]) if b < x[0][cut_i - 1]]
        assert f[:len(p) - 1] == p[:len(p) - 1] and len(p) > (100 if cf == 0 else 10), "look-ahead"
    # synthetic: flat, then a low sweep that closes back above the level, then a rally -> one long that hits the 2R target
    n = 700; t = np.arange(n, dtype=np.int64) * B * 60000; c = np.full(n, 100.0); o = c.copy(); h = c + 0.3; l = c - 0.3; v = np.full(n, 100.0); tb = np.full(n, 50.0)
    j = 400; l[j] = 98.0; c[j] = 99.9; h[j] = 100.1; o[j] = 100.0; tb[j] = 20.0; v[j] = 400.0                       # sweep bar: heavy, sellers dominant, closes back inside
    c[j + 1:] = 100.0; o[j + 1:] = 100.0; h[j + 1] = 101.0; h[j + 2:] = 105.0; c[j + 1] = 100.4
    a = np.full(n, 0.6); valid = np.ones(n, np.bool_); trend = np.ones(n, np.int64)
    e, xx, r, s, sd = run(t, o, h, l, c, v, tb, a, valid, trend, np.zeros(0, np.int64), np.zeros(0), 96, 2, 2.0, 0, 0.0)
    assert len(e) == 1 and sd[0] == 1 and abs(s[0] - (100.0 - (98.0 - 0.06)) / 100.0) < 1e-9, (e, sd, s)
    assert r[0] > 0.035, r                                                                                         # 2R on a ~2.1% risk: about +4.1% less fees
    print("tests passed: no look-ahead on real data; synthetic sweep -> one long with the right stop and a 2R target")


def select():
    cs = cells(); col = collect(DESIGN, cs); res = {}
    lines = ["# Round 11 SELECTION on design coins: A 2021-01..2024-06, B 2024-07..2025-06 (32 cells)"]
    for c in cs:
        a = pack(col[c], T_A); b = pack(col[c], T_B); ab = pack(col[c], (T_A[0], T_B[1]))
        sa, sb, sab = summ(*a, 1277), summ(*b, 365), summ(*ab, 1642); res[c] = (sa, sb, sab)
        lines.append(f"{name(c)}  A: n={sa['n']:5d} netR={sa['netR']:+.3f} gross={sa['gross']:+6.1f}bps | B: n={sb['n']:5d} netR={sb['netR']:+.3f} gross={sb['gross']:+6.1f} | A+B n={sab['n']} t={sab['t']:+.2f}")
    frozen = {}
    for cf in CONFS:
        ok = [(c, v[2]["t"]) for c, v in res.items() if c[1] == cf and v[2]["n"] >= 300 and v[0]["netR"] > 0 and v[1]["netR"] > 0]
        if ok: c = max(ok, key=lambda q: q[1])[0]; frozen[f"C{cf}"] = list(c); lines.append(f"FROZEN C{cf}: {name(c)}")
        else: lines.append(f"FROZEN C{cf}: none qualified (n>=300 and net R>0 in A and in B)")
    txt = "\n".join(lines); print(txt)
    open(os.path.join(HERE, "r11_select_out.txt"), "w").write(txt + "\n"); json.dump(frozen, open(os.path.join(HERE, "r11_frozen.json"), "w"))


def judge():
    frozen = json.load(open(os.path.join(HERE, "r11_frozen.json"))); lines = ["# Round 11 JUDGES (frozen cells, run once; C0 baseline of the same N, TM, TG alongside)"]
    if not frozen: lines.append("nothing frozen: no cell qualified in selection, so no judge was run."); print("\n".join(lines)); return
    want = {tuple(v) for v in frozen.values()} | {(v[0], 0, v[2], v[3]) for v in frozen.values()}
    sets = (("J1 design coins FINAL", DESIGN, T_J1, 456), ("J2 unseen coins", UNSEEN, T_ALL, 2008), ("J3 fresh coins", FRESH, T_ALL, 2008))
    allres = {}
    for nm, names, T, days in sets:
        col = collect(names, sorted(want)); col2 = collect(names, [tuple(v) for v in frozen.values()], 0.0002) if nm.startswith("J3") else None
        for c in sorted(want):
            allres[(nm, c)] = summ(*pack(col[c], T), days)
        if col2:
            for c, parts in col2.items(): allres[("J3+2bps", c)] = summ(*pack(parts, T), days)
    for key, cfc in frozen.items():
        c = tuple(cfc); base = (c[0], 0, c[2], c[3]); ok = True
        lines.append(f"\n{key}: {name(c)}")
        for nm, _, _, _ in sets:
            s = allres[(nm, c)]; b = allres[(nm, base)]
            lines.append(f"  {nm}: n={s['n']} ({s['perday']:.2f}/day) gross={s['gross']:+.1f}bps netR={s['netR']:+.3f} t={s['t']:+.2f} $10->${s['eq']:.2f} (taken {s['tk']}, dd {s['dd']*100:.0f}%) | C0 baseline: n={b['n']} netR={b['netR']:+.3f} $10->${b['eq']:.2f}")
        s2 = allres[("J3+2bps", c)]; lines.append(f"  J3 with +2 bps per side: netR={s2['netR']:+.3f}")
        j1, j2, j3 = (allres[(nm, c)] for nm, *_ in sets)
        passed = (j3["n"] >= 300 and j3["netR"] > 0 and j3["t"] >= 2.0 and j1["n"] >= 30 and j1["netR"] > 0 and j2["n"] >= 30 and j2["netR"] > 0
                  and all(q["eq"] > 10 and q["dd"] < 0.4 for q in (j1, j2, j3)) and s2["netR"] > 0)
        lines.append(f"  PASS: {'YES' if passed else 'no'}")
    txt = "\n".join(lines); print(txt); open(os.path.join(HERE, "r11_judge_out.txt"), "w").write(txt + "\n")


if __name__ == "__main__":
    {"test": test, "select": select, "judge": judge}[sys.argv[1]]()
