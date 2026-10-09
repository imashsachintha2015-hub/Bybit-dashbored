"""Stop distance vs isolated-margin liquidation distance for the system's 3,080 signals (exploratory)."""
import numpy as np, sit_core as C
rp = []
for te, sym, R, tx, t0, sd in C.FINAL:
    S = C.series(sym, sd == "S"); i = S["idx"][int(t0)]; ep = S["o"][i + 1]; st = S["c"][i] - 3 * S["a"][i]
    if ep - st < C.MINSTOP * abs(ep): st = ep - C.MINSTOP * abs(ep)
    rp.append((ep - st) / abs(ep))
rp = np.array(rp) * 100
print("stop distance from entry, all 3,080 signals: " + ", ".join(f"{q}th pct {np.percentile(rp, q):.1f}%" for q in (50, 90, 95, 99)) + f", max {rp.max():.1f}%")
for lev in (5, 10, 20):
    liq = 100 / lev - 1.0          # rough isolated-margin liquidation distance, keeping ~1% for maintenance margin and fees
    print(f"  {lev:2d}x: liquidation about {liq:.1f}% away -> {np.mean(rp >= liq) * 100:.1f}% of trades have their stop beyond the liquidation price")
