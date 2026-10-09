"""Resonance rebuild with LIMIT orders (lx_core.py execution model). Modes:
  MKT    market entry at the next open, market take-profit (14 bps round trip: the earlier tests)
  LMT0   limit at the signal close, LMT25 at close - 0.25 ATR, LMT50 at close - 0.5 ATR; each valid 4 bars; limit take-profits (maker)
Same structural stop price (min 0.3% from the actual entry), targets in R from the actual entry. 54 configs x 4 modes = 216.
RES_BASE=15m: s15 (design 12 coins, dev 50% / val 25% / final 25%, unseen 12 coins).
RES_BASE=1H : d4 (scen_an split: design 19 / unseen 13; dev < 2025-07-11 <= val < 2026-02-19 <= final) + d5 (2021-24) second holdout.
Selection on DEV only (best agree/ADX per trigger x exit x mode, n >= 60), one look at FINAL + UNSEEN (+ d5 for 1H)."""
import os, sys, json, pickle, math, collections, time, calendar
import numpy as np
from multiprocessing import Pool
REPO = os.environ.get("REPO", "/home/user/Bybit-dashbored")
for _d in ("resonance_test", "scenario_research", "limit_orders"): sys.path.insert(1, f"{REPO}/research_archive/{_d}")
import res
from lx_core import sim_exec, limit_fill, HOLD
from prereg_run import tstat, equity
FUND = 0.5e-4; MODES = {"MKT": None, "LMT0": 0.0, "LMT25": 0.25, "LMT50": 0.5}; EXITS = ("L", "S1", "S2"); LIM_BARS = 4
XN = {"L": "ladder", "S1": "1.5R", "S2": "2.25R"}
ms = lambda d: calendar.timegm(time.strptime(d, "%Y-%m-%d")) * 1000

def worker(args):
    sym, inv, folder = args
    res.FOLDER = folder
    t, o, h, l, c, v = res.load(sym, inv); n = len(t)
    if n < 2000: return []
    F = res.features(t, o, h, l, c, v); rows = []; hr = res.BAR / res.HR
    for i in range(1, n - 2):
        if not F["ready"][i] or not (F["choch"][i] or F["emax"][i]): continue
        a = F["atr"][i]; sb = F["stop_base"][i]; modes = {}
        for m, k in MODES.items():
            if k is None:
                e = i + 1; ep = o[e]; lim = False
            else:
                ep = c[i] - k * a; st0 = min(sb, ep - res.MINSTOP * abs(ep)); e = limit_fill(l, c, i, ep, st0, a, LIM_BARS); lim = True
                if e is None: modes[m] = None; continue
            st = min(sb, ep - res.MINSTOP * abs(ep)); risk = ep - st; outs = []
            for x in EXITS:
                g, fee, j = sim_exec(h, l, c, e, ep, st, risk, x, lim, lim, a)
                outs.append((g, fee, (j - e + 1) * hr, int(t[e]), int(t[j]) + res.BAR))
            modes[m] = (risk / abs(ep), outs)
        base = dict(sym=sym, side="S" if inv else "L", t=int(t[i]), res=int(F["res"][i]), adx=float(F["adx"][i]), modes=modes)
        if F["choch"][i]: rows.append(dict(base, kind="choch"))
        if F["emax"][i]: rows.append(dict(base, kind="ema"))
    return rows

def build(folder):
    syms = sorted(f.split("_")[0] for f in os.listdir(folder) if f.endswith(f"_{res.SUFFIX}.json") and len(json.load(open(f"{folder}/{f}"))) >= 2000)
    with Pool(4) as p: parts = p.map(worker, [(s, inv, folder) for s in syms for inv in (False, True)])
    return [r for P_ in parts for r in P_]

def dedupe(rows):
    rows.sort(key=lambda r: r["te"]); last = {}; out = []
    for r in rows:
        k = (r["sym"], r["side"])
        if r["te"] < last.get(k, 0): continue
        last[k] = r["tx"]; out.append(r)
    return out

def trades(rows, cfg, segf, gross=False):
    trig, thr, adxm, x, m = cfg; xi = EXITS.index(x); out = []
    for r in rows:
        if r["kind"] != trig or r["res"] < thr or r["adx"] < adxm or not segf(r): continue
        mm = r["modes"].get(m)
        if mm is None: continue
        rp, outs = mm; g, fee, hrs, te, tx = outs[xi]
        out.append(dict(t=r["t"], te=te, tx=tx, sym=r["sym"], side=r["side"], fee_r=fee / rp,
                        R=g - FUND * hrs / 8 / rp - (0 if gross else fee / rp)))
    return dedupe(out)

def st_(x): return tstat([(r["t"], r["R"]) for r in x]) if len(x) >= 2 else (len(x), 0.0, 0.0)
def eqs(x, risk): eq, tk, dd = equity([(r["te"], r["sym"], r["R"], r["tx"]) for r in x], risk=risk); return f"${eq:.2f} ({tk} trades, DD {dd:.0f}%)"
def winpf(x):
    if not x: return "-"
    gp = sum(r["R"] for r in x if r["R"] > 0); gl = -sum(r["R"] for r in x if r["R"] < 0)
    return f"win {sum(r['R'] > 0 for r in x)/len(x)*100:.0f}% PF {gp/max(gl,1e-9):.2f}"
def quarters(x, a, b):
    qs = [a + (b - a) * k / 4 for k in range(5)]; out = []
    for lo, hi in zip(qs, qs[1:]):
        v = [r["R"] for r in x if lo <= r["t"] < hi]; out.append(sum(v) / len(v) if v else float("nan"))
    return out
def name(c): return f"{c[0]:5s} agree>={c[1]} adx>={c[2]:2d} {XN[c[3]]:6s} {c[4]:5s}"

if __name__ == "__main__":
    if res.BASE == "1H":
        DES = set("AAVE AVAX BNB BTC CRV DOGE FIL HBAR ICP NEAR OP POL SOL STX SUI TRX UNI WLD XRP".split())
        T1, T2 = ms("2025-07-11"), ms("2026-02-19"); t0, t1 = ms("2024-04-09"), ms("2026-09-30"); rows = build("d4"); hold = build("d5")
    else:
        DES = set("BTC ETH SOL XRP DOGE BNB ADA AVAX LINK DOT LTC NEAR".split())
        _b = json.load(open("s15/BTC_15m.json")); t0, t1 = _b[0][0], _b[-1][0]; T1 = t0 + (t1 - t0) * .5; T2 = t0 + (t1 - t0) * .75
        rows = build("s15"); hold = None
    SEG = {"dev": lambda r: r["sym"] in DES and r["t"] < T1, "val": lambda r: r["sym"] in DES and T1 <= r["t"] < T2,
           "final": lambda r: r["sym"] in DES and r["t"] >= T2, "unseen": lambda r: r["sym"] not in DES, "all": lambda r: True}
    grid = [(tg, th, ad, x, m) for tg in ("choch", "ema") for th in (3, 4, 5) for ad in (0, 20, 25) for x in EXITS for m in MODES]
    print(f"=== resonance with limit orders, base {res.BASE}: {len(grid)} configs (54 x {len(MODES)} execution modes) ===")
    print("all coins, whole period, per execution mode: fill rate | avg fee per trade | configs positive after fees (of 54) | avg net R over the 54")
    for m in MODES:
        sig = [r for r in rows]; filled = sum(1 for r in sig if r["modes"].get(m) is not None) / max(1, len(sig))
        cf = [c for c in grid if c[4] == m]; nets = [st_(trades(rows, c, SEG["all"]))[1] for c in cf]
        fees = [x["fee_r"] for c in cf[:9] for x in trades(rows, c, SEG["all"])]
        print(f"  {m:5s} fill {filled*100:5.1f}% | fee {np.mean(fees):.3f}R | positive {sum(1 for z in nets if z > 0):2d}/54 | avg {np.mean(nets):+.3f}R")
    dev = {c: st_(trades(rows, c, SEG["dev"])) for c in grid}
    print(f"chance check: DEV t>=3: {sum(1 for s in dev.values() if s[0] >= 60 and s[2] >= 3)} (luck ~{0.00135*len(grid):.2f}); "
          f"t>=2: {sum(1 for s in dev.values() if s[0] >= 60 and s[2] >= 2)} (luck ~{0.0228*len(grid):.1f})")
    frozen = []
    for tg in ("choch", "ema"):
        for x in EXITS:
            for m in MODES:
                cand = [(c, s) for c, s in dev.items() if c[0] == tg and c[3] == x and c[4] == m and s[0] >= 60]
                if cand: frozen.append(max(cand, key=lambda z: z[1][2]))
    print(f"\n{'frozen on DEV (best agree/adx per trigger x exit x mode)':44s} DEV            | VAL            | FINAL                 | UNSEEN coins          | verdict")
    passed = []
    for c, s in frozen:
        v = st_(trades(rows, c, SEG["val"])); f = trades(rows, c, SEG["final"]); u = trades(rows, c, SEG["unseen"]); fs = st_(f); us = st_(u)
        q = quarters(u, t0, t1); ok = fs[0] >= 100 and fs[1] >= 0.10 and us[1] > 0 and sum(1 for z in q if z == z and z > 0) >= 3
        if ok: passed.append(c)
        print(f"  {name(c):42s} {s[1]:+.3f} t={s[2]:+.1f} | {v[1]:+.3f} t={v[2]:+.1f} | {fs[1]:+.3f} t={fs[2]:+.1f} n={fs[0]:4d} | {us[1]:+.3f} t={us[2]:+.1f} n={us[0]:4d} | {'PASS' if ok else 'fail'}"
              f" | $10 1%: {eqs(f + u, 0.01)}")
    if hold is not None:
        print("\n=== second holdout 2021-24 (d5), all coins, the same frozen configs; pass = avg > 0 and t >= 2 ===")
        for c, s in frozen:
            x = trades(hold, c, SEG["all"]); xs = st_(x); g = st_(trades(hold, c, SEG["all"], gross=True))
            print(f"  {name(c):42s} {xs[1]:+.3f}R t={xs[2]:+.1f} n={xs[0]:5d} (before fees {g[1]:+.3f}) | {winpf(x)} | {'PASS' if xs[1] > 0 and xs[2] >= 2 else 'fail'}"
                  f" | $10 at 1% -> {eqs(x, 0.01)} | at 2% -> {eqs(x, 0.02)}")
    print(f"\nPASSED (final + unseen bar): {passed if passed else 'none'}")
    pickle.dump(dict(frozen=frozen, passed=passed), open(f"lx_res_{res.BASE}.pkl", "wb"))
