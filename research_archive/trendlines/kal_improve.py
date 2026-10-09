"""Can the Kalman trend (trendlines/tl_setups.py KALMAN1) be improved with our data, honestly?
Every period has been looked at already, so improvements are judged WALK-FORWARD: for each test year Y (2022 .. 2026) the variant with
the best day-clustered t on all trades BEFORE Y is chosen and then traded in Y. The stitched test years are the honest estimate.
Variants (declared before running, 72): Kalman smoothness lam 3e-5 / 1e-4 / 3e-4 x entry z0 0.5 / 1 / 2 x exit (z < 0 | 4-ATR trail)
x direction (both | long only | only with BTC 1D trend | only with the coin's 1D trend). Common: 4H, entry next open (market),
3-ATR protective stop from the signal close, 120 bars max, 14 bps + 0.25 bp funding per bar, one trade per coin and side.
Data: OKX 1H d6 + d5 + d4 joined (2019-12 .. 2026-09) -> 4H, all coins, both price orientations."""
import os, sys, pickle, math, collections, time, calendar, itertools
os.environ["TL_WITH_D6"] = "1"
import numpy as np
from multiprocessing import Pool
import tl_core as T
sys.path.insert(1, os.path.join(os.environ.get("REPO", "/home/user/Bybit-dashbored"), "research_archive", "scenario_research"))
from prereg_run import tstat, equity
FEE = 14e-4; FUND_BAR = 0.25e-4; MINSTOP = 0.005; HOLD = 120
LAMS = (3e-5, 1e-4, 3e-4); Z0S = (0.5, 1.0, 2.0); EXITS = ("z", "trail"); DIRS = ("both", "long", "btc", "coin1d")
BASE = (1e-4, 1.0, "z", "both")
year = lambda ms_: time.gmtime(ms_ / 1000).tm_year

def btc_map(inv):
    t, o, h, l, c, v = T.load4h("BTC", inv); return dict(zip(t.tolist(), T.daily_trend(t, c).tolist()))

def sim(o, h, l, c, a, z, e, stop, mode):
    n = len(c); ep = o[e]
    if ep - stop < MINSTOP * abs(ep): stop = ep - MINSTOP * abs(ep)
    risk = ep - stop; st = stop; hh = ep; a0 = a[e - 1]
    for j in range(e, min(n, e + HOLD)):
        if l[j] <= st: return (st - ep) / risk, risk / abs(ep), j - e + 1, j
        if mode == "z" and z[j] < 0 and j + 1 < n: return (o[j + 1] - ep) / risk, risk / abs(ep), j - e + 2, j + 1
        if mode == "trail": hh = max(hh, h[j]); st = max(st, hh - 4.0 * a0)
    j = min(n - 1, e + HOLD - 1); return (c[j] - ep) / risk, risk / abs(ep), j - e + 1, j

def series(args):
    sym, inv, btc = args
    d = T.load4h(sym, inv)
    if d is None or len(d[0]) < 800: return []
    t, o, h, l, c, v = d; a = T.atr14(h, l, c); tr1d = T.daily_trend(t, c); b1d = np.array([btc.get(int(x), 0) for x in t])
    ap = a / np.abs(c); volreg = np.array([ap[i] / ap[max(0, i - 180):i + 1].mean() for i in range(len(c))])   # ATR% vs its 30-day mean
    out = []
    for lam in LAMS:
        z = T.kalman_trend(c, lam=lam)
        for z0 in Z0S:
            for i in range(201, len(c) - 2):
                if not (z[i] > z0 and z[i - 1] <= z0): continue
                e = i + 1; stop = c[i] - 3.0 * a[i]
                for x in EXITS:
                    g, rp, bars, j = sim(o, h, l, c, a, z, e, stop, x)
                    out.append(dict(lam=lam, z0=z0, x=x, sym=sym, side="S" if inv else "L", t=int(t[i]), te=int(t[e]), tx=int(t[j]) + T.H4,
                                    R=g - (FEE + FUND_BAR * bars) / rp, Rg=g, tr=int(tr1d[i]), btc=int(b1d[i]), vr=float(volreg[i]), bars=bars))
    return out

def dir_ok(r, d):
    return d == "both" or (d == "long" and r["side"] == "L") or (d == "btc" and r["btc"] == 1) or (d == "coin1d" and r["tr"] == 1)

def pick(G, var, ylo=None, yhi=None):
    lam, z0, x, d = var
    rows = [r for r in G[(lam, z0, x)] if dir_ok(r, d) and (ylo is None or year(r["t"]) >= ylo) and (yhi is None or year(r["t"]) <= yhi)]
    rows.sort(key=lambda r: r["te"]); last = {}; keep = []
    for r in rows:
        k = (r["sym"], r["side"])
        if r["te"] < last.get(k, 0): continue
        last[k] = r["tx"]; keep.append(r)
    return keep

def st_(x): return tstat([(r["t"], r["R"]) for r in x]) if len(x) >= 2 else (len(x), 0.0, 0.0)
def eqs(x, risk, mx=3):
    eq, tk, dd = equity([(r["te"], r["sym"], r["R"], r["tx"]) for r in x], risk=risk, maxpos=mx); return f"${eq:,.2f} ({tk} trades, DD {dd:.0f}%)"
def vname(v): return f"lam={v[0]:.0e} z0={v[1]} exit={v[2]:5s} dir={v[3]}"

if __name__ == "__main__":
    syms = sorted(f.split("_")[0] for f in os.listdir("d4") if f.endswith("_1H.json"))
    bL, bS = btc_map(False), btc_map(True)
    with Pool(4) as p: parts = p.map(series, [(s, inv, bS if inv else bL) for s in syms for inv in (False, True)])
    G = collections.defaultdict(list)
    for P_ in parts:
        for r in P_: G[(r["lam"], r["z0"], r["x"])].append(r)
    pickle.dump(dict(G), open("kal_trades.pkl", "wb"))
    YEARS = list(range(2020, 2027))
    base = pick(G, BASE)
    a0_, a1_ = (calendar.timegm(time.strptime(d_, "%Y-%m-%d")) * 1000 for d_ in ("2020-01-01", "2021-06-01"))
    b20 = [r for r in pick(G, BASE) if a0_ <= r["t"] < a1_]
    print(f"parity: baseline on 2020-01..2021-05 n={len(b20)} avg {np.mean([r['R'] for r in b20]):+.3f}R (pre-registered run: n=943, +0.462R)")

    print("\n=== TRUTH 1 - the baseline Kalman trend, taken apart by year (avg net R per trade, trades) ===")
    slices = [("all", lambda r: True), ("long", lambda r: r["side"] == "L"), ("short", lambda r: r["side"] == "S"),
              ("with BTC 1D trend", lambda r: r["btc"] == 1), ("against BTC 1D trend", lambda r: r["btc"] == -1), ("BTC flat", lambda r: r["btc"] == 0),
              ("with coin 1D trend", lambda r: r["tr"] == 1), ("against coin 1D trend", lambda r: r["tr"] == -1),
              ("high volatility (ATR% > 1.2x avg)", lambda r: r["vr"] > 1.2), ("low volatility (< 0.8x)", lambda r: r["vr"] < 0.8),
              ("BTC + ETH only", lambda r: r["sym"] in ("BTC", "ETH"))]
    print(f"  {'slice':34s} " + " ".join(f"{y:>13d}" for y in YEARS) + f" {'all years':>14s}")
    for nm, f in slices:
        x = [r for r in base if f(r)]
        cells = []
        for y in YEARS:
            v = [r["R"] for r in x if year(r["t"]) == y]; cells.append(f"{np.mean(v):+.3f} ({len(v):4d})" if v else f"{'-':>13s}")
        print(f"  {nm:34s} " + " ".join(f"{c_:>13s}" for c_ in cells) + f" {np.mean([r['R'] for r in x]):+.3f} ({len(x)})")
    win = [r for r in base if r["R"] > 0]; los = [r for r in base if r["R"] <= 0]
    print(f"  winners {len(win)/len(base)*100:.0f}% avg {np.mean([r['R'] for r in win]):+.2f}R held {np.mean([r['bars'] for r in win]):.0f} bars | "
          f"losers avg {np.mean([r['R'] for r in los]):+.2f}R held {np.mean([r['bars'] for r in los]):.0f} bars | "
          f"top 10% of trades make {sum(sorted([r['R'] for r in base])[-len(base)//10:]) / max(1e-9, sum(r['R'] for r in base)) * 100:.0f}% of the total R")

    print("\n=== the 72 variants, avg net R per trade by year (DESCRIPTIVE, in-sample; the honest test is the walk-forward below) ===")
    VARS = list(itertools.product(LAMS, Z0S, EXITS, DIRS)); res = {}
    for v in VARS:
        x = pick(G, v); per = {y: [r["R"] for r in x if year(r["t"]) == y] for y in YEARS}; res[v] = (x, per)
    ranked = sorted(VARS, key=lambda v: -st_(res[v][0])[2])
    for v in ranked[:12] + [BASE] + ranked[-3:]:
        x, per = res[v]; s = st_(x); pos = sum(1 for y in YEARS if per[y] and np.mean(per[y]) > 0)
        print(f"  {vname(v):44s} all {s[1]:+.3f}R t={s[2]:+.1f} n={s[0]:5d} | years positive {pos}/{len(YEARS)} | "
              + " ".join(f"{y % 100:02d}:{np.mean(per[y]):+.2f}" if per[y] else f"{y % 100:02d}:  -  " for y in YEARS) + ("   <- baseline" if v == BASE else ""))
    better = sum(1 for v in VARS if sum(1 for y in YEARS if res[v][1][y] and res[BASE][1][y] and np.mean(res[v][1][y]) > np.mean(res[BASE][1][y])) >= 5)
    print(f"  variants that beat the baseline in at least 5 of 7 years: {better} of {len(VARS)}")

    print("\n=== WALK-FORWARD: each test year uses the variant with the best t on all EARLIER years (min 150 trades) ===")
    wf = []; bl = []
    for Y in range(2022, 2027):
        cands = []
        for v in VARS:
            prior = [r for r in res[v][0] if year(r["t"]) < Y]
            if len(prior) >= 150: cands.append((st_(prior)[2], v))
        tbest, vbest = max(cands)
        test = [r for r in res[vbest][0] if year(r["t"]) == Y]; btest = [r for r in res[BASE][0] if year(r["t"]) == Y]
        wf += test; bl += btest
        print(f"  {Y}: chosen {vname(vbest)} (prior t={tbest:+.1f}) -> {np.mean([r['R'] for r in test]):+.3f}R n={len(test)} | baseline {np.mean([r['R'] for r in btest]):+.3f}R n={len(btest)}")
    for lab, x in (("walk-forward (chosen each year)", wf), ("baseline (fixed KALMAN1)", bl)):
        s = st_(x); x = sorted(x, key=lambda r: r["te"])
        print(f"  {lab:32s} 2022-2026: n={s[0]} avg {s[1]:+.3f}R t={s[2]:+.1f} | $10 at 1% (max 3 open) -> {eqs(x, 0.01)} | "
              f"at 0.5% (max 8 open) -> {eqs(x, 0.005, 8)} | at 2% (max 3) -> {eqs(x, 0.02)}")

    print("\n=== portfolio settings for the baseline over 2020-2026 (same trades, different position limits) ===")
    allb = sorted(base, key=lambda r: r["te"])
    for risk, mx in ((0.01, 3), (0.005, 6), (0.005, 8), (0.0033, 12), (0.02, 3)):
        print(f"  risk {risk*100:.2f}% per trade, max {mx:2d} open: $10 -> {eqs(allb, risk, mx)}")
