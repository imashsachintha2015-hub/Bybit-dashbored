"""Tests 2 and 3 of PREREG_situations.md (committed before running): managing a trade in profit (paired, the same signals
with a different exit) and crashes / fast markets (stress profile of the standard exit)."""
import math, time, collections
import numpy as np
import sit_core as C
from ls_improve import YEARS, year
H4 = C.H4

STD = {(x[1], x[4], x[5]): x for x in C.FINAL}
def per_signal(**kw):
    """{(sym, t0, side): (R, te, tx)} for the system's 3,080 signals under one variant"""
    return {k: C.simulate(k[0], k[2], k[1], **kw) for k in STD}

def stats(vals):
    v = np.array(vals); return dict(mean=v.mean(), med=np.median(v), win=(v > 0).mean() * 100, big=(v >= 3).mean() * 100, worst=v.min())

base_line = C.port_line("standard exit", C.FINAL)
def judge_port(L):
    e1, n1, d1 = L["all"]; e2, n2, d2 = L["recent"]; b1, b2 = base_line["all"], base_line["recent"]
    return (d1 > b1[2] and d2 > b2[2]), (C.mar(e1, d1) > C.mar(*b1[::2]) and C.mar(e2, d2) > C.mar(*b2[::2]))

# ============================================================== test 2
VARIANTS = {
    "PART_2R":   dict(targets=((2, 1 / 3),)),
    "PART_3R":   dict(targets=((3, 1 / 2),)),
    "BE_15":     dict(be_at=1.5),
    "TRAIL":     dict(trail_at=2.0),
    "TP3":       dict(full_tp=3),
    "TP5":       dict(full_tp=5),
    "LADDER":    dict(targets=((2, 1 / 3), (4, 1 / 3))),
    "LADDER_BE": dict(targets=((2, 1 / 3), (4, 1 / 3)), be_after_first=True),
}
print(f"parity: standard exit ${base_line['all'][0]:.2f} ({base_line['all'][1]} trades)")
det = {k: C.simulate(k[0], k[2], k[1], detail=True) for k in STD}
give = [k for k, (o_, parts, mfe, rp) in det.items() if mfe >= 2 and o_[0] <= 0]
print(f"standard exit, all 3,080 signals: {sum(1 for k, v in det.items() if v[2] >= 2)} reached +2R at some point; "
      f"{len(give)} of those still closed at or below 0R (gave the move back)")
s0 = stats([v[0] for v in STD.values() for v in [v[2]]]) if False else stats([x[2] for x in C.FINAL])
print(f"standard exit: mean {s0['mean']:+.3f}R, median {s0['med']:+.3f}R, winners {s0['win']:.0f}%, trades of +3R or more {s0['big']:.1f}%\n")

print("=== TEST 2: managing a trade in profit - paired difference per signal (variant minus standard), net R ===")
print(f"  {'variant':10s} | " + " | ".join(f"{sg:>15s}" for sg in C.SEGS) + " | pooled holdouts        | mean   median  win%  >=3R%  | verdict")
res2 = {}; lines2 = []
for name, kw in VARIANTS.items():
    V = per_signal(**kw); cells = []; wins = counted = 0
    for sg in C.SEGS:
        d = [(k[1], V[k][0] - STD[k][2]) for k in STD if C.segment(k[0], k[1]) == sg]
        n, m, se = C.mse(d)
        if sg in C.HOLDS and n >= 20: counted += 1; wins += m > 0
        cells.append(f"{m:+.3f} ({n:4d})")
    dh = [(k[1], V[k][0] - STD[k][2]) for k in STD if C.segment(k[0], k[1]) in C.HOLDS]
    n, m, se = C.mse(dh); t_ = m / se if se > 0 else float("nan")
    st = stats([V[k][0] for k in STD])
    L = C.port_line(name, C.variant_trades(**kw)); smaller_dd, higher_mar = judge_port(L)
    better = "BETTER strict" if t_ >= 2.9 and wins >= 3 else "BETTER" if t_ >= 2.0 and wins >= 3 else ""
    smoother = "SMOOTHER" if t_ > -1.0 and smaller_dd and higher_mar else ""
    verdict = " + ".join(x for x in (better, smoother) if x) or "fail"
    res2[name] = verdict; lines2.append(L)
    print(f"  {name:10s} | " + " | ".join(cells) + f" | {m:+.3f}R t {t_:+.2f} w {wins}/{counted} | "
          f"{st['mean']:+.3f} {st['med']:+.3f} {st['win']:4.0f}% {st['big']:5.1f}% | {verdict}")
print("\n  $10 portfolios (0.5% risk, max 8 open, one per coin; the variant decides how long a coin is busy):")
print(C.PORT_HEAD + " | MAR 2020-26 / 2024-26")
print(base_line["text"] + f" | {C.mar(*base_line['all'][::2]):5.2f} / {C.mar(*base_line['recent'][::2]):5.2f}")
for L in lines2: print(L["text"] + f" | {C.mar(*L['all'][::2]):5.2f} / {C.mar(*L['recent'][::2]):5.2f}")

# ============================================================== test 3
print("\n=== TEST 3: crashes and fast markets - stress profile of the standard exit ===")
STRESS = [("standard (stop = exactly -1R)", {}), ("GAP", dict(gap=True))] + \
         [(f"GAP + SLIP {x * 100:.2f}%", dict(gap=True, slip=x)) for x in (0.0005, 0.001, 0.0025, 0.005, 0.01)] + \
         [("GAP + FASTBAR", dict(gap=True, fastbar=0.5)), ("FEES x2", dict(fee_mult=2.0)), ("FUNDING x2", dict(fund_mult=2.0)),
          ("COMBINED", dict(gap=True, slip=0.0025, fastbar=0.5, fee_mult=2.0))]
print(f"  {'scenario':30s} | all signals mean R | pooled holdouts (t)   | $10 2020-2026 (trades, DD)   | $10 2024-2026 (trades, DD)")
# no stop was gapped in this data: 788 stop exits, none opened beyond the stop (4H opens sit ~1 bp from the previous close)
for lab, kw in STRESS:
    V = per_signal(**kw); a = np.mean([v[0] for v in V.values()])
    n, m, se = C.mse([(k[1], V[k][0]) for k in STD if C.segment(k[0], k[1]) in C.HOLDS])
    tr = C.variant_trades(**kw); e1, n1, d1 = C.port(tr); e2, n2, d2 = C.port([x for x in tr if year(x[4]) >= 2024])
    print(f"  {lab:30s} | {a:+.3f}R            | {m:+.3f}R (t {m / se:+.2f})    | ${e1:7.2f} ({n1:4d}, {d1:4.0f}%)       | ${e2:6.2f} ({n2:4d}, {d2:4.0f}%)")
    if lab == "GAP + SLIP 0.25%": robust = (m >= 0.10 and e1 > 40)

def bisect(f, lo=0.0, hi=0.03, it=22):
    if f(hi) > 0: return None
    for _ in range(it):
        mid = (lo + hi) / 2
        if f(mid) > 0: lo = mid
        else: hi = mid
    return (lo + hi) / 2
be_r = bisect(lambda x: np.mean([v[0] for v in per_signal(gap=True, slip=x).values()]))
be_10 = bisect(lambda x: C.port(C.variant_trades(gap=True, slip=x))[0] - 10.0)
print(f"\n  break-even slippage per market fill (with GAP): average net R reaches 0 at {be_r * 100:.2f}%; "
      f"$10 2020-2026 ends at $10 at {be_10 * 100:.2f}%")
verdict3 = "robust" if robust else ("fragile" if (be_r or 0) < 0.0025 else "neither robust nor fragile")
print(f"  pre-registered verdict: {verdict3} (robust = with GAP + 0.25% the holdout average stays >= +0.10R and $10 2020-2026 above $40; "
      f"fragile = break-even slippage below 0.25%)")

# crash table: the 10 largest non-overlapping 24-hour BTC falls
B = C.series("BTC", False); c = B["c"]; t = B["t"]
r6 = np.full(len(c), np.nan); r6[6:] = c[6:] / c[:-6] - 1
order = np.argsort(np.nan_to_num(r6, nan=1.0)); picked = []
for i in order:
    if np.isnan(r6[i]) or len(picked) == 10: break
    if all(abs(i - j) >= 18 for j in picked): picked.append(i)
_, _, _, REC = C.port(C.FINAL, record=True)


def mtm_R(row, w0, w1):
    """move of an open position over the window in R (close of the bar ending at w0 -> close of the bar ending at w1)"""
    te, sym, R, tx, t0, sd = row; S = C.series(sym, sd == "S"); i = S["idx"][int(t0)]; e = i + 1; ep = S["o"][e]
    stop = S["c"][i] - 3 * S["a"][i]
    if ep - stop < C.MINSTOP * abs(ep): stop = ep - C.MINSTOP * abs(ep)
    k0 = S["idx"].get(w0 - H4); k1 = S["idx"].get(min(w1, tx) - H4)
    if k0 is None or k1 is None: return 0.0
    p0 = max(S["c"][k0], stop) if k0 >= e else ep                      # a position opened inside the window starts at its entry
    p1 = S["c"][k1] if tx > w1 else (R * (ep - stop) + ep)             # closed inside the window: its realized exit (net R)
    return (p1 - p0) / (ep - stop)
print("\n  the 10 largest 24-hour BTC falls (4H closes) and the $10 portfolio, standard exit:")
print(f"  {'24h ending':16s} {'BTC':>7s} | open (long/short) | exits in window (stops) | mark-to-market move of all open trades | equity change")
for i in sorted(picked):
    w0, w1 = int(t[i - 6]) + H4, int(t[i]) + H4
    op = [(row, pnl, eq) for row, pnl, eq in REC if row[0] <= w1 and row[3] > w0]
    ex = [(row, pnl, eq) for row, pnl, eq in REC if w0 < row[3] <= w1]
    eq0 = max([eq for row, pnl, eq in REC if row[0] <= w0] or [10.0])
    mv = sum(mtm_R(row, w0, w1) for row, pnl, eq in op)
    deq = sum(mtm_R(row, w0, w1) * eq * 0.005 for row, pnl, eq in op) / eq0 * 100
    print(f"  {time.strftime('%Y-%m-%d %H:%M', time.gmtime(w1 / 1000)):16s} {r6[i] * 100:+6.1f}% | {sum(1 for r, p, e in op if r[5] == 'L'):2d} / {sum(1 for r, p, e in op if r[5] == 'S'):2d}"
          f"           | {len(ex):2d} ({sum(1 for r, p, e in ex if r[2] <= -0.99):2d})                 | {mv:+6.2f}R"
          f"                               | {deq:+5.1f}%")
print("\nverdicts test 2: " + ", ".join(f"{k} {v}" for k, v in res2.items()) + f" | test 3: {verdict3}")
