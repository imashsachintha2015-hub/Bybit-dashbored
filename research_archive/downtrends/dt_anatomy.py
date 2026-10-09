"""Downtrend anatomy, DESCRIPTIVE, DEV data only (19 design coins, 2021-06-01 .. 2023-12-31, 4H; includes the 2022 bear market).
A  up-legs vs down-legs (big swings = zigzag 6 ATR, hindsight labels): size, duration, speed, deepest counter-move, volume
B  trend lines split by direction: uptrend lines (rising support) vs downtrend lines (falling resistance): tests and breaks
C  reversal detectors split: catching TOPS (start of downtrends) vs catching BOTTOMS
D  altcoin beta: how much alts move during BTC down-legs vs BTC up-legs
E  bear rallies: counter-rallies inside down-legs, how big, and what follows the lower high
F  capitulation: volume and lower wicks at bottoms vs tops vs ordinary bars"""
import os, sys, pickle, collections, time, calendar
os.environ["TL_WITH_D6"] = "1"
import numpy as np
from multiprocessing import Pool
import tl_core as T, tl_study as ST
ms = lambda d: calendar.timegm(time.strptime(d, "%Y-%m-%d")) * 1000
A0, A1 = ms("2021-06-01"), ms("2024-01-01")
DES = ST.DES

def legs_of(S):
    h, l, c, a, t = S["h"], S["l"], S["c"], S["F"]["atr"], S["t"]
    piv = [(k, i, p) for k, i, p, _ in T.zigzag(h, l, a, 6.0)]; out = []
    for (k1, i1, p1), (k2, i2, p2) in zip(piv, piv[1:]):
        if not (A0 <= t[i1] and t[i2] < A1): continue
        up = k1 == "L"; seg_h, seg_l = h[i1:i2 + 1], l[i1:i2 + 1]
        counter = (np.maximum.accumulate(seg_h) - seg_l).max() if up else (seg_h - np.minimum.accumulate(seg_l)).max()
        out.append(dict(up=up, i1=i1, i2=i2, size=abs(p2 - p1) / a[i1], dur=i2 - i1, pct=(p2 / p1 - 1) * 100, counter=counter / a[i1],
                        vol=float(np.mean(S["F"]["relvol"][i1:i2 + 1])), t1=int(t[i1]), t2=int(t[i2])))
    return out

if __name__ == "__main__":
    syms = sorted(f.split("_")[0] for f in os.listdir("d4") if f.endswith("_1H.json"))
    bL, bS = ST.btc_map(False), ST.btc_map(True)
    with Pool(4) as p: S_all = [s for s in p.map(ST.build, [(s, inv, bS if inv else bL) for s in syms for inv in (False, True)]) if s]
    pickle.dump(S_all, open("dt_feat.pkl", "wb"))
    L = [S for S in S_all if not S["inv"] and S["sym"] in DES]
    # ---------------- A
    legs = [g for S in L for g in legs_of(S)]
    print("=== A. up-legs vs down-legs (moves of at least 6 ATR, DEV) ===")
    print(f"  {'':10s} {'n':>5s} {'size ATR':>9s} {'size %':>8s} {'bars':>6s} {'ATR/bar':>8s} {'deepest counter-move ATR':>25s} {'rel. volume':>12s}")
    for nm, f in (("up-legs", True), ("down-legs", False)):
        x = [g for g in legs if g["up"] == f]
        print(f"  {nm:10s} {len(x):5d} {np.median([g['size'] for g in x]):9.1f} {np.median([abs(g['pct']) for g in x]):7.1f}% {np.median([g['dur'] for g in x]):6.0f} "
              f"{np.median([g['size'] / g['dur'] for g in x]):8.3f} {np.median([g['counter'] for g in x]):25.2f} {np.mean([g['vol'] for g in x]):12.2f}")
    print("  (medians; deepest counter-move = biggest bounce inside a down-leg / biggest dip inside an up-leg)")
    # ---------------- B
    print("\n=== B. trend lines by direction (DEV): uptrend lines (rising support) vs downtrend lines (falling resistance) ===")
    for inv, nm in ((False, "UPTREND lines (long side)"), (True, "DOWNTREND lines (short side)")):
        tests, breaks = [], []
        for S in S_all:
            if S["inv"] != inv or S["sym"] not in DES: continue
            F, t, c, o, n = S["F"], S["t"], S["c"], S["o"], len(S["t"])
            for i in range(200, n - 25):
                if not (A0 <= t[i] < A1): continue
                if F["sup_test"][i]:
                    up, dn = ST.mfe_mae(S, i, 12)
                    tests.append(dict(held=not F["sup_break"][i + 1:i + 7].any(), f24=ST.fwd(S, i, 24), up=up, rej=(c[i] > o[i]) and (c[i] > F["sup_y"][i])))
                if F["sup_break"][i]:
                    y, s_raw, a = F["sup_y"][i], F["sup_raw"][i], F["atr"][i]
                    breaks.append(dict(follow24=-ST.fwd(S, i, 24), back=any(c[j] > y + s_raw * (j - i) for j in range(i + 1, min(n, i + 4))),
                                       touches=F["sup_touches"][i]))
        print(f"  {nm:30s} tests n={len(tests):4d}: held 6 bars {np.mean([r['held'] for r in tests])*100:3.0f}%, trend continues {np.mean([r['f24'] for r in tests]):+.2f} ATR/24 bars, "
              f"with rejection candle {np.mean([r['f24'] for r in tests if r['rej']]):+.2f} | breaks n={len(breaks):4d}: follow-through {np.mean([r['follow24'] for r in breaks]):+.2f} ATR, "
              f"false {np.mean([r['back'] for r in breaks])*100:.0f}%, 3+ touch breaks {np.mean([r['follow24'] for r in breaks if r['touches'] >= 3]) if any(r['touches'] >= 3 for r in breaks) else float('nan'):+.2f} ATR (n={sum(r['touches'] >= 3 for r in breaks)})")
    print("  ('trend continues' = move in the line's own trend direction after a test; a downtrend line test is a rally up into falling resistance)")
    # ---------------- C
    print("\n=== C. how early the start of a DOWNTREND (a top) is detected vs the start of an UPTREND (a bottom) ===")
    for inv, nm in ((False, "tops -> downtrend starts"), (True, "bottoms -> uptrend starts")):
        rows = collections.defaultdict(list); tops = 0
        for S in S_all:
            if S["inv"] != inv or S["sym"] not in DES: continue
            F, t, h, l, c, a = S["F"], S["t"], S["h"], S["l"], S["c"], S["F"]["atr"]
            m0 = int(np.searchsorted(t, A0)); m1 = int(np.searchsorted(t, A1))
            if m1 - m0 < 400: continue                                  # coin not listed during DEV
            piv =[(k, i, p) for k, i, p, _ in T.zigzag(h[:m1], l[:m1], a[:m1], 6.0) if i >= m0]
            lows = [(ci, p) for k, i_, p, ci in S["sw"] if k == "L"]; lastlow = np.full(m1, np.nan); j = 0; cur = np.nan
            for i in range(m1):
                while j < len(lows) and lows[j][0] <= i: cur = lows[j][1]; j += 1
                lastlow[i] = cur
            for (k1, i1, p1), (k2, i2, p2) in zip(piv, piv[1:]):
                if k1 != "H": continue
                tops += 1; D = (p1 - p2) / a[i1]
                for dn, sig in (("trend line break", lambda i: F["sup_break"][i]), ("CHoCH", lambda i: c[i] < lastlow[i - 1] <= c[i - 1]),
                                ("Kalman flip", lambda i: F["kal"][i] < 0 <= F["kal"][i - 1]), ("optimized line break", lambda i: c[i] < F["opt48_sup"][i] - T.DELTA * a[i])):
                    hits = [i for i in range(i1 + 1, i2 + 1) if sig(i)]
                    if hits: i = hits[0]; rows[dn].append(((p1 - c[i]) / a[i1], (c[i] - p2) / a[i1], D, i - i1))
        print(f"  {nm} ({tops} events, following move median {np.median([r[2] for v in rows.values() for r in v]):.1f} ATR):")
        for dn, v in rows.items():
            print(f"    {dn:22s} caught {len(v)/max(1,tops)*100:3.0f}% | lag {np.median([r[3] for r in v]):3.0f} bars | gave up {np.mean([r[0] for r in v]):.2f} ATR | left {np.mean([r[1] for r in v]):.2f} ATR | capture {np.mean([r[1]/r[2] for r in v])*100:3.0f}%")
    # ---------------- D
    print("\n=== D. altcoin moves during BTC's big legs (DEV): % move of the alt / % move of BTC ===")
    btc = next(S for S in L if S["sym"] == "BTC"); bl = legs_of(btc); closes = {S["sym"]: dict(zip(S["t"].tolist(), S["c"].tolist())) for S in L}
    for up, nm in ((True, "BTC up-legs"), (False, "BTC down-legs")):
        ratios = []; alt_moves = []; btc_moves = []
        for g in [g for g in bl if g["up"] == up]:
            for sym, cc in closes.items():
                if sym == "BTC" or g["t1"] not in cc or g["t2"] not in cc: continue
                am = (cc[g["t2"]] / cc[g["t1"]] - 1) * 100; alt_moves.append(am); btc_moves.append(g["pct"]); ratios.append(am / g["pct"])
        print(f"  {nm:14s} legs {sum(1 for g in bl if g['up'] == up):3d} | BTC median {np.median(btc_moves):+6.1f}% | alts median {np.median(alt_moves):+6.1f}% | "
              f"alt/BTC ratio median {np.median(ratios):.2f} | alts moved MORE than BTC in {np.mean([abs(a_) > abs(b_) for a_, b_ in zip(alt_moves, btc_moves)])*100:.0f}% of cases")
    # ---------------- E
    print("\n=== E. bear rallies: 3-ATR swing highs INSIDE down-legs (lower highs) and what follows their confirmation ===")
    rallies = []
    for S in L:
        h, l, c, a, sw = S["h"], S["l"], S["c"], S["F"]["atr"], S["sw"]
        for g in [g for g in legs_of(S) if not g["up"]]:
            hs = [(i_, p, ci) for k, i_, p, ci in sw if k == "H" and g["i1"] < i_ < g["i2"] and ci < g["i2"]]
            ls = [(i_, p) for k, i_, p, ci in sw if k == "L" and g["i1"] <= i_ < g["i2"]]
            for i_, p, ci in hs:
                prev_low = max([q for q in ls if q[0] < i_], default=None)
                if prev_low is None or ci + 25 >= len(c): continue
                rallies.append(dict(size=(p - prev_low[1]) / a[i_], f24=(c[ci + 24] - c[ci]) / a[ci], f6=(c[ci + 6] - c[ci]) / a[ci], bars=i_ - prev_low[0]))
    if rallies:
        print(f"  {len(rallies)} bear rallies: median size {np.median([r['size'] for r in rallies]):.1f} ATR over {np.median([r['bars'] for r in rallies]):.0f} bars; "
              f"after the lower high is confirmed, price moves {np.mean([r['f6'] for r in rallies]):+.2f} ATR in 6 bars and {np.mean([r['f24'] for r in rallies]):+.2f} ATR in 24 bars "
              f"(negative = the downtrend resumes); resumes down in 24 bars {np.mean([r['f24'] < 0 for r in rallies])*100:.0f}% of the time")
    # ---------------- F
    print("\n=== F. capitulation: the bar of the bottom vs the bar of the top vs ordinary bars ===")
    tb, bb, allb = [], [], []
    for S in L:
        o, h, l, c, rv = S["o"], S["h"], S["l"], S["c"], S["F"]["relvol"]
        for g in legs_of(S):
            i = g["i2"]; rng = max(h[i] - l[i], 1e-12)
            rec = dict(rv=rv[i], lw=(min(o[i], c[i]) - l[i]) / rng, uw=(h[i] - max(o[i], c[i])) / rng)
            (bb if not g["up"] else tb).append(rec)
        for i in range(300, len(c), 9):
            rng = max(h[i] - l[i], 1e-12); allb.append(dict(rv=rv[i], lw=(min(o[i], c[i]) - l[i]) / rng, uw=(h[i] - max(o[i], c[i])) / rng))
    for nm, x in (("bottoms (end of down-legs)", bb), ("tops (end of up-legs)", tb), ("ordinary bars", allb)):
        print(f"  {nm:28s} n={len(x):5d} rel. volume {np.mean([r['rv'] for r in x]):.2f}x | lower wick {np.mean([r['lw'] for r in x])*100:3.0f}% of range | upper wick {np.mean([r['uw'] for r in x])*100:3.0f}% | "
              f"volume >= 2x on {np.mean([r['rv'] >= 2 for r in x])*100:3.0f}% of them")
