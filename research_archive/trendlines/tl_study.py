"""Part 1 + 2 of the trend-line study, DESCRIPTIVE, on DEV data only (19 design coins, 2021-06 .. 2023-12, 4H).
Trend line = SUP line of each orientation: the uptrend line (long side) and, on mirrored prices, the downtrend line. 'Up' always
means 'in the direction of the trend the line belongs to'. All moves in ATR(14) units; 14 bps of fees is about 0.07-0.1 ATR on 4H.
Part 1: how price acts at a trend line: tests (continuation), breaks (reversal), false breaks, retests, acceleration.
Part 2: trend reversals: big swings (zigzag 6 ATR, hindsight labels only) and how early / how wrong each reversal detector is."""
import os, sys, pickle, math, collections, time, calendar
import numpy as np
from multiprocessing import Pool
import tl_core as T
ms = lambda d: calendar.timegm(time.strptime(d, "%Y-%m-%d")) * 1000
DES = set("AAVE AVAX BNB BTC CRV DOGE FIL HBAR ICP NEAR OP POL SOL STX SUI TRX UNI WLD XRP".split())
DEV_END, VAL_END = ms("2024-01-01"), ms("2025-01-01")

def build(args):
    sym, inv, btc = args
    d = T.load4h(sym, inv)
    if d is None or len(d[0]) < 800: return None
    t, o, h, l, c, v = d; F, sw = T.features(t, o, h, l, c, v)
    F["e20"], F["e50"] = T.ema(c, 20), T.ema(c, 50)
    F["btc1d"] = np.array([btc.get(int(x), 0) for x in t]) if btc is not None else F["tr1d"]
    return dict(sym=sym, inv=inv, t=t, o=o, h=h, l=l, c=c, v=v, F=F, sw=sw)

def btc_map(inv):
    t, o, h, l, c, v = T.load4h("BTC", inv); return dict(zip(t.tolist(), T.daily_trend(t, c).tolist()))

def fwd(S, i, k):
    c, a = S["c"], S["F"]["atr"][i]; j = min(len(c) - 1, i + k); return (c[j] - c[i]) / a

def mfe_mae(S, i, k):
    h, l, c, a = S["h"], S["l"], S["c"], S["F"]["atr"][i]; j = min(len(c), i + 1 + k)
    if j <= i + 1: return 0.0, 0.0
    return (h[i + 1:j].max() - c[i]) / a, (c[i] - l[i + 1:j].min()) / a

def table(title, rows, groups, cols):
    print(f"\n{title}")
    print(f"  {'group':34s} {'n':>6s} " + " ".join(f"{c_[0]:>12s}" for c_ in cols))
    for gname, gf in groups:
        x = [r for r in rows if gf(r)]
        if len(x) < 30: continue
        print(f"  {gname:34s} {len(x):6d} " + " ".join(f"{c_[1](x):12s}" for c_ in cols))

pct = lambda key: (lambda x: f"{np.mean([r[key] for r in x]) * 100:10.0f}%")
avg = lambda key: (lambda x: f"{np.mean([r[key] for r in x]):+12.2f}")

if __name__ == "__main__":
    syms = sorted(f.split("_")[0] for f in os.listdir("d4") if f.endswith("_1H.json"))
    bL, bS = btc_map(False), btc_map(True)
    with Pool(4) as p: S_all = [s for s in p.map(build, [(s, inv, bS if inv else bL) for s in syms for inv in (False, True)]) if s]
    pickle.dump(S_all, open("tl_feat.pkl", "wb"))
    print(f"series built: {len(S_all)} (coins x 2 orientations); 4H bars per full series ~{max(len(s['t']) for s in S_all)}")
    dev = [s for s in S_all if s["sym"] in DES]
    tests, breaks, base = [], [], []
    for S in dev:
        F = S["F"]; t = S["t"]; n = len(t); c, o = S["c"], S["o"]
        for i in range(200, n - 25):
            if t[i] >= DEV_END: break
            a = F["atr"][i]
            if i % 6 == 0: base.append(dict(f24=fwd(S, i, 24), f6=fwd(S, i, 6)))
            if F["sup_test"][i]:
                y = F["sup_y"][i]; held = not F["sup_break"][i + 1:i + 7].any()
                up, dn = mfe_mae(S, i, 12)
                tests.append(dict(prior=F["sup_tests"][i], slope=F["sup_slope"][i], age=F["sup_age"][i], tr=F["tr1d"][i], btc=F["btc1d"][i],
                                  rv=F["relvol"][i], held=held, up=up, dn=dn, f6=fwd(S, i, 6), f24=fwd(S, i, 24), touches=F["sup_touches"][i],
                                  rej=(c[i] > o[i]) and (c[i] > y)))
            if F["sup_break"][i]:
                y = F["sup_y"][i]; slope = F["sup_slope"][i]; s_raw = F["sup_raw"][i]
                back = any(c[j] > y + s_raw * (j - i) for j in range(i + 1, min(n, i + 4)))          # close back above the (extended) line
                retest = any(S["h"][j] >= y + s_raw * (j - i) - 0.1 * a for j in range(i + 1, min(n, i + 13)))
                up, dn = mfe_mae(S, i, 24)
                breaks.append(dict(prior=F["sup_tests"][i], touches=F["sup_touches"][i], slope=slope, accel=F["sup_accel"][i], age=F["sup_age"][i],
                                   rv=F["relvol"][i], body=abs(c[i] - o[i]) / a, tr=F["tr1d"][i], back=back, retest=retest,
                                   follow6=-fwd(S, i, 6), follow24=-fwd(S, i, 24), dn=dn, up=up))
    b24 = np.mean([r["f24"] for r in base]); b6 = np.mean([r["f6"] for r in base])
    print(f"\nDEV baseline (every 6th bar): fwd 6 bars {b6:+.3f} ATR, fwd 24 bars {b24:+.3f} ATR | events: {len(tests)} tests, {len(breaks)} breaks")
    print("(all moves in ATR; 'up' = direction of the trend the line belongs to; fees ~0.07-0.1 ATR per round trip)")
    sl = sorted(r["slope"] for r in tests); s1, s2 = sl[len(sl) // 3], sl[2 * len(sl) // 3]
    table("PART 1a - TESTS of a trend line (price comes back to the line): does it hold and bounce?", tests,
          [("all tests", lambda r: True), ("1st test (3rd touch)", lambda r: r["prior"] == 0), ("2nd test", lambda r: r["prior"] == 1),
           ("3rd+ test", lambda r: r["prior"] >= 2), (f"flat line (slope<{s1:.2f} ATR/bar)", lambda r: r["slope"] < s1),
           ("medium slope", lambda r: s1 <= r["slope"] < s2), (f"steep line (>={s2:.2f})", lambda r: r["slope"] >= s2),
           ("with 1D trend", lambda r: r["tr"] == 1), ("against 1D trend", lambda r: r["tr"] == -1),
           ("BTC aligned", lambda r: r["btc"] == 1), ("rejection candle (close>open & line)", lambda r: r["rej"]),
           ("1st test + with 1D trend + rejection", lambda r: r["prior"] == 0 and r["tr"] == 1 and r["rej"])],
          [("held 6 bars", pct("held")), ("bounce MFE12", avg("up")), ("drawdn MAE12", avg("dn")), ("fwd6", avg("f6")), ("fwd24", avg("f24"))])
    table("PART 1b - BREAKS of a trend line (close beyond by 0.25 ATR): follow-through in the break direction", breaks,
          [("all breaks", lambda r: True), ("2-touch line", lambda r: r["touches"] <= 2), ("3+ touch line", lambda r: r["touches"] >= 3),
           ("line tested 0 times", lambda r: r["prior"] == 0), ("line tested 2+ times", lambda r: r["prior"] >= 2),
           ("high volume break (>=1.5x)", lambda r: r["rv"] >= 1.5), ("low volume break (<1x)", lambda r: r["rv"] < 1.0),
           ("strong candle (body>=1 ATR)", lambda r: r["body"] >= 1.0), ("accelerated line (slope>=2x prev)", lambda r: r["accel"] >= 2),
           ("break against 1D trend (reversal)", lambda r: r["tr"] == 1), ("break with 1D trend", lambda r: r["tr"] == -1),
           ("old line (age >= 30 bars)", lambda r: r["age"] >= 30), ("young line (age < 10)", lambda r: r["age"] < 10)],
          [("follow 6", avg("follow6")), ("follow 24", avg("follow24")), ("false brk", pct("back")), ("retest", pct("retest")),
           ("MFE24 dn", avg("dn")), ("MAE24 up", avg("up"))])
    print(f"  (reference: an average bar moves {-b6:+.3f} / {-b24:+.3f} ATR 'down' over 6 / 24 bars)")

    # ---------------- PART 2: reversals
    print("\n================ PART 2 - TREND REVERSALS (big swings = zigzag 6 ATR, labels use hindsight; detectors use none) ================")
    det_names = ["D1 trend line break", "D2 3+touch line break", "D3 CHoCH (close < last swing low)", "D4 optimized line break (48)",
                 "D5 Kalman trend flips down", "D6 EMA20 < EMA50 cross", "D7 accelerated line break"]
    stats = {d_: dict(sig=0, right=0, first=[], fwd=[]) for d_ in det_names}; tops = 0; legs = []
    for S in dev:
        F = S["F"]; t = S["t"]; n = len(t); c, h, l = S["c"], S["h"], S["l"]; a = F["atr"]
        m = int(np.searchsorted(t, DEV_END))
        if m < 400: continue                                   # coin not listed during the DEV period
        big = T.zigzag(h[:m], l[:m], a[:m], 6.0)
        piv = [(k_, i_, p_) for k_, i_, p_, _ in big]
        down_leg = np.zeros(m, bool)
        for (k1, i1, p1), (k2, i2, p2) in zip(piv, piv[1:]):
            if k1 == "H": down_leg[i1 + 1:i2 + 1] = True
        lows = [(ci, p_) for k_, i_, p_, ci in S["sw"] if k_ == "L"]
        sig = {d_: np.zeros(m, bool) for d_ in det_names}
        lastlow = np.full(m, np.nan); j = 0; cur = np.nan
        for i in range(m):
            while j < len(lows) and lows[j][0] <= i: cur = lows[j][1]; j += 1
            lastlow[i] = cur
        for i in range(201, m):
            sig["D1 trend line break"][i] = F["sup_break"][i]
            sig["D2 3+touch line break"][i] = F["sup_break"][i] and F["sup_touches"][i] >= 3
            sig["D3 CHoCH (close < last swing low)"][i] = c[i] < lastlow[i - 1] and c[i - 1] >= lastlow[i - 1]
            sig["D4 optimized line break (48)"][i] = c[i] < F["opt48_sup"][i] - T.DELTA * a[i] and c[i - 1] >= F["opt48_sup"][i - 1] - T.DELTA * a[i - 1]
            sig["D5 Kalman trend flips down"][i] = F["kal"][i] < 0 <= F["kal"][i - 1]
            sig["D6 EMA20 < EMA50 cross"][i] = F["e20"][i] < F["e50"][i] and F["e20"][i - 1] >= F["e50"][i - 1]
            sig["D7 accelerated line break"][i] = F["sup_break"][i] and F["sup_accel"][i] >= 2
        lo_i, hi_i = (piv[0][1], piv[-1][1]) if len(piv) >= 2 else (m, m)     # only bars inside labelled legs
        for d_ in det_names:
            idx = np.where(sig[d_])[0]; idx = idx[(idx > lo_i) & (idx <= hi_i) & (idx < m - 25)]
            stats[d_]["sig"] += len(idx); stats[d_]["right"] += int(down_leg[idx].sum())
            stats[d_]["fwd"] += [-(c[i + 24] - c[i]) / a[i] for i in idx]
        for (k1, i1, p1), (k2, i2, p2) in zip(piv, piv[1:]):
            if k1 != "H" or i2 >= m - 1: continue
            tops += 1; D = (p1 - p2) / a[i1]; legs.append(D)
            for d_ in det_names:
                hits = [i for i in range(i1 + 1, i2 + 1) if sig[d_][i]]
                if hits:
                    i = hits[0]; stats[d_]["first"].append((i - i1, (p1 - c[i]) / a[i1], (c[i] - p2) / a[i1], D))
    print(f"tops (end of up-legs >= 6 ATR) in DEV: {tops}; average following down-leg {np.mean(legs):.1f} ATR (median {np.median(legs):.1f})")
    print(f"  {'detector':36s} {'signals':>8s} {'right side':>10s} {'fwd24 in signal dir':>20s} {'tops caught':>11s} {'lag bars':>9s} {'gave up':>8s} {'left to catch':>13s} {'capture':>8s}")
    for d_ in det_names:
        s_ = stats[d_]; fs = s_["first"]
        if not s_["sig"]: continue
        print(f"  {d_:36s} {s_['sig']:8d} {s_['right']/s_['sig']*100:9.0f}% {np.mean(s_['fwd']):+19.3f} {len(fs)/max(1,tops)*100:10.0f}% "
              f"{np.median([x[0] for x in fs]) if fs else 0:9.0f} {np.mean([x[1] for x in fs]) if fs else 0:8.2f} {np.mean([x[2] for x in fs]) if fs else 0:13.2f} "
              f"{np.mean([x[2] / x[3] for x in fs]) * 100 if fs else 0:7.0f}%")
    print("  right side = signal fired during a (hindsight) down-leg; gave up / left to catch = ATR from the top to the signal / from the signal to the next bottom")
