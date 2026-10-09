"""Parity of the situation engine with the published system before any test runs."""
import sit_core as C
from prereg_run import equity
from ls_improve import growth, YEARS
bad = 0; worst = 0.0
for te, sym, R, tx, t0, sd in C.FINAL:
    R2, te2, tx2 = C.simulate(sym, sd, t0)
    d = abs(R2 - R); worst = max(worst, d)
    if d > 1e-9 or te2 != te or tx2 != tx: bad += 1
print(f"1. re-simulated standard exit vs stored result: {len(C.FINAL)} signals, {bad} mismatches, largest |dR| {worst:.2e}")
seq = C.variant_trades()
fin = sorted(C.FINAL); same = len(seq) == len(fin) and all((a[0], a[1], a[3], a[4], a[5]) == (b[0], b[1], b[3], b[4], b[5]) and abs(a[2] - b[2]) < 1e-9 for a, b in zip(seq, fin))
print(f"2. all {len(C.CANDS)} candidate entries re-simulated and sequenced one per coin: same trades as the system's list: {same} ({len(seq)} vs {len(fin)})")
a = equity([x[:4] for x in C.FINAL], risk=0.005, maxpos=8); b = C.port(C.FINAL)
print(f"3. portfolio: prereg_run.equity {a[0]:.6f} / {a[1]} / {a[2]:.4f} | sit_core.port {b[0]:.6f} / {b[1]} / {b[2]:.4f} | identical: {abs(a[0] - b[0]) < 1e-9 and a[1] == b[1] and abs(a[2] - b[2]) < 1e-9}")
py = C.port_years(C.FINAL); ok = all(abs(py[y][0] - growth(C.FINAL, y, y)[0]) < 1e-9 for y in YEARS)
print(f"4. per-year restarts identical to ls_improve.growth: {ok}  " + " ".join(f"{y}:{py[y][0]:.2f}" for y in YEARS))
w1 = C.port(C.FINAL, weight=lambda r: 1.0); print(f"5. weight 1.0 everywhere unchanged: {abs(w1[0] - b[0]) < 1e-12}; dircap 8 unchanged: {abs(C.port(C.FINAL, dircap=8)[0] - b[0]) < 1e-12}")
C.save_cache()
