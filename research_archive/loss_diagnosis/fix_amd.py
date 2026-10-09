"""Four Truths for AMD-FVG on one continuous OKX 1H series per coin, 2021-06 .. 2026-09 (d5 + d4 joined; they are contiguous).
Baseline = the CORRECTED fill rule (a touched limit is a fill; backend_lib/amd_fvg.py after 2026-10-08).
Truth 1: anatomy (gross vs fees vs funding, how losers die: MFE before the stop).
Truth 2: the cause found while fixing the fill rule - gaps that are run through on the first touch (touch bar closes below the gap)
         lose; that is only known at the bar's CLOSE, so a resting limit cannot avoid it.
Truth 3/4, path A ('confirm'): do not rest a limit; wait for the first touch bar to close. Gap held (close >= gap bottom) -> enter at the
         next open, market, 14 bps, same stop price, target 2R from the real entry. Gap failed -> no trade. No parameters were fitted;
         it was designed after seeing 2021-26, so its real test is forward data (stated, not hidden).
Truth 3/4, path B (walk-forward cause filter): for each test year Y, causes are learned only from trades that closed before Y
         (bucket worse than the rest with t <= -2), then applied to year Y. Leak assertion included."""
import os, sys, json, math, collections, time, calendar
sys.path.insert(0, "/home/user/Bybit-dashbored")
import numpy as np
from multiprocessing import Pool
from backend_lib import amd_fvg
from backend_lib.amd_fvg import _drift_ok, _outcome
REPO = os.environ.get("REPO", "/home/user/Bybit-dashbored")
for _d in ("resonance_test", "scenario_research"): sys.path.insert(1, f"{REPO}/research_archive/{_d}")
from prereg_run import tstat, equity
import scen
HR = 3600000; DAY = 86400000; FUND = 0.5e-4
ms = lambda d: calendar.timegm(time.strptime(d, "%Y-%m-%d")) * 1000

def series(sym):
    a = json.load(open(f"d5/{sym}_1H.json")) if os.path.exists(f"d5/{sym}_1H.json") else []
    b = json.load(open(f"d4/{sym}_1H.json")); last = a[-1][0] if a else -1
    return a + [r for r in b if r[0] > last]

def setups(A, P):
    """AMD detection exactly as amd_fvg.scan (no spacing here; spacing is applied per entry method)."""
    n = A["n"]; N = P["N"]; h, l, c, o, atr, vr = A["h"], A["l"], A["c"], A["o"], A["atr"], A["vrel"]
    for i in range(110, n):
        at = atr[i]
        if at <= 0 or not _drift_ok(A, i, P["gate"]): continue
        if not (l[i] > h[i - 2] and (c[i - 1] - o[i - 1]) >= P["D"] * atr[i - 1] and vr[i - 1] >= P["v"]): continue
        hit = None
        for j in range(i - 2 - P["W"], i - 1):
            RL = A["rmin"][N][j]; RH0 = A["rmax"][N][j]
            if RL is None or (RH0 - RL) / atr[j] > P["comp"]: continue
            if l[j] < RL - P["s"] * atr[j] and c[i - 1] > RL: hit = j; break
        if hit is None: continue
        sl = min(l[hit:i]) - P["b"] * at; zb, zt = h[i - 2], l[i]; ce = (zb + zt) / 2
        if ce - sl <= 0 or (ce - sl) / abs(ce) < P["mr"]: continue
        yield i, sl, zb, zt, ce

def walk(A, e, ep, stop, tp, tmax, limit):
    """returns gross R, exit index, MFE, MAE (R units). limit: the fill bar can only stop out."""
    h, l, c, n = A["h"], A["l"], A["c"], A["n"]; risk = ep - stop; mfe = mae = 0.0
    if limit and l[e] <= stop: return -1.0, e, 0.0, 1.0
    for j in range(e + 1 if limit else e, min(n, e + tmax)):
        if l[j] <= stop: return -1.0, j, mfe, 1.0
        if h[j] >= tp: return (tp - ep) / risk, j, max(mfe, (tp - ep) / risk), mae
        mfe = max(mfe, (h[j] - ep) / risk); mae = max(mae, (ep - l[j]) / risk)
    j = min(n - 1, e + tmax - 1); return (c[j] - ep) / risk, j, mfe, mae

def feats(A, i, tr1d, btc, sym):
    t, c, atr = A["t"], A["c"], A["atr"]
    k = max(0, i - 720); ap = [atr[x] / abs(c[x]) for x in range(k, i + 1)]
    b = btc.get(t[i], (0, 0.0)); hr = (t[i] // HR) % 24
    return dict(vol_regime=(atr[i] / abs(c[i])) / (sum(ap) / len(ap)), coin_1d=tr1d[i], btc_1d=b[0], btc_24h=b[1],
                relvol=A["vrel"][i - 1], session=0 if hr < 7 else 1 if hr < 13 else 2 if hr < 21 else 3, weekday=((t[i] // DAY) + 3) % 7)

def job(args):
    sym, inv, btc = args
    rows = series(sym)
    if len(rows) < 2000: return []
    A = amd_fvg.prep([tuple(r) for r in rows], invert=inv); n = A["n"]
    t_ = [r[0] for r in rows]; c_ = [(-r[4] if inv else r[4]) for r in rows]
    tr1d, _ = scen.daily_trend(t_, [(-r[1] if inv else r[1]) for r in rows], [(-r[3] if inv else r[2]) for r in rows],
                               [(-r[2] if inv else r[3]) for r in rows], c_)
    out = []
    for P in (amd_fvg.NO_GATE, amd_fvg.PRIMARY):
        last = {"limit": -99, "confirm": -99}
        for i, sl, zb, zt, ce in setups(A, P):
            f = feats(A, i, tr1d, btc, sym); f["gap_atr"] = (zt - zb) / A["atr"][i]; f["risk_pct"] = (ce - sl) / abs(ce) * 100
            touch = None; held = None
            for w in range(1, P["w"] + 1):
                q = i + w
                if q >= n - 1: break
                if A["l"][q] <= ce - amd_fvg.FILL_PEN * A["atr"][i]: touch = q; held = A["c"][q] >= zb; break
                if A["c"][q] < zb: break
            if touch is None: continue
            # baseline: corrected resting limit at the 50% level (fill on the touch bar)
            if i - last["limit"] >= 6:
                tp = ce + P["tp"] * (ce - sl); Rg, x, mfe, mae = walk(A, touch, ce, sl, tp, P["tmax"], True)
                if x < n - 1 or Rg in (-1.0, P["tp"]):
                    rp = (ce - sl) / abs(ce); hrs = x - touch + 1
                    out.append(dict(m="limit", cfg=P["name"], sym=sym, side="S" if inv else "L", t=A["t"][i], te=A["t"][touch], tx=A["t"][x] + HR,
                                    Rg=Rg, rp=rp, hrs=hrs, R=Rg - (amd_fvg.FEE_LIMIT + FUND * hrs / 8) / rp, mfe=mfe, mae=mae, held=held, f=f))
                    last["limit"] = i
            # path A: confirmation at the touch bar's close
            if held and i - last["confirm"] >= 6 and touch + 1 < n - 1:
                e = touch + 1; ep = A["o"][e]; risk = ep - sl
                if risk > 0 and risk / abs(ep) >= P["mr"]:
                    tp = ep + P["tp"] * risk; Rg, x, mfe, mae = walk(A, e, ep, sl, tp, P["tmax"], False)
                    rp = risk / abs(ep); hrs = x - e + 1
                    out.append(dict(m="confirm", cfg=P["name"], sym=sym, side="S" if inv else "L", t=A["t"][i], te=A["t"][e], tx=A["t"][x] + HR,
                                    Rg=Rg, rp=rp, hrs=hrs, R=Rg - (14e-4 + FUND * hrs / 8) / rp, mfe=mfe, mae=mae, held=True, f=f))
                    last["confirm"] = i
    return out

def btc_ctx(inv):
    rows = series("BTC"); t_ = [r[0] for r in rows]; s = -1 if inv else 1
    o = [s * r[1] for r in rows]; h = [(-r[3] if inv else r[2]) for r in rows]; l = [(-r[2] if inv else r[3]) for r in rows]; c = [s * r[4] for r in rows]
    tr1d, _ = scen.daily_trend(t_, o, h, l, c)
    return {t_[k]: (tr1d[k], (c[k] - c[k - 24]) / abs(c[k - 24]) if k >= 24 else 0.0) for k in range(len(rows))}

CAUSES = ["vol_regime", "coin_1d", "btc_1d", "btc_24h", "relvol", "session", "weekday", "gap_atr", "risk_pct"]
CAT = {"coin_1d", "btc_1d", "session", "weekday"}

def lab_fn(train, name):
    if name in CAT: return lambda r: r["f"][name]
    v = sorted(r["f"][name] for r in train); q1, q2 = v[len(v) // 3], v[2 * len(v) // 3]
    return lambda r: 0 if r["f"][name] < q1 else 1 if r["f"][name] < q2 else 2

def learn_bad(train):
    bad = []
    for name in CAUSES:
        lab = lab_fn(train, name)
        for bk in set(lab(r) for r in train):
            a = [(r["t"], r["R"]) for r in train if lab(r) == bk]; b = [(r["t"], r["R"]) for r in train if lab(r) != bk]
            if len(a) < 60 or len(b) < 60: continue
            na, ma, ta = tstat(a); nb, mb, tb = tstat(b)
            sa = abs(ma / ta) if ta else 0; sb = abs(mb / tb) if tb else 0
            d = (ma - mb) / math.sqrt(sa ** 2 + sb ** 2) if sa or sb else 0
            if d <= -2: bad.append((name, bk, lab, round(ma - mb, 3), round(d, 1)))
    return bad

def show(label, x):
    n, m, t = tstat([(r["t"], r["R"]) for r in x]) if len(x) > 1 else (len(x), 0, 0)
    e1 = equity([(r["te"], r["sym"], r["R"], r["tx"]) for r in x], risk=0.01); e2 = equity([(r["te"], r["sym"], r["R"], r["tx"]) for r in x], risk=0.02)
    win = sum(r["R"] > 0 for r in x) / max(1, len(x)) * 100
    print(f"  {label:44s} n={n:5d} avg {m:+.3f}R t={t:+.1f} win {win:3.0f}% | $10 at 1% -> ${e1[0]:7.2f} ({e1[1]} trades, DD {e1[2]:.0f}%) | at 2% -> ${e2[0]:7.2f} ({e2[1]} trades, DD {e2[2]:.0f}%)")

if __name__ == "__main__":
    syms = sorted(f.split("_")[0] for f in os.listdir("d4") if f.endswith("_1H.json"))
    bL, bS = btc_ctx(False), btc_ctx(True)
    with Pool(4) as p: parts = p.map(job, [(s, inv, bS if inv else bL) for s in syms for inv in (False, True)])
    T = [r for P_ in parts for r in P_]
    yr = lambda r: time.gmtime(r["t"] / 1000).tm_year
    for cfg in ("AMD_NO_GATE", "AMD_PRIMARY"):
        base = [r for r in T if r["cfg"] == cfg and r["m"] == "limit"]; conf = [r for r in T if r["cfg"] == cfg and r["m"] == "confirm"]
        print(f"\n######## {cfg} (2021-06 .. 2026-09, all coins, corrected fill rule) ########")
        print("TRUTH 1 - DUKKHA (the loss, measured):")
        g = np.mean([r["Rg"] for r in base]); fe = np.mean([amd_fvg.FEE_LIMIT / r["rp"] for r in base]); fu = np.mean([FUND * r["hrs"] / 8 / r["rp"] for r in base])
        st = [r for r in base if r["Rg"] == -1.0]
        print(f"  gross {g:+.3f}R - fees {fe:.3f}R - funding {fu:.3f}R = net {np.mean([r['R'] for r in base]):+.3f}R  (n={len(base)}, median stop {np.median([r['rp'] for r in base])*100:.2f}%)")
        print(f"  stopped {len(st)/len(base)*100:.0f}%: straight to stop (MFE<0.3R) {sum(r['mfe'] < 0.3 for r in st)/len(st)*100:.0f}% | "
              f"0.3-1R first {sum(0.3 <= r['mfe'] < 1 for r in st)/len(st)*100:.0f}% | were +1R or more first {sum(r['mfe'] >= 1 for r in st)/len(st)*100:.0f}%")
        print("TRUTH 2 - SAMUDAYA (the cause): the gap is run through on the first touch")
        for lab, x in (("touch bar closed back above the gap bottom (held)", [r for r in base if r["held"]]),
                       ("touch bar closed below the gap bottom (failed)", [r for r in base if not r["held"]])):
            print(f"  {lab:52s} n={len(x):5d} avg {np.mean([r['R'] for r in x]):+.3f}R, stopped {sum(r['Rg'] == -1.0 for r in x)/len(x)*100:.0f}%, straight-to-stop {sum(r['mfe'] < 0.3 and r['Rg'] == -1.0 for r in x)/len(x)*100:.0f}%")
        print("TRUTH 3/4 path A - wait for the touch bar to close, enter only if the gap held (market, 14 bps):")
        show("baseline: resting limit (corrected)", base); show("confirm-at-close entry", conf)
        print("  by year (avg net R, n):  limit | confirm")
        for y in sorted(set(map(yr, base))):
            a = [r["R"] for r in base if yr(r) == y]; b = [r["R"] for r in conf if yr(r) == y]
            print(f"    {y}: {np.mean(a):+.3f} ({len(a)}) | {np.mean(b) if b else float('nan'):+.3f} ({len(b)})")
        print("TRUTH 3/4 path B - walk-forward cause filter (learned only from trades closed before each test year):")
        for m, X in (("limit", base), ("confirm", conf)):
            tot_f = []; tot_b = []
            for Y in (2023, 2024, 2025, 2026):
                y0 = ms(f"{Y}-01-01"); y1 = ms(f"{Y + 1}-01-01")
                train = [r for r in X if r["tx"] <= y0]; test = [r for r in X if y0 <= r["t"] < y1]
                assert all(r["tx"] <= y0 for r in train), "leak"
                bad = learn_bad(train)
                kept = [r for r in test if not any(lab(r) == bk for _, bk, lab, _, _ in bad)]
                tot_f += kept; tot_b += test
                print(f"    {m:7s} test {Y}: causes learned before {Y}: {[(nm, bk, d) for nm, bk, _, d, _ in bad] or 'none'} | "
                      f"unfiltered {np.mean([r['R'] for r in test]):+.3f}R (n={len(test)}) -> filtered {np.mean([r['R'] for r in kept]) if kept else float('nan'):+.3f}R (n={len(kept)})")
            show(f"{m}: walk-forward 2023-26, unfiltered", sorted(tot_b, key=lambda r: r["te"])); show(f"{m}: walk-forward 2023-26, filtered", sorted(tot_f, key=lambda r: r["te"]))
