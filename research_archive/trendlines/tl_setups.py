"""Part 3 of the trend-line study: setups written down from the DEV findings (tl_study_out.txt) BEFORE any VAL / FINAL / UNSEEN data
was looked at. 4H, long logic on both price orientations (so every setup trades both directions). Market entry at the next open,
14 bps + 0.25 bp funding per 4H bar. Stop-first inside a bar. One open trade per coin and side.
  CHOCH   close crosses above the last confirmed swing high (DEV: 91% right side, +0.39 ATR / 24 bars)
  STRONG  break of a resistance line with 3+ touches or 2+ tests (DEV: +0.7 .. +1.0 ATR / 24 bars, rare)
  BOUNCE  test of an uptrend line closing as a rejection candle (DEV: held 55%, +0.43 ATR / 24 bars)
  KALMAN  noise-free trend: Kalman slope z crosses above z0, exit when z < 0
  OPTBRK  close above the optimized no-violation resistance line (window 48)
  RETEST  after a resistance-line break, the first pull-back to the line that closes back above it
  SPRING  close below the uptrend line, then a close back above it within 3 bars (false break)
Exits: 2R / 3R fixed (60 bars max) or a 3-ATR chandelier trail (120 bars max). Filters: 1D trend any / with (and BTC for BOUNCE).
Selection on DEV (19 design coins, before 2024) only; then VAL (2024), FINAL (2025-26) and UNSEEN coins (all years)."""
import os, sys, pickle, math, collections, time, calendar
import numpy as np
from multiprocessing import Pool
import tl_core as T
sys.path.insert(1, os.path.join(os.environ.get("REPO", "/home/user/Bybit-dashbored"), "research_archive", "scenario_research"))
from prereg_run import tstat, equity
ms = lambda d: calendar.timegm(time.strptime(d, "%Y-%m-%d")) * 1000
DES = set("AAVE AVAX BNB BTC CRV DOGE FIL HBAR ICP NEAR OP POL SOL STX SUI TRX UNI WLD XRP".split())
DEV_END, VAL_END = ms("2024-01-01"), ms("2025-01-01")
FEE = 14e-4; FUND_BAR = 0.5e-4 / 2          # funding 0.5 bp per 8h = 0.25 bp per 4H bar
MINSTOP = 0.005

def sim(S, e, stop, mode, kal_exit=False):
    """long from the open of bar e. mode: 2 / 3 (R target) or 'trail'. returns (gross R, risk %, bars held, exit index)"""
    o, h, l, c, a = S["o"], S["h"], S["l"], S["c"], S["F"]["atr"]; n = len(c); ep = o[e]
    if ep - stop < MINSTOP * abs(ep): stop = ep - MINSTOP * abs(ep)
    risk = ep - stop; st = stop; hold = 120 if mode == "trail" or kal_exit else 60; hh = ep; a0 = a[e - 1]
    tp = ep + mode * risk if isinstance(mode, int) else None
    for j in range(e, min(n, e + hold)):
        if l[j] <= st: return (st - ep) / risk, risk / abs(ep), j - e + 1, j
        if tp is not None and h[j] >= tp: return float(mode), risk / abs(ep), j - e + 1, j
        if mode == "trail": hh = max(hh, h[j]); st = max(st, hh - 3.0 * a0)
        if kal_exit and S["F"]["kal"][j] < 0 and j + 1 < n: return (o[j + 1] - ep) / risk, risk / abs(ep), j - e + 2, j + 1
    j = min(n - 1, e + hold - 1); return (c[j] - ep) / risk, risk / abs(ep), j - e + 1, j

def signals(S):
    """(setup, i, stop price, extra tags) for every signal bar i (entry at i + 1)"""
    F = S["F"]; o, h, l, c = S["o"], S["h"], S["l"], S["c"]; a = F["atr"]; n = len(c); out = []
    highs = [(ci, p) for k, i_, p, ci in S["sw"] if k == "H"]; lows = [(ci, p) for k, i_, p, ci in S["sw"] if k == "L"]
    lastH = np.full(n, np.nan); lastL = np.full(n, np.nan); jh = jl = 0; ch = cl = np.nan
    for i in range(n):
        while jh < len(highs) and highs[jh][0] <= i: ch = highs[jh][1]; jh += 1
        while jl < len(lows) and lows[jl][0] <= i: cl = lows[jl][1]; jl += 1
        lastH[i] = ch; lastL[i] = cl
    pend_retest = None; pend_spring = None
    for i in range(201, n - 2):
        ai = a[i]
        if c[i] > lastH[i - 1] and c[i - 1] <= lastH[i - 1]:
            out.append(("CHOCH", i, lastL[i] - 0.2 * ai if not np.isnan(lastL[i]) else c[i] - 2 * ai))
        if F["res_break"][i]:
            if F["res_touches"][i] >= 3 or F["res_tests"][i] >= 2: out.append(("STRONG", i, c[i] - 2.0 * ai))
            pend_retest = (i, F["res_y"][i], F["res_raw"][i])
        if pend_retest is not None and i > pend_retest[0]:
            i0, y0, s0 = pend_retest; yy = y0 + s0 * (i - i0)
            if i - i0 > 12: pend_retest = None
            elif l[i] <= yy + 0.1 * ai and c[i] > yy:
                out.append(("RETEST", i, yy - 1.0 * ai)); pend_retest = None
        if F["sup_test"][i] and c[i] > o[i] and c[i] > F["sup_y"][i]:
            out.append(("BOUNCE", i, min(l[i], F["sup_y"][i]) - 0.3 * ai))
        if F["sup_break"][i]: pend_spring = (i, F["sup_y"][i], F["sup_raw"][i])
        if pend_spring is not None and i > pend_spring[0]:
            i0, y0, s0 = pend_spring
            if i - i0 > 3: pend_spring = None
            elif c[i] > y0 + s0 * (i - i0):
                out.append(("SPRING", i, l[i0:i + 1].min() - 0.2 * ai)); pend_spring = None
        r48 = F["opt48_res"]
        if c[i] > r48[i] + T.DELTA * ai and c[i - 1] <= r48[i - 1] + T.DELTA * a[i - 1]:
            out.append(("OPTBRK", i, c[i] - 2.0 * ai))
        k = F["kal"]
        for z0 in (0.0, 1.0):
            if k[i] > z0 and k[i - 1] <= z0: out.append((f"KALMAN{z0:g}", i, c[i] - 3.0 * ai))
    return out

EXITS = (2, 3, "trail")
def configs():
    cf = []
    for st in ("CHOCH", "STRONG", "OPTBRK", "RETEST", "SPRING"):
        for x in EXITS:
            for flt in ("any", "with1D"): cf.append((st, x, flt))
    for x in EXITS:
        for flt in ("any", "with1D", "btc"): cf.append(("BOUNCE", x, flt))
    for z in ("KALMAN0", "KALMAN1"):
        for flt in ("any", "with1D"): cf.append((z, "kal", flt))
    return cf

def run_series(S):
    sig = signals(S); rows = []
    for name, i, stop in sig:
        e = i + 1
        if e >= len(S["c"]) - 1: continue
        xs = ("kal",) if name.startswith("KALMAN") else EXITS
        for x in xs:
            # KALMAN: no target (99R), 3-ATR protective stop, exit at the open after the first close with z < 0, 120 bars max
            g, rp, bars, j = sim(S, e, stop, 99, kal_exit=True) if x == "kal" else sim(S, e, stop, x)
            rows.append(dict(setup=name, x=x, sym=S["sym"], side="S" if S["inv"] else "L", t=int(S["t"][i]), te=int(S["t"][e]),
                             tx=int(S["t"][j]) + T.H4, R=g - (FEE + FUND_BAR * bars) / rp, Rg=g, rp=rp, bars=bars,
                             tr=int(S["F"]["tr1d"][i]), btc=int(S["F"]["btc1d"][i])))
    return rows

def seg(r, w):
    if w == "dev": return r["sym"] in DES and r["t"] < DEV_END
    if w == "val": return r["sym"] in DES and DEV_END <= r["t"] < VAL_END
    if w == "final": return r["sym"] in DES and r["t"] >= VAL_END
    if w == "unseen": return r["sym"] not in DES
    return True

def pick(R, cfg, w):
    st, x, flt = cfg; out = []
    for r in R:
        if r["setup"] != st or r["x"] != x or not seg(r, w): continue
        if flt == "with1D" and r["tr"] != 1: continue
        if flt == "btc" and r["btc"] != 1: continue
        out.append(r)
    out.sort(key=lambda r: r["te"]); last = {}; keep = []
    for r in out:
        k = (r["sym"], r["side"])
        if r["te"] < last.get(k, 0): continue
        last[k] = r["tx"]; keep.append(r)
    return keep

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

if __name__ == "__main__":
    S_all = pickle.load(open("tl_feat.pkl", "rb"))
    with Pool(4) as p: parts = p.map(run_series, S_all)
    R = [r for P_ in parts for r in P_]; pickle.dump(R, open("tl_trades.pkl", "wb"))
    CF = configs(); print(f"trades simulated: {len(R)}; configurations: {len(CF)}")
    print("\n=== every configuration on DEV (design coins, 2021-06 .. 2023-12): avg R after fees, before fees, t, trades ===")
    dev = {}
    for cf in CF:
        x = pick(R, cf, "dev"); s = st_(x); g = np.mean([r["Rg"] for r in x]) if x else 0; dev[cf] = s
        print(f"  {cf[0]:8s} {str(cf[1]):6s} {cf[2]:7s} net {s[1]:+.3f}R (gross {g:+.3f}) t={s[2]:+.1f} n={s[0]:5d} | {winpf(x)} | med stop {np.median([r['rp'] for r in x])*100 if x else 0:.1f}%")
    print(f"chance check: DEV t>=3: {sum(1 for s in dev.values() if s[0] >= 60 and s[2] >= 3)} (luck ~{0.00135*len(CF):.2f}); "
          f"t>=2: {sum(1 for s in dev.values() if s[0] >= 60 and s[2] >= 2)} (luck ~{0.0228*len(CF):.1f})")
    frozen = []
    for st in ("CHOCH", "STRONG", "OPTBRK", "RETEST", "SPRING", "BOUNCE", "KALMAN0", "KALMAN1"):
        cand = [(cf, s) for cf, s in dev.items() if cf[0] == st and s[0] >= 60]
        if cand: frozen.append(max(cand, key=lambda z: z[1][2]))
    t0, t1 = ms("2021-06-01"), ms("2026-10-01")
    print(f"\n=== frozen DEV-best per setup -> VAL 2024 -> ONE LOOK at FINAL 2025-26 and UNSEEN coins (pass: FINAL n>=100, avg>=+0.10R, UNSEEN>0, 3/4 qtrs>0) ===")
    passed = []
    for cf, s in frozen:
        v = st_(pick(R, cf, "val")); f = pick(R, cf, "final"); u = pick(R, cf, "unseen"); fs = st_(f); us = st_(u); q = quarters(u, t0, t1)
        ok = fs[0] >= 100 and fs[1] >= 0.10 and us[1] > 0 and sum(1 for z in q if z == z and z > 0) >= 3
        if ok: passed.append(cf)
        print(f"  {cf[0]:8s} {str(cf[1]):6s} {cf[2]:7s} DEV {s[1]:+.3f} t={s[2]:+.1f} | VAL {v[1]:+.3f} t={v[2]:+.1f} n={v[0]} | FINAL {fs[1]:+.3f} t={fs[2]:+.1f} n={fs[0]} | "
              f"UNSEEN {us[1]:+.3f} t={us[2]:+.1f} n={us[0]} qtrs {' '.join(f'{z:+.2f}' for z in q)} | {'PASS' if ok else 'fail'}")
        print(f"        {winpf(f + u)} | $10 FINAL+UNSEEN at 1% -> {eqs(f + u, 0.01)} | at 2% -> {eqs(f + u, 0.02)}")
    print(f"\nPASSED: {passed if passed else 'none'}")
    pickle.dump(dict(frozen=frozen, passed=passed), open("tl_sel.pkl", "wb"))
