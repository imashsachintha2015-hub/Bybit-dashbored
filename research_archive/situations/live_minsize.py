"""What exchange minimum order sizes do to the tested system at small account sizes (exploratory).
Assumed Bybit minimums: 0.001 BTC, 0.01 ETH, 5 USDT order value for other coins. Leverage 10x (margin check only).
Each trade wants 0.5% risk; if that position is below the minimum, the minimum is used and the risk per trade grows."""
import numpy as np, sit_core as C
from ls_improve import year
info = {}
for te, sym, R, tx, t0, sd in C.FINAL:
    S = C.series(sym, sd == "S"); i = S["idx"][int(t0)]; ep = S["o"][i + 1]; st = S["c"][i] - 3 * S["a"][i]
    if ep - st < C.MINSTOP * abs(ep): st = ep - C.MINSTOP * abs(ep)
    info[(sym, t0, sd)] = ((ep - st) / abs(ep), abs(ep))
def min_notional(sym, px): return 0.001 * px if sym == "BTC" else 0.01 * px if sym == "ETH" else 5.0
def run(start, trades, lev=10.0):
    openp = []; eq = start; pk = start; dd = 0.0; taken = 0; risks = []; skipped_margin = 0; bust = False
    for row in sorted(trades):
        te, sym, R, tx, t0, sd = row
        for p in sorted([p for p in openp if p[0] <= te], key=lambda p: p[:2]):
            openp.remove(p); eq += p[1]; pk = max(pk, eq); dd = min(dd, eq / pk - 1)
        if eq <= 0: bust = True; break
        if len(openp) >= 8 or any(p[2] == sym for p in openp) or eq < 1: continue
        rp, px = info[(sym, t0, sd)]
        notional = max(eq * 0.005 / rp, min_notional(sym, px))
        if sum(p[3] for p in openp) + notional / lev > eq: skipped_margin += 1; continue
        risks.append(notional * rp / eq); openp.append((tx, notional * rp * R, sym, notional / lev)); taken += 1
    for p in sorted(openp, key=lambda p: p[:2]): eq += p[1]; pk = max(pk, eq); dd = min(dd, eq / pk - 1)
    return eq, taken, dd * 100, np.median(risks) * 100 if risks else 0, np.max(risks) * 100 if risks else 0, skipped_margin, bust or eq <= 0
print("tested system, signals 2020-01 .. 2026-09; desired risk 0.5% per trade, max 8 open, 10x leverage")
print(f"  {'start':>7s} | end equity (x start)        | trades | max DD | risk per trade: median / largest | skipped for margin")
for start in (10, 50, 100, 300, 650, 1000):
    e, n, d, med, mx, sm, b = run(start, C.FINAL)
    print(f"  ${start:6.0f} | ${e:10.2f} ({e / start:6.2f}x){' BUST' if b else '     '} | {n:6d} | {d:5.0f}% | {med:5.1f}% / {mx:6.1f}%            | {sm}")
print("\n2024-01 .. 2026-09 only:")
rec = [x for x in C.FINAL if year(x[4]) >= 2024]
for start in (10, 100, 650, 1000):
    e, n, d, med, mx, sm, b = run(start, rec)
    print(f"  ${start:6.0f} | ${e:10.2f} ({e / start:6.2f}x){' BUST' if b else '     '} | {n:6d} | {d:5.0f}% | {med:5.1f}% / {mx:6.1f}%            | {sm}")
