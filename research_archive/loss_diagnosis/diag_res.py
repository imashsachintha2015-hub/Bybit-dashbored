"""Truth 1 (Dukkha) + Truth 2 (Samudaya) for the resonance rebuild (resonance_test/res.py).
Truth 1: measure every loss - gross direction edge vs fees vs funding, and how the losers died (MFE / MAE in R).
Truth 2: candidate causes known BEFORE entry (declared in advance), net R by tercile on DEV; a cause is accepted only if its worst
bucket is clearly worse than the rest on DEV (difference t <= -2) AND still worse than the rest on VAL. Design coins only."""
import os, sys, json, pickle, math, collections, statistics
import numpy as np
from multiprocessing import Pool
REPO = os.environ.get("REPO", "/home/user/Bybit-dashbored")
for _d in ("resonance_test", "scenario_research"): sys.path.insert(1, f"{REPO}/research_archive/{_d}")
import res
from prereg_run import tstat

A = set("BTC ETH SOL XRP DOGE BNB ADA AVAX LINK DOT LTC NEAR".split())
_b = json.load(open("s15/BTC_15m.json")); t0, t1 = _b[0][0], _b[-1][0]; T1 = t0 + (t1 - t0) * .5; T2 = t0 + (t1 - t0) * .75
BAR, HR, DAY = res.BAR, res.HR, res.DAY; FUND_8H = 0.5e-4; FEE = 14e-4

def daily_ctx(t, h, l, c):
    """1D EMA50 and 1D ATR(14) of the last CLOSED day at each 15m bar (for overextension)."""
    k = DAY // BAR; bid = t // DAY
    ub, st, cnt = np.unique(bid, return_index=True, return_counts=True); keep = cnt == k; ub = ub[keep]; st = st[keep]
    H = np.array([h[s:s + k].max() for s in st]); L = np.array([l[s:s + k].min() for s in st]); C = c[st + k - 1]
    pc = np.concatenate([[C[0]], C[:-1]]); tr = np.maximum(H - L, np.maximum(abs(H - pc), abs(L - pc)))
    e50 = res.ema(C, 50); atr = res.rma(tr, 14)
    idx = np.searchsorted((ub + 1) * DAY, t + BAR, side="right") - 1
    ok = idx >= 50
    return np.where(ok, e50[np.maximum(idx, 0)], np.nan), np.where(ok, atr[np.maximum(idx, 0)], np.nan)

def excursions(h, l, e, ep, stop, risk, k=2.25):
    """walk the 2.25R fixed-target path: MFE (best move for us) and MAE (worst move against us) in R, up to the exit.
    The exit bar's ambiguous side is excluded (its high on a stop bar, its low on a target bar)."""
    n = len(h); tp = ep + k * risk; mfe = 0.0; mae = 0.0
    for j in range(e, min(n, e + res.HOLD)):
        if l[j] <= stop: return mfe, max(mae, 1.0), "stop"
        if h[j] >= tp: return max(mfe, k), mae, "target"
        mfe = max(mfe, (h[j] - ep) / risk); mae = max(mae, (ep - l[j]) / risk)
    return mfe, mae, "timeout"

def btc_ctx(inv):
    t, o, h, l, c, v = res.load("BTC", inv); F = res.features(t, o, h, l, c, v)
    c96 = np.concatenate([np.full(96, np.nan), c[:-96]])
    return dict(zip(t.tolist(), zip(F["tr_1D"].tolist(), ((c - c96) / np.abs(c96)).tolist())))

def worker(args):
    sym, inv, btc = args
    t, o, h, l, c, v = res.load(sym, inv); n = len(t)
    if n < 5000: return []
    F = res.features(t, o, h, l, c, v)
    e50d, atrd = daily_ctx(t, h, l, c)
    atrp = F["atr"] / np.abs(c); cs = np.concatenate([[0.0], np.cumsum(atrp)]); W = 2880
    vreg = np.array([atrp[i] / ((cs[i] - cs[i - W]) / W) if i >= W else np.nan for i in range(n)])
    cv = np.concatenate([[0.0], np.cumsum(v)])
    rows = []
    for i in range(1, n - 2):
        if not F["ready"][i] or not (F["choch"][i] or F["emax"][i]): continue
        e = i + 1; ep = o[e]; stop = F["stop_base"][i]
        if ep - stop < res.MINSTOP * abs(ep): stop = ep - res.MINSTOP * abs(ep)
        risk = ep - stop; rp = risk / abs(ep)
        out = res.outcomes(t, h, l, c, e, ep, stop, risk)
        mfe, mae, how = excursions(h, l, e, ep, stop, risk)
        bt = btc.get(int(t[i]), (0, float("nan")))
        hr = int((t[i] // HR) % 24)
        feats = dict(stop_pct=rp * 100, vol_regime=vreg[i], coin_1d=int(F["tr_1D"][i]), btc_1d=int(bt[0]), agree=int(F["res"][i]),
                     overext=(c[i] - e50d[i]) / atrd[i] if atrd[i] == atrd[i] and atrd[i] > 0 else np.nan,
                     relvol=v[i] / max((cv[i] - cv[max(0, i - 96)]) / max(1, min(96, i)), 1e-12),
                     session="Asia 00-07" if hr < 7 else "London 07-13" if hr < 13 else "NY 13-21" if hr < 21 else "late 21-24",
                     weekday=int(((t[i] // DAY) + 3) % 7), btc_24h=bt[1], adx=float(F["adx"][i]))
        for kind in ("choch", "ema"):
            if (kind == "choch" and F["choch"][i]) or (kind == "ema" and F["emax"][i]):
                rows.append(dict(kind=kind, sym=sym, side="S" if inv else "L", t=int(t[i]), res=int(F["res"][i]), adx=float(F["adx"][i]),
                                 rp=rp, out=out, mfe=mfe, mae=mae, how=how, f=feats))
    return rows

def dedupe(rows, x):
    rows = sorted(rows, key=lambda r: r["out"][x][2]); last = {}; keep = []
    for r in rows:
        k = (r["sym"], r["side"])
        if r["out"][x][2] < last.get(k, 0): continue
        last[k] = r["out"][x][3]; keep.append(r)
    return keep

def netR(r, x, fee=FEE): R, hrs, te, tx = r["out"][x]; return R - (fee + FUND_8H * hrs / 8) / r["rp"]

def anatomy(rows, x, label):
    g = [r["out"][x][0] for r in rows]; fee = [FEE / r["rp"] for r in rows]; fund = [FUND_8H * r["out"][x][1] / 8 / r["rp"] for r in rows]
    net = [a - b - c_ for a, b, c_ in zip(g, fee, fund)]; n = len(rows)
    print(f"\n[{label}] n={n}  gross {np.mean(g):+.3f}R  - fees {np.mean(fee):.3f}R  - funding {np.mean(fund):.3f}R  = net {np.mean(net):+.3f}R"
          f"   (median stop {np.median([r['rp'] for r in rows])*100:.2f}%, median fee {np.median(fee):.2f}R)")
    if x == 2:
        hows = collections.Counter(r["how"] for r in rows)
        st = [r for r in rows if r["how"] == "stop"]; tg = [r for r in rows if r["how"] == "target"]
        straight = sum(r["mfe"] < 0.3 for r in st); partial = sum(0.3 <= r["mfe"] < 1.0 for r in st); gave = sum(r["mfe"] >= 1.0 for r in st)
        print(f"   exits: target {hows['target']/n*100:.0f}%  stop {hows['stop']/n*100:.0f}%  timeout {hows['timeout']/n*100:.0f}%")
        print(f"   how the stopped trades died: straight to stop (MFE<0.3R) {straight/max(1,len(st))*100:.0f}% | moved 0.3-1R first {partial/max(1,len(st))*100:.0f}%"
              f" | were +1R or more, then stopped {gave/max(1,len(st))*100:.0f}%")
        print(f"   winners that first came within 0.2R of the stop (MAE>=0.8R): {sum(r['mae'] >= 0.8 for r in tg)/max(1,len(tg))*100:.0f}%"
              f" | MFE reached by stopped trades: median {np.median([r['mfe'] for r in st]):.2f}R")

def bucketize(rows, name):
    vals = [r["f"][name] for r in rows]
    if isinstance(vals[0], str) or name in ("coin_1d", "btc_1d", "weekday", "agree"):
        return lambda r: r["f"][name], sorted(set(vals), key=lambda z: str(z))
    clean = sorted(v for v in vals if v == v)
    q1, q2 = clean[len(clean) // 3], clean[2 * len(clean) // 3]
    lab = lambda r: "nan" if r["f"][name] != r["f"][name] else ("low" if r["f"][name] < q1 else "mid" if r["f"][name] < q2 else "high")
    return lab, ["low", "mid", "high"], (q1, q2)

def diff_t(a_pairs, b_pairs):
    na, ma, ta = tstat(a_pairs); nb, mb, tb = tstat(b_pairs)
    sa = abs(ma / ta) if ta else 0; sb = abs(mb / tb) if tb else 0
    return ma - mb, (ma - mb) / math.sqrt(sa ** 2 + sb ** 2) if sa or sb else 0.0

CAUSES = ["stop_pct", "vol_regime", "coin_1d", "btc_1d", "agree", "overext", "relvol", "session", "weekday", "btc_24h", "adx"]

if __name__ == "__main__":
    btcL, btcS = btc_ctx(False), btc_ctx(True)
    syms = sorted(f.split("_")[0] for f in os.listdir("s15") if f.endswith("_15m.json"))
    with Pool(4) as p: parts = p.map(worker, [(s, inv, btcS if inv else btcL) for s in syms for inv in (False, True)])
    rows = [r for P_ in parts for r in P_]
    # ---- parity with res_ev.pkl (the events the resonance test used) + excursion sanity
    ev = pickle.load(open("res_ev.pkl", "rb"))["ev"]
    ref = {(e["kind"], e["sym"], e["side"], e["t"]): e["out"] for e in ev if e["kind"] in ("choch", "ema")}
    mine = {(r["kind"], r["sym"], r["side"], r["t"]): r["out"] for r in rows}
    print(f"parity with res_ev.pkl: {len(mine)} vs {len(ref)} trigger events, identical keys {mine.keys() == ref.keys()}, "
          f"identical outcomes {all(mine[k] == ref[k] for k in ref)}")
    bad = sum(1 for r in rows if (r["how"] == "target" and r["mfe"] < 2.25) or (r["how"] == "stop" and r["mae"] < 1.0)
              or (r["how"] == "target") != (r["out"][2][0] == 2.25))
    print(f"excursion sanity (target => MFE>=2.25R, stop => MAE>=1R, same exit as the simulator): {bad} violations")
    pickle.dump(rows, open("diag_res.pkl", "wb"))

    dev = [r for r in rows if r["sym"] in A and r["t"] < T1]; val = [r for r in rows if r["sym"] in A and T1 <= r["t"] < T2]
    print("\n================ TRUTH 1 - DUKKHA: what the losses are made of (DEV, design coins, first half of the year) ================")
    for kind in ("choch", "ema"):
        P = dedupe([r for r in dev if r["kind"] == kind and r["res"] >= 3], 2)
        anatomy(P, 2, f"{kind} trigger, >=3 timeframes agree, exit all at 2.25R")
    vid = dedupe([r for r in dev if r["kind"] == "choch" and r["res"] == 5 and r["adx"] >= 25], 0)
    anatomy(vid, 0, "video-like: CHoCH, all 5 agree, ADX>=25, TP1/TP2/TP3 ladder")
    anatomy(dedupe([r for r in dev if r["kind"] == "choch" and r["res"] == 5 and r["adx"] >= 25], 2), 2, "video-like entries, exit all at 2.25R")

    print("\n================ TRUTH 2 - SAMUDAYA: which condition known BEFORE entry goes with the losses (DEV; confirmed on VAL) ================")
    accepted = []
    for kind in ("choch", "ema"):
        P = dedupe([r for r in dev if r["kind"] == kind and r["res"] >= 3], 2); V = dedupe([r for r in val if r["kind"] == kind and r["res"] >= 3], 2)
        print(f"\n-- {kind} trigger (>=3 agree, 2.25R exit): DEV n={len(P)} avg net {np.mean([netR(r, 2) for r in P]):+.3f}R | VAL n={len(V)}")
        for name in CAUSES:
            bz = bucketize(P, name); lab, buckets = bz[0], bz[1]
            cells = []
            for bk in buckets:
                a = [(r["t"], netR(r, 2)) for r in P if lab(r) == bk]; rest = [(r["t"], netR(r, 2)) for r in P if lab(r) != bk]
                if len(a) < 30: continue
                d, dt = diff_t(a, rest)
                va = [netR(r, 2) for r in V if lab(r) == bk]; vr = [netR(r, 2) for r in V if lab(r) != bk]
                vd = (np.mean(va) - np.mean(vr)) if va and vr else float("nan")
                g = np.mean([r["out"][2][0] for r in P if lab(r) == bk]); fe = np.mean([FEE / r["rp"] for r in P if lab(r) == bk])
                cells.append((bk, len(a), np.mean([x[1] for x in a]), g, fe, d, dt, vd))
            if not cells: continue
            worst = min(cells, key=lambda z: z[6])
            ok = worst[6] <= -2 and worst[7] == worst[7] and worst[7] < 0
            if ok: accepted.append((kind, name, worst[0]))
            cut = f" cuts {bz[2][0]:.3g}/{bz[2][1]:.3g}" if len(bz) > 2 else ""
            print(f"  {name:10s}{cut:18s} " + " | ".join(f"{bk}: {m:+.2f}R (gross {g:+.2f}, fee {fe:.2f}, n={n_})" for bk, n_, m, g, fe, d, dt, vd in cells)
                  + f"  -> worst '{worst[0]}' vs rest {worst[5]:+.2f}R t={worst[6]:+.1f}, VAL {worst[7]:+.2f}R {'ACCEPTED' if ok else ''}")
    print(f"\naccepted causes (DEV t<=-2 and still worse on VAL): {accepted}")
    pickle.dump(accepted, open("diag_res_causes.pkl", "wb"))
