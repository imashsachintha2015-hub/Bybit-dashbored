"""Exploratory sensitivity of the one portfolio rule that passed (CORR_CAP: skip if >= K open same-direction trades are in coins
correlated > RHO with the new coin over 30 days). Not part of the pre-registered rule."""
import sys, io, contextlib
import sit_core as C
from ls_improve import YEARS
with contextlib.redirect_stdout(io.StringIO()):
    import sit_flush_heat as F                                         # reuse its correlation cache and helpers
base = C.port_line("current", C.FINAL); bm1, bm2 = C.mar(*base["all"][::2]), C.mar(*base["recent"][::2])
print(f"current rule: $10 -> ${base['all'][0]:.2f} ({base['all'][1]} trades, DD {base['all'][2]:.0f}%), MAR {bm1:.2f} / {bm2:.2f}")
print(f"  {'rho':>4s} {'K':>2s} | $10 2020-2026 (trades, DD)  | $10 2024-2026 (trades, DD)  | MAR 2020-26 / 2024-26 | years with higher MAR")
for rho in (0.6, 0.7, 0.8):
    for K in (2, 3, 4):
        def cap(row, open_rows, rho=rho, K=K):
            k = 0
            for r in open_rows:
                if r[5] != row[5]: continue
                v = F.corr(row[1], r[1], row[4])
                if v is not None and v > rho: k += 1
            return k >= K
        L = C.port_line(f"{rho} {K}", C.FINAL, corrcap=cap); m1, m2 = C.mar(*L["all"][::2]), C.mar(*L["recent"][::2])
        ym = sum(1 for y in YEARS if C.mar(L["years"][y][0], L["years"][y][2]) > C.mar(base["years"][y][0], base["years"][y][2]) + 1e-9)
        e1, n1, d1 = L["all"]; e2, n2, d2 = L["recent"]
        print(f"  {rho:4.1f} {K:2d} | ${e1:7.2f} ({n1:4d}, {d1:4.0f}%)       | ${e2:6.2f} ({n2:4d}, {d2:4.0f}%)       | {m1:5.2f} / {m2:5.2f}         | {ym}/7"
              + ("   <- pre-registered" if (rho, K) == (0.7, 3) else ""))
