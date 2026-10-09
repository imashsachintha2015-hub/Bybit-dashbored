"""Improving the long + short Kalman system. Candidates were written down (in this docstring) before running; each comes from an
earlier finding. Judged by portfolio growth of $10 per calendar year (0.5% risk per trade, max 8 open, one position per coin, real
funding, 14 bps) and by a factor walk-forward: in year Y a candidate is switched on only if it beat the base over all earlier years.
BASE        Kalman z crosses above +1 (long) / below -1 (short), BTC 1D trend in the same direction, 3-ATR stop, exit when z crosses 0
SHORT_BEAR  shorts only when BTC's daily close is below its 200-day EMA (a real bear market)
SHORT_Z2    shorts need z below -2
SHORT_FUND  shorts only when the last 9 funding payments average > 0 (crowded longs pay the shorts)
SHORT_HALF  shorts at half risk
SHORT_QUICK shorts exit when z rises above -0.5 (down-legs are shorter)
LONG_PATH   longs only when less volume (last 180 bars) sits in the 3 ATR above price than in the 3 ATR below
NEWTON2     add the Newton (acceleration) engine + BTC filter as a second engine, both sides
LONG_LT     longs only when BTC's daily close is above its 200-day EMA
LONG_ONLY   no shorts at all"""
import os, sys, pickle, math, collections, time, calendar
os.environ["TL_WITH_D6"] = "1"
import numpy as np
from multiprocessing import Pool
import tl_core as T, inv_core as I, funding as FU
sys.path.insert(1, os.path.join(os.environ.get("REPO", "/home/user/Bybit-dashbored"), "research_archive", "scenario_research"))
from prereg_run import equity
FEE = 14e-4; MINSTOP = 0.005; HOLD = 120; DAY = 86400000
CANDS = ["SHORT_BEAR", "SHORT_Z2", "SHORT_FUND", "SHORT_HALF", "SHORT_QUICK", "LONG_PATH", "NEWTON2", "LONG_LT", "LONG_ONLY"]
YEARS = list(range(2020, 2027))
year = lambda ms_: time.gmtime(ms_ / 1000).tm_year

def btc_tags():
    t, o, h, l, c, v = T.load4h("BTC", False)
    day = t // DAY; ud, st, cnt = np.unique(day, return_index=True, return_counts=True); keep = cnt == 6; ud, st = ud[keep], st[keep]
    C = c[st + 5]; e200 = T.ema(C, 200); above = C > e200; above[:200] = False; below = C < e200; below[:200] = False
    idx = np.searchsorted((ud + 1) * DAY, t + T.H4, side="right") - 1
    tr = T.daily_trend(t, c)
    mk = lambda arr: dict(zip(t.tolist(), [bool(arr[i]) if i >= 0 else False for i in idx]))
    return dict(tr=dict(zip(t.tolist(), tr.tolist())), above=mk(above), below=mk(below))

def sim(o, h, l, c, e, stop, zx, thr):
    n = len(c); ep = o[e]
    if ep - stop < MINSTOP * abs(ep): stop = ep - MINSTOP * abs(ep)
    risk = ep - stop
    for j in range(e, min(n, e + HOLD)):
        if l[j] <= stop: return -1.0, risk / abs(ep), j
        if zx[j] < thr and j + 1 < n: return (o[j + 1] - ep) / risk, risk / abs(ep), j + 1
    j = min(n - 1, e + HOLD - 1); return (c[j] - ep) / risk, risk / abs(ep), j

def series(args):
    sym, inv, B = args
    d = T.load4h(sym, inv)
    if d is None or len(d[0]) < 800: return []
    t, o, h, l, c, v = d; a = T.atr14(h, l, c); z = T.kalman_trend(c); vz, az = I.newton(c)
    side = "S" if inv else "L"; ft, fcum = FU.series(sym); out = []
    for i in range(201, len(c) - 2):
        ti = int(t[i]); btc_tr = B["tr"].get(ti, 0) * (-1 if inv else 1)
        lt = B["below"].get(ti, False) if inv else B["above"].get(ti, False)
        fund_pos = None
        if ft:
            b = int(np.searchsorted(ft, ti + T.H4, side="right"))
            if b >= 9: fund_pos = (fcum[b] - fcum[b - 9]) / 9 > 0
        ents = []
        if z[i] > 1 and z[i - 1] <= 1: ents.append("KAL1")
        if z[i] > 2 and z[i - 1] <= 2: ents.append("KAL2")
        if vz[i] > 1 and vz[i - 1] <= 1 and az[i] > 0: ents.append("NEW")
        for en in ents:
            stop = c[i] - 3 * a[i]; zx = vz if en == "NEW" else z
            outs = {"std": sim(o, h, l, c, i + 1, stop, zx, 0.0)}
            if inv and en != "NEW": outs["quick"] = sim(o, h, l, c, i + 1, stop, zx, 0.5)
            res = {}
            for k, (g, rp, j) in outs.items():
                te, tx = int(t[i + 1]), int(t[j]) + T.H4; f, _ = FU.paid(sym, side, te, tx)
                res[k] = (g - FEE / rp - f / rp, te, tx)
            out.append(dict(en=en, sym=sym, side=side, t=ti, btc=btc_tr, lt=lt, fund=fund_pos,
                            path=I.barrier_lighter(h, l, c, v, a, i) if (en == "KAL1" and not inv) else None, res=res))
    return out

def trades(E, on):
    sel = []
    for r in E:
        if r["btc"] != 1: continue
        S_ = r["side"] == "S"
        if S_ and "LONG_ONLY" in on: continue
        if r["en"] == "NEW":
            if "NEWTON2" not in on: continue
        elif S_:
            if r["en"] != ("KAL2" if "SHORT_Z2" in on else "KAL1"): continue
            if "SHORT_BEAR" in on and not r["lt"]: continue
            if "SHORT_FUND" in on and r["fund"] is not True: continue
        else:
            if r["en"] != "KAL1": continue
            if "LONG_PATH" in on and not r["path"]: continue
            if "LONG_LT" in on and not r["lt"]: continue
        R, te, tx = r["res"]["quick" if (S_ and "SHORT_QUICK" in on and r["en"] != "NEW") else "std"]
        if S_ and "SHORT_HALF" in on: R *= 0.5
        sel.append((te, r["sym"], R, tx, r["t"], r["side"]))
    sel.sort(); last = {}; keep = []
    for te, sym, R, tx, t0, sd in sel:
        if te < last.get(sym, 0): continue
        last[sym] = tx; keep.append((te, sym, R, tx, t0, sd))
    return keep

def growth(tr, y0, y1):
    """$10 -> ? over calendar years y0..y1 (each year restarted from the previous year's end equity)"""
    eq = 10.0; dd_all = 0.0; n = 0
    for y in range(y0, y1 + 1):
        x = [(te, sym, R, tx) for te, sym, R, tx, t0, sd in tr if year(t0) == y]
        e, k, dd = equity(x, risk=0.005, maxpos=8); eq *= e / 10.0; dd_all = min(dd_all, dd); n += k
    return eq, n, dd_all

if __name__ == "__main__":
    B = btc_tags(); syms = sorted(f.split("_")[0] for f in os.listdir("d4") if f.endswith("_1H.json"))
    with Pool(4) as p: parts = p.map(series, [(s, inv, B) for s in syms for inv in (False, True)])
    E = [r for P_ in parts for r in P_]; pickle.dump(E, open("ls_entries.pkl", "wb"))
    base = trades(E, set())
    print("=== per-year $10 growth (each year from $10; real funding; 0.5% risk, max 8 open) ===")
    print(f"  {'system':12s} " + " ".join(f"{y:>9d}" for y in YEARS) + "   years better than BASE")
    gb = {y: growth(base, y, y)[0] for y in YEARS}
    print(f"  {'BASE':12s} " + " ".join(f"{gb[y]:9.2f}" for y in YEARS))
    single = {}
    for c_ in CANDS:
        tr = trades(E, {c_}); g = {y: growth(tr, y, y)[0] for y in YEARS}; single[c_] = g
        print(f"  {c_:12s} " + " ".join(f"{g[y]:9.2f}" for y in YEARS) + f"   {sum(1 for y in YEARS if g[y] > gb[y])}/7")
    print("\n=== WALK-FORWARD: in year Y switch on every candidate that beat BASE over all earlier years (product of yearly growth) ===")
    wf_eq = 10.0; base_eq = 10.0; wf_tr = []
    for Y in YEARS[1:]:
        prior = lambda g: np.prod([g[y] / 10.0 for y in YEARS if y < Y])
        on = {c_ for c_ in CANDS if prior(single[c_]) > prior(gb)}
        if "LONG_ONLY" in on: on -= {"SHORT_BEAR", "SHORT_Z2", "SHORT_FUND", "SHORT_HALF", "SHORT_QUICK"}
        tr = trades(E, on); gy = growth(tr, Y, Y)[0]; wf_eq *= gy / 10.0; base_eq *= gb[Y] / 10.0
        wf_tr += [x for x in tr if year(x[4]) == Y]
        print(f"  {Y}: switched on {sorted(on) if on else 'nothing'} -> $10 becomes ${gy:.2f} (BASE ${gb[Y]:.2f})")
    print(f"  2021-2026 compounded: walk-forward system $10 -> ${wf_eq:.2f} | BASE $10 -> ${base_eq:.2f}")
    print("\n=== full-period view 2020-2026 (in-sample, for reference) ===")
    eqc, nc, ddc = equity([(te, sym, R, tx) for te, sym, R, tx, t0, sd in base], risk=0.005, maxpos=8)
    print(f"  parity: BASE compounded continuously 2020-2026 $10 -> ${eqc:.2f} ({nc} trades, DD {ddc:.0f}%) "
          f"[downtrends/kal_funding: $76.53 with one trade per coin AND side]")
    final_on = {c_ for c_ in CANDS if np.prod([single[c_][y] / 10 for y in YEARS]) > np.prod([gb[y] / 10 for y in YEARS])}
    if "LONG_ONLY" in final_on: final_on -= {"SHORT_BEAR", "SHORT_Z2", "SHORT_FUND", "SHORT_HALF", "SHORT_QUICK"}
    for lab, on in (("BASE", set()), (f"BASE + {sorted(final_on)}", final_on)):
        tr = trades(E, on); eq, n, dd = growth(tr, 2020, 2026); eq2, n2, dd2 = growth(tr, 2024, 2026)
        print(f"  {lab}: 2020-2026 $10 -> ${eq:.2f} ({n} trades, worst year drawdown {dd:.0f}%) | 2024-2026 $10 -> ${eq2:.2f} ({n2} trades)")
