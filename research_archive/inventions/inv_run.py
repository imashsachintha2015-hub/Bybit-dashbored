"""Invention lab runner: exactly the protocol in PREREG_inventions.md (written before this ran)."""
import os, sys, pickle, math, collections, time, calendar
os.environ["TL_WITH_D6"] = "1"
import numpy as np
from multiprocessing import Pool
import tl_core as T, inv_core as I
sys.path.insert(1, os.path.join(os.environ.get("REPO", "/home/user/Bybit-dashbored"), "research_archive", "scenario_research"))
from prereg_run import tstat, equity
ms = lambda d: calendar.timegm(time.strptime(d, "%Y-%m-%d")) * 1000
DES = set("AAVE AVAX BNB BTC CRV DOGE FIL HBAR ICP NEAR OP POL SOL STX SUI TRX UNI WLD XRP".split())
D0, D1, V1, OLD0 = ms("2021-06-01"), ms("2024-01-01"), ms("2025-01-01"), ms("2020-01-01")
FEE = 14e-4; FUND_BAR = 0.25e-4; MINSTOP = 0.005; HOLD = 120
NAMES = ["KALMAN", "NEWTON", "IMM", "SWARM", "HURST", "ENTROPY", "VOLCLOCK", "ELASTIC", "PATH"]

def seg(r):
    t, s = r["t"], r["sym"]
    if OLD0 <= t < D0: return "OLD"
    if t < OLD0: return None
    if s not in DES: return "UNSEEN"
    return "DEV" if t < D1 else "VAL" if t < V1 else "FINAL"

def btc_map(inv):
    t, o, h, l, c, v = T.load4h("BTC", inv); return dict(zip(t.tolist(), T.daily_trend(t, c).tolist()))

def build(args):
    sym, inv, btc = args
    d = T.load4h(sym, inv)
    if d is None or len(d[0]) < 800: return None
    t, o, h, l, c, v = d; a = T.atr14(h, l, c)
    z, lev, dev = I.llt(c); vz, az = I.newton(c); zi = I.imm(c); zv = I.volclock(c, v); vr = I.variance_ratio(c); pe, gate = I.perm_entropy(c)
    return dict(sym=sym, inv=inv, t=t, o=o, h=h, l=l, c=c, v=v, a=a, btc=np.array([btc.get(int(x), 0) for x in t]),
                z=z, dev=dev, vz=vz, az=az, zi=zi, zv=zv, vr=vr, gate=gate)

def sim(S, e, stop, zx):
    o, h, l, c = S["o"], S["h"], S["l"], S["c"]; n = len(c); ep = o[e]
    if ep - stop < MINSTOP * abs(ep): stop = ep - MINSTOP * abs(ep)
    risk = ep - stop
    for j in range(e, min(n, e + HOLD)):
        if l[j] <= stop: return (stop - ep) / risk, risk / abs(ep), j - e + 1, j
        if zx[j] < 0 and j + 1 < n: return (o[j + 1] - ep) / risk, risk / abs(ep), j - e + 2, j + 1
    j = min(n - 1, e + HOLD - 1); return (c[j] - ep) / risk, risk / abs(ep), j - e + 1, j

def trades(S):
    z, n = S["z"], len(S["c"]); out = []
    up = lambda x, i, th=1.0: x[i] > th and x[i - 1] <= th
    in_trend = False; elastic_done = False
    for i in range(201, n - 2):
        if not (z[i] > 1): in_trend = False; elastic_done = False
        elif not in_trend: in_trend = True; elastic_done = False
        kal = up(z, i)
        ent = {"KALMAN": kal, "NEWTON": up(S["vz"], i) and S["az"][i] > 0, "IMM": up(S["zi"], i), "SWARM": kal and S["breadth"][i] >= 0.6,
               "HURST": kal and S["vr"][i] >= 1.1, "ENTROPY": kal and bool(S["gate"][i]), "VOLCLOCK": up(S["zv"], i),
               "ELASTIC": False, "PATH": kal and I.barrier_lighter(S["h"], S["l"], S["c"], S["v"], S["a"], i)}
        if in_trend and not elastic_done and S["dev"][i] <= -1.0: ent["ELASTIC"] = True; elastic_done = True
        for nm, ok in ent.items():
            if not ok: continue
            zx = S["vz"] if nm == "NEWTON" else S["zi"] if nm == "IMM" else S["zv"] if nm == "VOLCLOCK" else z
            g, rp, bars, j = sim(S, i + 1, S["c"][i] - 3.0 * S["a"][i], zx)
            r = dict(name=nm, sym=S["sym"], side="S" if S["inv"] else "L", t=int(S["t"][i]), te=int(S["t"][i + 1]), tx=int(S["t"][j]) + T.H4,
                     R=g - (FEE + FUND_BAR * bars) / rp, btc=int(S["btc"][i]), i=i)
            r["seg"] = seg(r)
            if r["seg"]: out.append(r)
    return out

def pick(R, nm, btcf, segs):
    x = [r for r in R if r["name"] == nm and r["seg"] in segs and (not btcf or r["btc"] == 1)]
    x.sort(key=lambda r: r["te"]); last = {}; keep = []
    for r in x:
        k = (r["sym"], r["side"])
        if r["te"] < last.get(k, 0): continue
        last[k] = r["tx"]; keep.append(r)
    return keep

def st_(x): return tstat([(r["t"], r["R"]) for r in x]) if len(x) >= 2 else (len(x), 0.0, 0.0)
def eqs(x, risk=0.005, mx=8):
    eq, tk, dd = equity([(r["te"], r["sym"], r["R"], r["tx"]) for r in sorted(x, key=lambda r: r["te"])], risk=risk, maxpos=mx)
    return f"${eq:,.2f} ({tk} trades, DD {dd:.0f}%)"

if __name__ == "__main__":
    syms = sorted(f.split("_")[0] for f in os.listdir("d4") if f.endswith("_1H.json"))
    bL, bS = btc_map(False), btc_map(True)
    with Pool(4) as p: SS = [s for s in p.map(build, [(s, inv, bS if inv else bL) for s in syms for inv in (False, True)]) if s]
    # SWARM breadth: share of coins (same orientation) whose Kalman z > 0 at each 4H timestamp
    for inv in (False, True):
        cnt = collections.Counter(); pos = collections.Counter()
        for S in SS:
            if S["inv"] != inv: continue
            for tt, zz in zip(S["t"].tolist(), S["z"].tolist()):
                if zz == zz: cnt[tt] += 1; pos[tt] += zz > 0
        for S in SS:
            if S["inv"] == inv: S["breadth"] = np.array([pos[tt] / cnt[tt] if cnt[tt] >= 5 else 0.0 for tt in S["t"].tolist()])
    with Pool(4) as p: parts = p.map(trades, SS)
    R = [r for P_ in parts for r in P_]; pickle.dump(R, open("inv_trades.pkl", "wb"))
    print(f"series {len(SS)}; trades simulated {len(R)}")
    print("\n=== DEV (design coins, 2021-06 .. 2023-12): avg net R per trade (t, trades), without / with the BTC filter ===")
    dev = {}
    for nm in NAMES:
        row = []
        for b in (False, True):
            s = st_(pick(R, nm, b, {"DEV"})); dev[(nm, b)] = s; row.append(f"{s[1]:+.3f}R (t={s[2]:+.1f}, n={s[0]:5d})")
        print(f"  {nm:9s} no filter {row[0]} | BTC filter {row[1]}")
    frozen = {nm: max((False, True), key=lambda b: dev[(nm, b)][2]) for nm in NAMES}
    print("frozen BTC filter choice (by DEV t): " + ", ".join(f"{nm}={'on' if b else 'off'}" for nm, b in frozen.items()))
    bench = {sg: st_(pick(R, "KALMAN", True, {sg}))[1] for sg in ("VAL", "FINAL", "UNSEEN", "OLD")}
    print("\n=== HOLDOUTS, one look (benchmark = KALMAN with BTC filter) ===")
    print(f"  {'invention':9s} {'filter':6s} | {'VAL 2024':>16s} | {'FINAL 2025-26':>16s} | {'UNSEEN coins':>16s} | {'OLD 2020-21':>16s} | beats bench | pooled holdouts | overlap w/ Kalman")
    kal_entries = collections.defaultdict(set)
    for r in pick(R, "KALMAN", False, {"DEV", "VAL", "FINAL", "UNSEEN", "OLD"}): kal_entries[(r["sym"], r["side"])].add(r["i"])
    summary = {}
    for nm in NAMES:
        b = frozen[nm]; cells = []; beats = 0
        for sg in ("VAL", "FINAL", "UNSEEN", "OLD"):
            s = st_(pick(R, nm, b, {sg})); cells.append(f"{s[1]:+.3f} ({s[0]:5d})")
            if nm != "KALMAN" and s[1] > bench[sg]: beats += 1
        pool = pick(R, nm, b, {"VAL", "FINAL", "UNSEEN", "OLD"}); ps = st_(pool)
        ov = sum(1 for r in pool if any(abs(r["i"] - k) <= 3 for k in kal_entries[(r["sym"], r["side"])])) / max(1, len(pool))
        summary[nm] = (b, beats, ps)
        verdict = "-" if nm == "KALMAN" else ("YES" if beats == 4 else "partly" if beats == 3 else "no")
        print(f"  {nm:9s} {'BTC' if b else 'none':6s} | " + " | ".join(f"{c_:>16s}" for c_ in cells) +
              f" | {beats}/4 {verdict:6s} | {ps[1]:+.3f}R t={ps[2]:+.1f} {'PASS' if ps[1] > 0 and ps[2] >= 2 else 'fail'} | {ov*100:4.0f}%")
    print(f"  benchmark KALMAN+BTC: " + " | ".join(f"{sg} {v:+.3f}" for sg, v in bench.items()))
    print("\n=== $10 portfolios (0.5% risk, max 8 open, one per coin), frozen configs ===")
    for nm in NAMES:
        b = frozen[nm]
        old = pick(R, nm, b, {"OLD"}); rec = [r for r in pick(R, nm, b, {"VAL", "FINAL", "UNSEEN"}) if r["t"] >= D1]
        print(f"  {nm:9s} ({'BTC' if b else 'none'}) 2020-21: {eqs(old)} | 2024-26 all coins: {eqs(rec)}")
    pickle.dump(dict(frozen=frozen, summary=summary), open("inv_sel.pkl", "wb"))
