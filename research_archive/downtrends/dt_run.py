"""Short-side inventions, exactly as PREREG_downtrend.md (written before this ran). Shorts are simulated as longs on mirrored prices
(the S series of dt_feat.pkl), so 'up' below means 'down' in real prices. Costs: 14 bps + REAL funding (shorts receive positive funding)."""
import os, sys, pickle, math, collections, time, calendar
os.environ["TL_WITH_D6"] = "1"
import numpy as np
from multiprocessing import Pool
import tl_core as T, inv_core as I, funding as FU
sys.path.insert(1, os.path.join(os.environ.get("REPO", "/home/user/Bybit-dashbored"), "research_archive", "scenario_research"))
from prereg_run import tstat, equity
from inv_run import seg, DES, D1
FEE = 14e-4; MINSTOP = 0.005
NAMES = ["KAL_S", "REJECT_LINE", "LOWER_HIGH", "BLOWOFF", "BULL_TRAP", "NEWTON_S", "PATH_S", "WEAK_ALT", "FUNDING"]

def sim(S, e, stop, mode, zx=None):
    o, h, l, c = S["o"], S["h"], S["l"], S["c"]; n = len(c); ep = o[e]
    if ep - stop < MINSTOP * abs(ep): stop = ep - MINSTOP * abs(ep)
    risk = ep - stop; hold = 120 if mode == "z" else 60; tp = ep + 3 * risk if mode == "3R" else None
    for j in range(e, min(n, e + hold)):
        if l[j] <= stop: return -1.0, risk / abs(ep), j
        if tp is not None and h[j] >= tp: return 3.0, risk / abs(ep), j
        if mode == "z" and zx[j] < 0 and j + 1 < n: return (o[j + 1] - ep) / risk, risk / abs(ep), j + 1
    j = min(n - 1, e + hold - 1); return (c[j] - ep) / risk, risk / abs(ep), j

def run(args):
    S, btc_close = args
    F = S["F"]; o, h, l, c, v, t = S["o"], S["h"], S["l"], S["c"], S["v"], S["t"]; a = F["atr"]; z = F["kal"]; n = len(c)
    vz, az = I.newton(c)
    ft, fcum = FU.series(S["sym"])
    lows = [(ci, i_, p) for k, i_, p, ci in S["sw"] if k == "L"]; by_conf = {ci: (i_, p) for ci, i_, p in lows}
    prev_low = None; pend_trap = None; out = []
    for i in range(201, n - 2):
        ent = {}
        kal = z[i] > 1 and z[i - 1] <= 1
        ent["KAL_S"] = (kal, c[i] - 3 * a[i], "z", z)
        if F["sup_test"][i] and c[i] > o[i] and c[i] > F["sup_y"][i]:
            ent["REJECT_LINE"] = (True, min(l[i], F["sup_y"][i]) - 0.3 * a[i], "3R", None)
        if i in by_conf:                                                  # swing low confirmed (mirrored) = real swing high confirmed
            i_, p = by_conf[i]
            if prev_low is not None and p > prev_low and z[i] > 1:
                ent["LOWER_HIGH"] = (True, p - 0.2 * a[i], "z", z)
            prev_low = p
        rng = max(h[i] - l[i], 1e-12)
        if F["relvol"][i] >= 2.5 and (min(o[i], c[i]) - l[i]) / rng >= 0.45 and c[i] >= (h[i] + l[i]) / 2 and c[i] <= c[i - 20] - 4 * a[i]:
            ent["BLOWOFF"] = (True, l[i] - 0.2 * a[i], "3R", None)
        if F["sup_break"][i]: pend_trap = (i, F["sup_y"][i], F["sup_raw"][i])
        if pend_trap is not None and i > pend_trap[0]:
            i0, y0, s0 = pend_trap
            if i - i0 > 3: pend_trap = None
            elif c[i] > y0 + s0 * (i - i0): ent["BULL_TRAP"] = (True, l[i0:i + 1].min() - 0.2 * a[i], "3R", None); pend_trap = None
        if vz[i] > 1 and vz[i - 1] <= 1 and az[i] > 0: ent["NEWTON_S"] = (True, c[i] - 3 * a[i], "z", vz)
        if kal:
            ent["PATH_S"] = (I.barrier_lighter(h, l, c, v, a, i), c[i] - 3 * a[i], "z", z)
            k0 = max(0, i - 180); bt0, bt1 = btc_close.get(int(t[k0])), btc_close.get(int(t[i]))
            if bt0 and bt1 and S["sym"] != "BTC": ent["WEAK_ALT"] = ((-c[i]) / (-c[k0]) < bt1 / bt0, c[i] - 3 * a[i], "z", z)
            if ft:
                b = int(np.searchsorted(ft, t[i] + T.H4, side="right"))
                if b >= 9: ent["FUNDING"] = ((fcum[b] - fcum[b - 9]) / 9 > 0, c[i] - 3 * a[i], "z", z)
        for nm, (ok, stop, mode, zx) in ent.items():
            if not ok: continue
            g, rp, j = sim(S, i + 1, stop, mode, zx)
            te, tx = int(t[i + 1]), int(t[j]) + T.H4; fpaid, real = FU.paid(S["sym"], "S", te, tx)
            r = dict(name=nm, sym=S["sym"], side="S", t=int(t[i]), te=te, tx=tx, R=g - FEE / rp - fpaid / rp, Rg=g, btc=int(F["btc1d"][i]), i=i)
            r["seg"] = seg(r)
            if r["seg"]: out.append(r)
    return out

def pick(R, nm, btcf, segs):
    x = [r for r in R if r["name"] == nm and r["seg"] in segs and (not btcf or r["btc"] == 1)]
    x.sort(key=lambda r: r["te"]); last = {}; keep = []
    for r in x:
        if r["te"] < last.get(r["sym"], 0): continue
        last[r["sym"]] = r["tx"]; keep.append(r)
    return keep

def st_(x): return tstat([(r["t"], r["R"]) for r in x]) if len(x) >= 2 else (len(x), 0.0, 0.0)
def eqs(x, risk=0.005, mx=8):
    eq, tk, dd = equity([(r["te"], r["sym"], r["R"], r["tx"]) for r in sorted(x, key=lambda r: r["te"])], risk=risk, maxpos=mx)
    return f"${eq:,.2f} ({tk} trades, DD {dd:.0f}%)"

if __name__ == "__main__":
    S_all = pickle.load(open("dt_feat.pkl", "rb")); SS = [S for S in S_all if S["inv"]]
    btcL = next(S for S in S_all if S["sym"] == "BTC" and not S["inv"]); btc_close = dict(zip(btcL["t"].tolist(), btcL["c"].tolist()))
    with Pool(4) as p: parts = p.map(run, [(S, btc_close) for S in SS])
    R = [r for P_ in parts for r in P_]; pickle.dump(R, open("dt_trades.pkl", "wb"))
    print(f"short series {len(SS)}; trades simulated {len(R)}")
    print("\n=== DEV (design coins, 2021-06 .. 2023-12, includes the 2022 bear market): shorts, avg net R per trade (t, n) ===")
    dev = {}
    for nm in NAMES:
        row = []
        for b in (False, True):
            s = st_(pick(R, nm, b, {"DEV"})); dev[(nm, b)] = s; row.append(f"{s[1]:+.3f}R (t={s[2]:+.1f}, n={s[0]:5d})")
        print(f"  {nm:12s} no filter {row[0]} | BTC down only {row[1]}")
    frozen = {nm: (True if nm == "KAL_S" else max((False, True), key=lambda b: dev[(nm, b)][2])) for nm in NAMES}
    print("frozen BTC-down filter (by DEV t; KAL_S fixed on): " + ", ".join(f"{nm}={'on' if b else 'off'}" for nm, b in frozen.items()))
    bench = {sg: st_(pick(R, "KAL_S", True, {sg}))[1] for sg in ("VAL", "FINAL", "UNSEEN", "OLD")}
    print("\n=== HOLDOUTS, one look (benchmark = KAL_S, the short half of the improved Kalman system) ===")
    print(f"  {'invention':12s} {'filter':7s} | {'VAL 2024':>15s} | {'FINAL 2025-26':>15s} | {'UNSEEN coins':>15s} | {'OLD 2020-21':>15s} | beats | pooled holdouts")
    for nm in NAMES:
        b = frozen[nm]; cells = []; beats = 0
        for sg in ("VAL", "FINAL", "UNSEEN", "OLD"):
            s = st_(pick(R, nm, b, {sg})); cells.append(f"{s[1]:+.3f} ({s[0]:4d})")
            if nm != "KAL_S" and s[1] > bench[sg]: beats += 1
        ps = st_(pick(R, nm, b, {"VAL", "FINAL", "UNSEEN", "OLD"}))
        v = "-" if nm == "KAL_S" else "YES" if beats == 4 else "partly" if beats == 3 else "no"
        print(f"  {nm:12s} {'BTCdown' if b else 'none':7s} | " + " | ".join(f"{c_:>15s}" for c_ in cells) +
              f" | {beats}/4 {v:6s} | {ps[1]:+.3f}R t={ps[2]:+.1f} n={ps[0]} {'PASS' if ps[1] > 0 and ps[2] >= 2 else 'fail'}")
    print("\n=== $10 short-only portfolios (0.5% risk, max 8 open, one per coin) ===")
    for nm in NAMES:
        b = frozen[nm]; old = pick(R, nm, b, {"OLD"}); rec = [r for r in pick(R, nm, b, {"VAL", "FINAL", "UNSEEN"}) if r["t"] >= D1]
        dv = pick(R, nm, b, {"DEV"})
        print(f"  {nm:12s} 2020-21: {eqs(old)} | 2021-23 (DEV, design coins): {eqs(dv)} | 2024-26 all coins: {eqs(rec)}")
    pickle.dump(dict(frozen=frozen), open("dt_sel.pkl", "wb"))
