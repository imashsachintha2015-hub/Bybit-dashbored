"""Limit orders on the previous-week-low sweep (the scenario family with the best gross edge in BOTH periods: about +0.10-0.12R per
trade before fees, about 0 after) and on AMD-FVG (corrected fill rule) with limit take-profits. Execution model: lx_core.py.
PWL event (exactly scen2.py): first 1H bar of the week whose low pierces last week's low by 0.1 ATR(EMA27) and closes back above it.
Stop = that bar's low - 0.1 ATR (min 0.3% from the entry); targets 2R or last week's high (only if >= 1R away); 96h max.
Modes: MKT next open (14 bps) | RETEST limit at last week's low | LMT25 limit at close - 0.25 ATR (limits valid 4 bars, maker TPs).
Grid: 1D bias any/with/against x BTC any/aligned x target 2R/PWH x 3 modes = 36. Selection on 2024-26 DEV design coins only, then VAL,
FINAL + UNSEEN coins, then 2021-24 (d5; its gross numbers for this family were seen in the cause map - stated, not hidden)."""
import os, sys, json, pickle, math, collections, time, calendar
import numpy as np
from multiprocessing import Pool
REPO = os.environ.get("REPO", "/home/user/Bybit-dashbored")
for _d in ("resonance_test", "scenario_research", "limit_orders", "loss_diagnosis"): sys.path.insert(1, f"{REPO}/research_archive/{_d}")
sys.path.insert(1, REPO)
import scen
from lx_core import sim_exec, limit_fill, TAKER, MAKER, TP_PEN, FILL_PEN
from prereg_run import tstat, equity
HR, DAY = 3600000, 86400000; FUND = 0.5e-4; MINSTOP = 0.003; HOLD = 96
MODES = ("MKT", "RETEST", "LMT25")
ms = lambda d: calendar.timegm(time.strptime(d, "%Y-%m-%d")) * 1000

def pwl_worker(args):
    sym, inv, btc_tr, folder = args
    scen.D = folder
    t, o, h, l, c, v = scen.load(sym, inv); n = len(t)
    if n < 2000: return []
    tr = [h[0] - l[0]] + [max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])) for i in range(1, n)]
    atr = scen.ema(tr, 27); tr1d, _ = scen.daily_trend(t, o, h, l, c)
    day_of = [ts // DAY for ts in t]; dayH = {}; dayL = {}
    for i, d in enumerate(day_of): dayH[d] = max(dayH.get(d, -1e30), h[i]); dayL[d] = min(dayL.get(d, 1e30), l[i])
    wk = lambda d: (d - 4) // 7; wkH = {}; wkL = {}
    for d in dayH: w = wk(d); wkH[w] = max(wkH.get(w, -1e30), dayH[d]); wkL[w] = min(wkL.get(w, 1e30), dayL[d])
    done = set(); out = []; H, L, C = np.array(h), np.array(l), np.array(c)
    for i in range(300, n - 2):
        a = atr[i]; d = day_of[i]
        if a <= 0 or d - 1 not in dayH: continue
        w = wk(d)
        if w - 1 not in wkH or w in done: continue
        PWH, PWL = wkH[w - 1], wkL[w - 1]
        if not (l[i] < PWL - 0.1 * a and c[i] > PWL): continue
        done.add(w); sb = l[i] - 0.1 * a
        dd = (t[i] // DAY) if (t[i] + HR) % DAY == 0 else (t[i] // DAY - 1)
        rec = dict(sym=sym, side="S" if inv else "L", t=t[i], tr1d=tr1d[i], btc=btc_tr.get(dd, 0), modes={})
        for m in MODES:
            if m == "MKT": e = i + 1; ep = o[e]; lim = False
            else:
                ep = PWL if m == "RETEST" else c[i] - 0.25 * a; lim = True
                e = limit_fill(L, C, i, ep, min(sb, ep - MINSTOP * abs(ep)), a, 4)
                if e is None: rec["modes"][m] = None; continue
            st = min(sb, ep - MINSTOP * abs(ep)); risk = ep - st; rp = risk / abs(ep); res_ = {}
            for tg, mult in (("2R", 2.0), ("PWH", (PWH - ep) / risk)):
                if mult < 1.0: continue
                g, fee, j = sim_exec(H, L, C, e, ep, st, risk, mult, lim, lim, a, hold=HOLD)
                res_[tg] = (g, fee, j - e + 1, t[e], t[j] + HR)
            rec["modes"][m] = (rp, res_, sb <= ep - MINSTOP * abs(ep))      # last flag: stop not widened (scen2 kept only these)
        out.append(rec)
    return out

def build(folder):
    scen.D = folder
    syms = sorted(f.split("_")[0] for f in os.listdir(folder) if f.endswith("_1H.json") and len(json.load(open(f"{folder}/{f}"))) >= 2000)
    bL, bS = scen.btc_daily(False), scen.btc_daily(True)
    with Pool(4) as p: parts = p.map(pwl_worker, [(s, inv, bS if inv else bL, folder) for s in syms for inv in (False, True)])
    return [r for P_ in parts for r in P_]

BIAS = {"any": lambda r: True, "with1D": lambda r: r["tr1d"] == 1, "against1D": lambda r: r["tr1d"] == -1}
def dedupe(rows):
    rows.sort(key=lambda r: r["te"]); last = {}; out = []
    for r in rows:
        k = (r["sym"], r["side"])
        if r["te"] < last.get(k, 0): continue
        last[k] = r["tx"]; out.append(r)
    return out
def trades(rows, cfg, segf, gross=False):
    b, bt, tg, m = cfg; out = []
    for r in rows:
        if not segf(r) or not BIAS[b](r) or (bt and r["btc"] != 1): continue
        mm = r["modes"].get(m)
        if mm is None or tg not in mm[1]: continue
        rp = mm[0]; g, fee, hrs, te, tx = mm[1][tg]
        out.append(dict(t=r["t"], te=te, tx=tx, sym=r["sym"], side=r["side"], R=g - FUND * hrs / 8 / rp - (0 if gross else fee / rp)))
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

# ---------------- AMD-FVG, corrected fill, limit entry + taker TP (old cost model) vs limit entry + maker TP
def amd_job(args):
    import fix_amd
    from backend_lib import amd_fvg
    sym, inv = args
    rows = fix_amd.series(sym)
    if len(rows) < 2000: return []
    A = amd_fvg.prep([tuple(r) for r in rows], invert=inv); n = A["n"]; H, L, C = A["h"], A["l"], A["c"]; out = []
    for P in (amd_fvg.NO_GATE, amd_fvg.PRIMARY):
        last = -99
        for i, sl, zb, zt, ce in fix_amd.setups(A, P):
            if i - last < 6: continue
            touch = None
            for w in range(1, P["w"] + 1):
                q = i + w
                if q >= n - 1: break
                if L[q] <= ce - amd_fvg.FILL_PEN * A["atr"][i]: touch = q; break
                if C[q] < zb: break
            if touch is None: continue
            risk = ce - sl; rp = risk / abs(ce); r_ = {}
            for mk in (False, True):
                g, fee, j = sim_exec(H, L, C, touch, ce, sl, risk, P["tp"], True, mk, A["atr"][i], hold=P["tmax"])
                if j >= n - 1 and g not in (-1.0, P["tp"]): break
                r_[mk] = (g - (fee if mk else amd_fvg.FEE_LIMIT) / rp - FUND * (j - touch + 1) / 8 / rp, A["t"][touch], A["t"][j] + HR)
            if len(r_) == 2:
                out.append(dict(cfg=P["name"], sym=sym, side="S" if inv else "L", t=A["t"][i], taker=r_[False], maker=r_[True])); last = i
    return out

if __name__ == "__main__":
    DES = set("AAVE AVAX BNB BTC CRV DOGE FIL HBAR ICP NEAR OP POL SOL STX SUI TRX UNI WLD XRP".split())
    T1, T2 = ms("2025-07-11"), ms("2026-02-19"); t0, t1 = ms("2024-04-09"), ms("2026-09-30")
    SEG = {"dev": lambda r: r["sym"] in DES and r["t"] < T1, "val": lambda r: r["sym"] in DES and T1 <= r["t"] < T2,
           "final": lambda r: r["sym"] in DES and r["t"] >= T2, "unseen": lambda r: r["sym"] not in DES, "all": lambda r: True}
    r4 = build("d4"); r5 = build("d5")
    # parity with scen2.py: same events; MKT gross R equal where scen2 kept the trade (stop not widened)
    for folder, rows, f in (("d4", r4, "scen2_ev.pkl"), ("d5", r5, "scen2_ev_d5.pkl")):
        ref = [e for e in pickle.load(open(f, "rb"))["ev"] if e["fam"] == "PWL_SWEEP_RECLAIM"]
        a = {(e["sym"], e["side"], e["t"]) for e in ref}; b = {(r["sym"], r["side"], r["t"]) for r in rows}
        mine = {(r["sym"], r["side"], r["t"]): r["modes"]["MKT"] for r in rows}; bad = 0; chk = 0
        for e in ref:
            if not e["res"]: continue
            mm = mine.get((e["sym"], e["side"], e["t"]))
            for tg, key in (("2R", "2R"), ("struct", "PWH")):
                if tg in e["res"] and mm and mm[2] and key in mm[1]:
                    chk += 1; bad += abs(e["res"][tg][0] - mm[1][key][0]) > 1e-9
        print(f"[{folder}] parity with scen2 PWL events: {len(b)} vs {len(a)}, same set {a == b}; gross R checked {chk}, mismatches {bad}")
    grid = [(b, bt, tg, m) for b in BIAS for bt in (False, True) for tg in ("2R", "PWH") for m in MODES]
    print(f"\n=== previous-week-low sweep + reclaim, {len(grid)} configs ===")
    for m in MODES:
        fr = sum(1 for r in r4 if r["modes"].get(m) is not None) / len(r4)
        g = [st_(trades(r4, c, SEG["all"], gross=True))[1] for c in grid if c[3] == m]; nn = [st_(trades(r4, c, SEG["all"]))[1] for c in grid if c[3] == m]
        g5 = [st_(trades(r5, c, SEG["all"], gross=True))[1] for c in grid if c[3] == m]; n5 = [st_(trades(r5, c, SEG["all"]))[1] for c in grid if c[3] == m]
        print(f"  {m:6s} fill {fr*100:5.1f}% | 2024-26 avg over 12 configs: before fees {np.mean(g):+.3f}R, after {np.mean(nn):+.3f}R | 2021-24: before {np.mean(g5):+.3f}R, after {np.mean(n5):+.3f}R")
    dev = {c: st_(trades(r4, c, SEG["dev"])) for c in grid}
    print(f"chance check: DEV t>=2: {sum(1 for s in dev.values() if s[0] >= 60 and s[2] >= 2)} (luck ~{0.0228*len(grid):.1f})")
    print(f"\n{'frozen on DEV (best per mode)':40s} DEV            | VAL            | FINAL                | UNSEEN coins         | 2021-24 (d5)")
    for m in MODES:
        cand = [(c, s) for c, s in dev.items() if c[3] == m and s[0] >= 60]
        c, s = max(cand, key=lambda z: z[1][2]); v = st_(trades(r4, c, SEG["val"])); f = trades(r4, c, SEG["final"]); u = trades(r4, c, SEG["unseen"])
        fs, us = st_(f), st_(u); q = quarters(u, t0, t1); ok = fs[0] >= 100 and fs[1] >= 0.10 and us[1] > 0 and sum(1 for z in q if z == z and z > 0) >= 3
        x5 = trades(r5, c, SEG["all"]); s5 = st_(x5)
        lab = f"{m}|{c[0]}|{'btcAligned' if c[1] else 'any'}|{c[2]}"
        print(f"  {lab:38s} {s[1]:+.3f} t={s[2]:+.1f} | {v[1]:+.3f} t={v[2]:+.1f} | {fs[1]:+.3f} t={fs[2]:+.1f} n={fs[0]:3d} | {us[1]:+.3f} t={us[2]:+.1f} n={us[0]:4d} | "
              f"{s5[1]:+.3f} t={s5[2]:+.1f} n={s5[0]} | {'PASS' if ok and s5[1] > 0 else 'fail'}")
        print(f"      {winpf(f + u)} | $10 final+unseen at 1% -> {eqs(f + u, 0.01)} | 2021-24 at 1% -> {eqs(x5, 0.01)}, at 2% -> {eqs(x5, 0.02)}")

    print("\n=== AMD-FVG (corrected fill), limit entry: take-profit as taker (old model, 8 bps total) vs as maker limit (2 bps, needs trade-through) ===")
    syms = sorted(f.split("_")[0] for f in os.listdir("d4") if f.endswith("_1H.json"))
    with Pool(4) as p: parts = p.map(amd_job, [(s, inv) for s in syms for inv in (False, True)])
    A_ = [r for P_ in parts for r in P_]
    for cfg in ("AMD_NO_GATE", "AMD_PRIMARY"):
        for k in ("taker", "maker"):
            x = [dict(t=r["t"], te=r[k][1], tx=r[k][2], sym=r["sym"], side=r["side"], R=r[k][0]) for r in A_ if r["cfg"] == cfg]
            s = st_(x); yrs = collections.defaultdict(list)
            for r in x: yrs[time.gmtime(r["t"] / 1000).tm_year].append(r["R"])
            print(f"  {cfg:12s} TP {k}: n={s[0]} {s[1]:+.3f}R t={s[2]:+.1f} | {winpf(x)} | $10 at 1% -> {eqs(x, 0.01)} | "
                  + " ".join(f"{y}:{np.mean(v):+.2f}" for y, v in sorted(yrs.items())))
