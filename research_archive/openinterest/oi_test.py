"""Open-interest crowding filters on the final funding-aware Kalman trend system, exactly as PREREG_openinterest.md
(committed before the data was downloaded; coverage addendum committed before any file was opened).
Unit: net R per signal (14 bps + real funding) of the final system before the 8-position limit, one per coin; day-clustered SEs."""
import pickle, collections, math, time
import numpy as np
import funding as FU, oi_feat as OF
from ls_improve import trades, growth, YEARS, year
from prereg_run import equity, tstat
from inv_run import seg
DAY = 86400000; HOLD = ("VAL", "FINAL", "UNSEEN", "OLD"); SEGS = ("DEV",) + HOLD; MIN_N = 20

def mse(pairs):
    """n, mean, day-clustered standard error (the formula of prereg_run.tstat)"""
    d = collections.defaultdict(list)
    for t, r in pairs: d[t // DAY].append(r)
    n = sum(len(v) for v in d.values())
    if n < 2: return n, (sum(r for _, r in pairs) / n if n else float("nan")), float("nan")
    m = sum(x for v in d.values() for x in v) / n
    return n, m, math.sqrt(sum((sum(v) - m * len(v)) ** 2 for v in d.values())) / n

E = pickle.load(open("ls_entries.pkl", "rb"))
for r in E:
    r["favg"] = None; ft, fcum = FU.series(r["sym"])
    if ft:
        b = int(np.searchsorted(ft, r["t"] + 4 * 3600000, side="right"))
        if b >= 9: r["favg"] = (fcum[b] - fcum[b - 9]) / 9
E2 = [r for r in E if not (r["side"] == "L" and r["favg"] is not None and r["favg"] > 0.0003)]
final = trades(E2, {"SHORT_FUND"}); base = trades(E, set())
_fc = {}
def F(sym, t0):
    if (sym, t0) not in _fc: _fc[(sym, t0)] = OF.feats(sym, t0)
    return _fc[(sym, t0)]
favg_of = {(r["sym"], r["t"], r["side"]): r["favg"] for r in E if r["en"] == "KAL1"}

# parity with the published result before anything else
eq0, n0, dd0 = equity([(te, s, R, tx) for te, s, R, tx, t0, sd in final], risk=0.005, maxpos=8)
print(f"parity: final system 2020-01 .. 2026-09 $10 -> ${eq0:.2f} ({n0} trades, DD {dd0:.0f}%) [longshort/README: $94.12, 1,616 trades, -28%]")
print(f"8 pre-registered tests; at t >= 2.0 about {8 * 0.023:.2f} false passes are expected by luck, at t >= 2.5 about {8 * 0.0062:.2f}\n")

def split(tr, side, feat, cond, segs):
    k, x = [], []
    for te, sym, R, tx, t0, sd in tr:
        if sd != side or seg({"t": t0, "sym": sym}) not in segs: continue
        v = F(sym, t0)[feat]
        if v is None: continue
        (k if cond(v) else x).append((t0, R))
    return k, x

fmt = lambda n, m: f"{m:+.3f} ({n:3d})" if n else "    -      "
verdicts = {}
for fam, side in (("PRIMARY: shorts", "S"), ("SECONDARY: longs", "L")):
    print(f"=== {fam} - average net R per signal (n): benchmark = all signals with data | kept by the filter | removed ===")
    for name, (sd, feat, cond) in OF.FILTERS.items():
        if sd != side: continue
        print(f"  {name} (keep if {feat} {'> 0' if cond(1) and not cond(-1) else '< 0'})")
        wins = counted = 0
        for sg in SEGS:
            k, x = split(final, side, feat, cond, {sg}); b = k + x
            nb, mb, _ = mse(b); nk, mk, _ = mse(k); nx, mx, _ = mse(x)
            tag = ""
            if sg in HOLD:
                if nb >= MIN_N:
                    counted += 1; w = nk > 0 and mk > mb; wins += w; tag = "win" if w else "loss"
                else: tag = "too few (not counted)"
            print(f"    {sg:6s} bench {fmt(nb, mb)} | kept {fmt(nk, mk)} | removed {fmt(nx, mx)}  {tag}")
        k, x = split(final, side, feat, cond, set(HOLD)); nk, mk, sk = mse(k); nx, mx, sx = mse(x)
        tsep = (mk - mx) / math.sqrt(sk ** 2 + sx ** 2) if nk > 1 and nx > 1 else float("nan")
        v = "PASS strict" if tsep >= 2.5 and wins >= 3 else "PASS" if tsep >= 2.0 and wins >= 3 else "fail"
        verdicts[name] = v
        print(f"    pooled holdouts: kept {mk:+.3f}R (n {nk}, se {sk:.3f}) vs removed {mx:+.3f}R (n {nx}, se {sx:.3f}): "
              f"separation t = {tsep:+.2f}; wins {wins} of {counted} counted holdouts -> {v}")
    print()

print("=== $10 portfolio, 0.5% risk, max 8 open, one per coin (signals without data stay as in the final system) ===")
print(f"  {'system':24s} " + " ".join(f"{y:>7d}" for y in YEARS) + "   2020-2026 (trades, DD)       2024-2026 (trades, DD)    years better")
gf = {y: growth(final, y, y)[0] for y in YEARS}
def line(lab, tr, g):
    e1, n1, d1 = equity([(te, s, R, tx) for te, s, R, tx, t0, sd in tr], risk=0.005, maxpos=8)
    e2, n2, d2 = equity([(te, s, R, tx) for te, s, R, tx, t0, sd in tr if year(t0) >= 2024], risk=0.005, maxpos=8)
    better = "" if g is gf else f"{sum(1 for y in YEARS if g[y] > gf[y] + 1e-9)}/7 ({sum(1 for y in YEARS if abs(g[y] - gf[y]) < 1e-9)} equal)"
    print(f"  {lab:24s} " + " ".join(f"{g[y]:7.2f}" for y in YEARS) + f"   ${e1:6.2f} ({n1:4d}, {d1:4.0f}%)     ${e2:6.2f} ({n2:4d}, {d2:4.0f}%)    {better}")
line("final system", final, gf)
for name, (sd, feat, cond) in OF.FILTERS.items():
    E3 = []
    for r in E2:
        if r["side"] == sd and r["en"] == "KAL1" and r["btc"] == 1:
            v = F(r["sym"], r["t"])[feat]
            if v is not None and not cond(v): continue
        E3.append(r)
    tr = trades(E3, {"SHORT_FUND"}); line(f"+ {name}", tr, {y: growth(tr, y, y)[0] for y in YEARS})

print("\n=== for information only: the same filters on the BASE system (no funding filters), all segments with data pooled ===")
for name, (sd, feat, cond) in OF.FILTERS.items():
    k, x = split(base, sd, feat, cond, set(SEGS)); nk, mk, sk = mse(k); nx, mx, sx = mse(x)
    print(f"  {name:15s} kept {mk:+.3f}R (n {nk}) vs removed {mx:+.3f}R (n {nx}), separation t = {(mk - mx) / math.sqrt(sk ** 2 + sx ** 2):+.2f}")

print("\n=== diagnostics: rank correlation of each feature with the 3-day funding average at final-system signals ===")
rk = lambda a: np.argsort(np.argsort(a))
for side in ("S", "L"):
    row = []
    for feat in OF.NAMES:
        pr = [(F(sym, t0)[feat], favg_of.get((sym, t0, sd))) for te, sym, R, tx, t0, sd in final if sd == side]
        pr = [(a, b) for a, b in pr if a is not None and b is not None]
        row.append(f"{feat} {np.corrcoef(rk([a for a, _ in pr]), rk([b for _, b in pr]))[0, 1]:+.2f} (n {len(pr)})")
    print(f"  {'shorts' if side == 'S' else 'longs '}: " + " | ".join(row))
print("\nverdicts: " + ", ".join(f"{k} {v}" for k, v in verdicts.items()))
