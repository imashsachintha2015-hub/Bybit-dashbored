"""Do the shorts earn their place in the final funding-aware Kalman trend system? (exploratory, no new rules)
Splits the final system (BASE + SHORT_FUND + LONG_FUND_CAP) into its long and short halves:
per-signal average R and day-clustered t by side and year, the dollars each side made inside the $10 portfolio
(0.5% risk, max 8 open, one per coin), and the portfolio with and without the shorts."""
import pickle, time, collections
import numpy as np
import funding as FU
from ls_improve import trades, growth, YEARS, year
from prereg_run import equity, tstat
E = pickle.load(open("ls_entries.pkl", "rb"))
for r in E:
    r["favg"] = None; ft, fcum = FU.series(r["sym"])
    if ft:
        b = int(np.searchsorted(ft, r["t"] + 4 * 3600000, side="right"))
        if b >= 9: r["favg"] = (fcum[b] - fcum[b - 9]) / 9
E2 = [r for r in E if not (r["side"] == "L" and r["favg"] is not None and r["favg"] > 0.0003)]
final = trades(E2, {"SHORT_FUND"}); longonly = trades(E2, {"LONG_ONLY"})
shortonly = [x for x in final if x[5] == "S"]

print("=== 1. every signal of the final system (before the 8-position limit): average R after 14 bps + real funding ===")
print("  side   " + " ".join(f"{y:>13d}" for y in YEARS) + "        all years")
for sd, lab in (("L", "long "), ("S", "short")):
    row = []
    for y in YEARS:
        x = [(te, R) for te, s, R, tx, t0, d in final if d == sd and year(t0) == y]
        n, m, t = tstat(x); row.append(f"{m:+.2f} ({n:3d}) " if n else "    -       ")
    n, m, t = tstat([(te, R) for te, s, R, tx, t0, d in final if d == sd])
    top = sorted([R for te, s, R, tx, t0, d in final if d == sd], reverse=True); k = max(1, len(top) // 100)
    print(f"  {lab}  " + " ".join(row) + f"   {m:+.3f}R, t={t:.1f}, n={n}; without its top 1% ({k} trades): {np.mean(top[k:]):+.3f}R")

print("\n=== 2. inside the $10 portfolio (0.5% risk, max 8 open, one per coin, compounding 2020-01 .. 2026-09) ===")
openp = []; eq = 10.0; pk = 10.0; dd = 0.0; pnl = collections.defaultdict(float); nside = collections.Counter()
for te, sym, R, tx, t0, sd in sorted(final):
    for p in sorted([p for p in openp if p[0] <= te]):
        openp.remove(p); eq += p[1]; pk = max(pk, eq); dd = min(dd, eq / pk - 1); pnl[(p[3], year(p[0] - 1))] += p[1]
    if len(openp) >= 8 or any(p[2] == sym for p in openp) or eq < 1: continue
    openp.append((tx, eq * 0.005 * R, sym, sd)); nside[sd] += 1
for p in sorted(openp): eq += p[1]; pnl[(p[3], year(p[0] - 1))] += p[1]
print(f"  final equity ${eq:.2f}: longs made ${sum(v for (s, y), v in pnl.items() if s == 'L'):+.2f} from {nside['L']} trades, "
      f"shorts ${sum(v for (s, y), v in pnl.items() if s == 'S'):+.2f} from {nside['S']} trades")
print("  dollars made per year (by exit year):  " + "  ".join(f"{y}: L {pnl[('L', y)]:+6.2f} S {pnl[('S', y)]:+6.2f}" for y in YEARS))

print("\n=== 3. with vs without the shorts ($10, same portfolio rules) ===")
print(f"  {'system':28s} " + " ".join(f"{y:>8d}" for y in YEARS) + "   2020-2026 (trades, max DD)         2024-2026 (trades, max DD)")
for lab, tr in (("long + short (final)", final), ("long only (with fund cap)", longonly), ("short only", shortonly)):
    g = {y: growth(tr, y, y)[0] for y in YEARS}
    e1, n1, d1 = equity([(te, s, R, tx) for te, s, R, tx, t0, sd in tr], risk=0.005, maxpos=8)
    e2, n2, d2 = equity([(te, s, R, tx) for te, s, R, tx, t0, sd in tr if year(t0) >= 2024], risk=0.005, maxpos=8)
    print(f"  {lab:28s} " + " ".join(f"{g[y]:8.2f}" for y in YEARS) +
          f"   ${e1:6.2f} ({n1:4d} trades, {d1:4.0f}%)    ${e2:6.2f} ({n2:4d} trades, {d2:4.0f}%)")
