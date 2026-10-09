"""Truth 3 (Nirodha) + Truth 4 (Magga) for the resonance rebuild.
Causes found by diag_res.py: fees are 5-6x the gross edge (median stop 0.85-1.3% on 15m), and for the EMA trigger, trades taken while
BTC has no 1D trend lose badly (DEV -0.61R, confirmed on VAL). Fixes are RE-SIMULATED, never made by deleting losing trades:
  F1 limit entry: limit at signal close - 0.25 ATR for 4 bars, fills only when price trades 0.05 ATR through it (checked BEFORE any
     cancel), fill bar can only stop out, 8 bps; cancelled if a bar that did not fill closes below the stop.
  F2 skip when BTC's 1D trend is flat (EMA trigger only, where the cause was accepted).
  F3 confirmation: enter at the open after the next bar only if that bar closes beyond the signal close (14 bps).
mode A (RES_BASE=15m): DEV comparison on the 6 frozen configs (+ video-like), keep fixes that help >= 4 of 6 (F2: 2 of 3),
                       then VAL and one look at FINAL + UNSEEN.
mode C (RES_BASE=1H):  the fee cause says 'move up a timeframe': 54-config grid x kept entry variants on 1H (1H/2H/4H/12H/1D),
                       selected on 2024-26 DEV design coins, VAL, FINAL + UNSEEN coins, then 2021-24 (d5) as a second holdout."""
import os, sys, json, pickle, math, collections, time, calendar
import numpy as np
from multiprocessing import Pool
REPO = os.environ.get("REPO", "/home/user/Bybit-dashbored")
for _d in ("resonance_test", "scenario_research"): sys.path.insert(1, f"{REPO}/research_archive/{_d}")
import res
from prereg_run import tstat, equity

MODE = sys.argv[1] if len(sys.argv) > 1 else "A"
LIM_ATR, LIM_BARS, FILL_PEN = 0.25, 4, 0.05
FUND = 0.5e-4; EXI = {"L": 0, "S1": 1, "S2": 2}; FEES = {"base": 14e-4, "lim": 8e-4, "conf": 14e-4}; RPK = {"base": "rp", "lim": "rpL", "conf": "rpC"}
XN = {"L": "ladder", "S1": "1.5R", "S2": "2.25R"}
ms = lambda d: calendar.timegm(time.strptime(d, "%Y-%m-%d")) * 1000

def worker(args):
    sym, inv, btc1d, folder = args
    res.FOLDER = folder
    t, o, h, l, c, v = res.load(sym, inv); n = len(t)
    if n < 2000: return []
    F = res.features(t, o, h, l, c, v); rows = []
    for i in range(1, n - 2):
        if not F["ready"][i] or not (F["choch"][i] or F["emax"][i]): continue
        a = F["atr"][i]; sb = F["stop_base"][i]
        e = i + 1; ep = o[e]; st = min(sb, ep - res.MINSTOP * abs(ep)); base = res.outcomes(t, h, l, c, e, ep, st, ep - st)
        Lp = c[i] - LIM_ATR * a; sL = min(sb, Lp - res.MINSTOP * abs(Lp)); lim = None
        for q in range(i + 1, min(n - 1, i + 1 + LIM_BARS)):
            if l[q] <= Lp - FILL_PEN * a: lim = res.outcomes(t, h, l, c, q, Lp, sL, Lp - sL, limit=True); break
            if c[q] < sL: break
        conf = None; rpC = None
        if i + 2 < n - 1 and c[i + 1] > c[i]:
            e2 = i + 2; ep2 = o[e2]; s2 = min(sb, ep2 - res.MINSTOP * abs(ep2)); conf = res.outcomes(t, h, l, c, e2, ep2, s2, ep2 - s2); rpC = (ep2 - s2) / abs(ep2)
        common = dict(sym=sym, side="S" if inv else "L", t=int(t[i]), res=int(F["res"][i]), adx=float(F["adx"][i]), btc1d=btc1d.get(int(t[i]), 0),
                      rp=(ep - st) / abs(ep), base=base, rpL=(Lp - sL) / abs(Lp), lim=lim, rpC=rpC, conf=conf)
        if F["choch"][i]: rows.append(dict(common, kind="choch"))
        if F["emax"][i]: rows.append(dict(common, kind="ema"))
    return rows

def build(folder):
    res.FOLDER = folder
    syms = sorted(f.split("_")[0] for f in os.listdir(folder) if f.endswith(f"_{res.SUFFIX}.json") and len(json.load(open(f"{folder}/{f}"))) >= 2000)
    btc = {}
    for inv in (False, True):
        t, o, h, l, c, v = res.load("BTC", inv); F = res.features(t, o, h, l, c, v); btc[inv] = dict(zip(t.tolist(), F["tr_1D"].tolist()))
    with Pool(4) as p: parts = p.map(worker, [(s, inv, btc[inv], folder) for s in syms for inv in (False, True)])
    return [r for P_ in parts for r in P_]

def dedupe(rows):
    rows.sort(key=lambda r: r["te"]); last = {}; out = []
    for r in rows:
        k = (r["sym"], r["side"])
        if r["te"] < last.get(k, 0): continue
        last[k] = r["tx"]; out.append(r)
    return out

def trades(rows, cfg, segf, fee_bps=None):
    trig, thr, adxm, x, var, f2 = cfg; out = []
    for r in rows:
        if r["kind"] != trig or r["res"] < thr or r["adx"] < adxm or not segf(r): continue
        if f2 and r["btc1d"] == 0: continue
        o_ = r[var]
        if o_ is None: continue
        R, hrs, te, tx = o_[EXI[x]]; rp = r[RPK[var]]; fee = FEES[var] if fee_bps is None else fee_bps * 1e-4
        out.append(dict(t=r["t"], te=te, tx=tx, sym=r["sym"], side=r["side"], R=R - (fee + FUND * hrs / 8) / rp))
    return dedupe(out)

def st_(x): return tstat([(r["t"], r["R"]) for r in x]) if len(x) >= 2 else (len(x), 0.0, 0.0)
def eqs(x, risk): eq, tk, dd = equity([(r["te"], r["sym"], r["R"], r["tx"]) for r in x], risk=risk); return f"${eq:.2f} ({tk} trades, max DD {dd:.0f}%)"
def winpf(x):
    if not x: return "-"
    gp = sum(r["R"] for r in x if r["R"] > 0); gl = -sum(r["R"] for r in x if r["R"] < 0)
    return f"win {sum(r['R'] > 0 for r in x)/len(x)*100:.0f}% PF {gp/max(gl,1e-9):.2f}"
def quarters(x, a, b):
    qs = [a + (b - a) * k / 4 for k in range(5)]; out = []
    for lo, hi in zip(qs, qs[1:]):
        v = [r["R"] for r in x if lo <= r["t"] < hi]; out.append(sum(v) / len(v) if v else float("nan"))
    return out
def name(cfg): return f"{cfg[0]:5s} agree>={cfg[1]} adx>={cfg[2]:2d} {XN[cfg[3]]:6s} entry={cfg[4]}{' skipBTCflat' if cfg[5] else ''}"

if MODE == "A":
    A = set("BTC ETH SOL XRP DOGE BNB ADA AVAX LINK DOT LTC NEAR".split())
    _b = json.load(open("s15/BTC_15m.json")); t0, t1 = _b[0][0], _b[-1][0]; T1 = t0 + (t1 - t0) * .5; T2 = t0 + (t1 - t0) * .75
    SEG = {"dev": lambda r: r["sym"] in A and r["t"] < T1, "val": lambda r: r["sym"] in A and T1 <= r["t"] < T2,
           "final": lambda r: r["sym"] in A and r["t"] >= T2, "unseen": lambda r: r["sym"] not in A}
    rows = build("s15"); pickle.dump(rows, open("fix_res_15m.pkl", "wb"))
    sel = pickle.load(open("res_sel.pkl", "rb")); frozen = [tuple(c) for c in sel["frozen"]]
    print("=== TRUTH 3 (Nirodha) on DEV: does removing the cause stop the loss? (15m, design coins, first half) ===")
    par = all((lambda x, y: x[0] == y[0] and abs(x[1] - y[1]) < 1e-9)(st_(trades(rows, (*c, "base", False), SEG["dev"])), sel["dev"][c]) for c in frozen)
    print(f"parity: baseline DEV stats identical to resonance_test/res_an.py for all 6 frozen configs: {par}")
    wins = collections.Counter(); cnt = collections.Counter(); imp = collections.defaultdict(list)
    for c in list(dict.fromkeys(frozen + [("choch", 5, 25, "L")])):      # the video-like config may already be one of the frozen ones
        b = st_(trades(rows, (*c, "base", False), SEG["dev"])); f1 = st_(trades(rows, (*c, "lim", False), SEG["dev"]))
        f3 = st_(trades(rows, (*c, "conf", False), SEG["dev"])); f2 = st_(trades(rows, (*c, "base", True), SEG["dev"])) if c[0] == "ema" else None
        line = (f"  {name((*c, 'base', False))[:34]:34s} base {b[1]:+.3f}R (n={b[0]}) | F1 limit {f1[1]:+.3f}R (n={f1[0]}) | F3 confirm {f3[1]:+.3f}R (n={f3[0]})"
                + (f" | F2 skip BTC flat {f2[1]:+.3f}R (n={f2[0]})" if f2 else ""))
        print(line + ("   [video-like, for reference]" if c not in frozen else "   [= video-like]" if c == ("choch", 5, 25, "L") else ""))
        if c in frozen:
            for fx, s in (("F1", f1), ("F3", f3)) + ((("F2", f2),) if f2 else ()):
                cnt[fx] += 1; imp[fx].append(s[1] - b[1])
                if s[1] > b[1] and s[0] >= 60: wins[fx] += 1
    kept = [fx for fx in ("F1", "F2", "F3") if cnt[fx] and wins[fx] >= (2 if fx == "F2" else 4)]
    print(f"fix helped on DEV in: F1 {wins['F1']}/{cnt['F1']}, F2 {wins['F2']}/{cnt['F2']}, F3 {wins['F3']}/{cnt['F3']} -> kept {kept}  "
          f"(mean change F1 {np.mean(imp['F1']):+.3f}R, F2 {np.mean(imp['F2']) if imp['F2'] else 0:+.3f}R, F3 {np.mean(imp['F3']):+.3f}R)")
    entry = "base"
    ek = [fx for fx in kept if fx in ("F1", "F3")]
    if ek: entry = {"F1": "lim", "F3": "conf"}[max(ek, key=lambda fx: np.mean(imp[fx]))]
    f2k = "F2" in kept
    pickle.dump(dict(kept=kept, entry=entry, f2=f2k), open("fix_res_kept.pkl", "wb"))
    print(f"\n=== TRUTH 4 (Magga) on 15m: frozen configs with the kept fixes (entry={entry}, skip-BTC-flat for EMA={f2k}) -> VAL, then ONE LOOK ===")
    for c in frozen:
        cf = (*c, entry, f2k and c[0] == "ema"); v = st_(trades(rows, cf, SEG["val"]))
        f = trades(rows, cf, SEG["final"]); u = trades(rows, cf, SEG["unseen"]); fs = st_(f); us = st_(u); q = quarters(u, t0, t1)
        ok = fs[0] >= 100 and fs[1] >= 0.10 and us[1] > 0 and sum(1 for z in q if z == z and z > 0) >= 3
        print(f"  {name(cf):58s} VAL {v[1]:+.3f}R (n={v[0]}) | FINAL {fs[1]:+.3f}R t={fs[2]:+.1f} (n={fs[0]}) | UNSEEN {us[1]:+.3f}R t={us[2]:+.1f} (n={us[0]}) "
              f"qtrs {' '.join(f'{z:+.2f}' for z in q)} | {winpf(f + u)} | {'PASS' if ok else 'fail'}")
        print(f"      $10 at 1% -> {eqs(f + u, 0.01)} | at 2% -> {eqs(f + u, 0.02)}")

if MODE == "C":
    kept = pickle.load(open("fix_res_kept.pkl", "rb"))
    DES = set("AAVE AVAX BNB BTC CRV DOGE FIL HBAR ICP NEAR OP POL SOL STX SUI TRX UNI WLD XRP".split())  # scen_an.py split
    T1, T2 = ms("2025-07-11"), ms("2026-02-19")
    SEG = {"dev": lambda r: r["sym"] in DES and r["t"] < T1, "val": lambda r: r["sym"] in DES and T1 <= r["t"] < T2,
           "final": lambda r: r["sym"] in DES and r["t"] >= T2, "unseen": lambda r: r["sym"] not in DES, "all": lambda r: True}
    r4 = build("d4"); r5 = build("d5")
    variants = [("base", False)] + ([(kept["entry"], False)] if kept["entry"] != "base" else []) + ([("base", True)] if kept["f2"] else [])
    grid = [(tg, th, ad, x, var, f2) for tg in ("choch", "ema") for th in (3, 4, 5) for ad in (0, 20, 25) for x in ("L", "S1", "S2")
            for var, f2 in variants if not (f2 and tg != "ema")]
    print(f"=== 1H base timeframe (1H/2H/4H/12H/1D), the fee fix one level up: {len(grid)} configs; entry variants {variants} ===")
    allp = [(c, st_(trades(r4, c, SEG["all"], fee_bps=0))[1], st_(trades(r4, c, SEG["all"]))) for c in grid]
    print(f"2024-26 all coins: positive before fees {sum(1 for _, g, _ in allp if g > 0)}/{len(grid)}, after fees {sum(1 for _, _, s in allp if s[1] > 0)}/{len(grid)}; "
          f"median stop {np.median([r['rp'] for r in r4])*100:.2f}% -> fee {np.median([14e-4 / r['rp'] for r in r4]):.3f}R per trade")
    dev = {c: st_(trades(r4, c, SEG["dev"])) for c in grid}
    print(f"chance check: DEV t>=3: {sum(1 for s in dev.values() if s[0] >= 60 and s[2] >= 3)} (luck ~{0.00135*len(grid):.2f}), t>=2: {sum(1 for s in dev.values() if s[0] >= 60 and s[2] >= 2)} (luck ~{0.0228*len(grid):.1f})")
    frozen = []
    for tg in ("choch", "ema"):
        for x in ("L", "S1", "S2"):
            for var, f2 in variants:
                cand = [(c, s) for c, s in dev.items() if c[0] == tg and c[3] == x and c[4] == var and c[5] == f2 and s[0] >= 60]
                if cand: frozen.append(max(cand, key=lambda z: z[1][2]))
    print(f"\n{'frozen on DEV (best agree/adx per trigger x exit x entry)':58s} DEV            | VAL            | FINAL (design coins)      | UNSEEN coins              | verdict")
    passed = []
    for c, s in frozen:
        v = st_(trades(r4, c, SEG["val"])); f = trades(r4, c, SEG["final"]); u = trades(r4, c, SEG["unseen"]); fs = st_(f); us = st_(u)
        q = quarters(u, ms("2024-04-09"), ms("2026-09-30"))
        ok = fs[0] >= 100 and fs[1] >= 0.10 and us[1] > 0 and sum(1 for z in q if z == z and z > 0) >= 3
        if ok: passed.append(c)
        print(f"  {name(c):56s} {s[1]:+.3f} t={s[2]:+.1f} | {v[1]:+.3f} t={v[2]:+.1f} | {fs[1]:+.3f} t={fs[2]:+.1f} n={fs[0]:4d} | {us[1]:+.3f} t={us[2]:+.1f} n={us[0]:4d} | {'PASS' if ok else 'fail'}")
    print("\n=== second holdout: the same frozen configs on 2021-24 (d5, never used for resonance), all coins; pass = avg > 0 and t >= 2 ===")
    for c, s in frozen:
        x = trades(r5, c, SEG["all"]); xs = st_(x); g = st_(trades(r5, c, SEG["all"], fee_bps=0))
        print(f"  {name(c):56s} 2021-24: {xs[1]:+.3f}R t={xs[2]:+.1f} n={xs[0]} (gross {g[1]:+.3f}) | {winpf(x)} | {'PASS' if xs[1] > 0 and xs[2] >= 2 else 'fail'}"
              f" | $10 at 1% -> {eqs(x, 0.01)} | at 2% -> {eqs(x, 0.02)}")
    pickle.dump(dict(frozen=frozen, passed=passed), open("fix_res_1h.pkl", "wb"))
