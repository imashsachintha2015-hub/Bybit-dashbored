"""One extra, single test written after ls_improve_out.txt: the mirror of SHORT_FUND for longs.
LONG_FUND_CAP = skip longs when the last 9 funding payments average more than 0.03% per payment (longs overcrowded and paying).
Threshold fixed before running (3x the neutral 0.01%). Compared per year against BASE + SHORT_FUND."""
import pickle, time
import numpy as np
import funding as FU
from ls_improve import trades, growth, YEARS, year
from prereg_run import equity
E = pickle.load(open("ls_entries.pkl", "rb"))
for r in E:
    r["favg"] = None
    ft, fcum = FU.series(r["sym"])
    if ft:
        b = int(np.searchsorted(ft, r["t"] + 4 * 3600000, side="right"))
        if b >= 9: r["favg"] = (fcum[b] - fcum[b - 9]) / 9
E2 = [r for r in E if not (r["side"] == "L" and r["favg"] is not None and r["favg"] > 0.0003)]
skipped = sum(1 for r in E if r["side"] == "L" and r["en"] == "KAL1" and r["btc"] == 1 and r["favg"] is not None and r["favg"] > 0.0003)
a = trades(E, {"SHORT_FUND"}); b = trades(E2, {"SHORT_FUND"})
ga = {y: growth(a, y, y)[0] for y in YEARS}; gb = {y: growth(b, y, y)[0] for y in YEARS}
print(f"long signals skipped by the cap (funding > 0.03%/payment): {skipped}")
print("  system                         " + " ".join(f"{y:>8d}" for y in YEARS))
print("  BASE + SHORT_FUND              " + " ".join(f"{ga[y]:8.2f}" for y in YEARS))
print("  + LONG_FUND_CAP                " + " ".join(f"{gb[y]:8.2f}" for y in YEARS) + f"   better in {sum(1 for y in YEARS if gb[y] > ga[y])}/7 years")
for lab, tr in (("BASE + SHORT_FUND", a), ("BASE + SHORT_FUND + LONG_FUND_CAP", b)):
    eq, n, dd = equity([(te, s, R, tx) for te, s, R, tx, t0, sd in tr], risk=0.005, maxpos=8)
    eq2, n2, dd2 = equity([(te, s, R, tx) for te, s, R, tx, t0, sd in tr if year(t0) >= 2024], risk=0.005, maxpos=8)
    print(f"  {lab:36s} 2020-2026 $10 -> ${eq:.2f} ({n} trades, DD {dd:.0f}%) | 2024-2026 $10 -> ${eq2:.2f} ({n2} trades, DD {dd2:.0f}%)")
base = trades(E, set())
for lab, tr in (("BASE (for reference)", base),):
    eq, n, dd = equity([(te, s, R, tx) for te, s, R, tx, t0, sd in tr], risk=0.005, maxpos=8)
    eq2, n2, dd2 = equity([(te, s, R, tx) for te, s, R, tx, t0, sd in tr if year(t0) >= 2024], risk=0.005, maxpos=8)
    print(f"  {lab:36s} 2020-2026 $10 -> ${eq:.2f} ({n} trades, DD {dd:.0f}%) | 2024-2026 $10 -> ${eq2:.2f} ({n2} trades, DD {dd2:.0f}%)")
