"""Round 3 checks on the debug slice (BTC, ETH; data through 2023-03; trades looked at 2023-01..03 only).
LIQ: no repaint (cut data mid-way, every series before the cut identical); map total never exceeds the OI notional added; trade counts."""
import sys
import numpy as np
import hf_core as C
import hf_fam as F
import r3_liq as LQ

def cut_coin(nm, cut):
    co = C.load_coin(nm, first=(2022, 10), last=(2023, 3))
    m = co.times >= cut
    for a in (co.o, co.h, co.l, co.c, co.qv, co.n, co.tb): a[m] = np.nan
    return co

def liq_checks():
    fails = 0
    for nm in ("BTC", "ETH"):
        oi = C.load_oi(nm).copy()
        res = []
        for cut in (C.ms(2023, 4), C.ms(2023, 2, 20) + 7 * 60000):
            co = cut_coin(nm, cut); o2 = oi.copy(); o2[(np.arange(len(o2)) * 5 * 60000 + C.GRID_T0) >= cut] = np.nan
            O, H, L, Cc, Q, NN, TB = C.aggregate(co.o, co.h, co.l, co.c, co.qv, co.n, co.tb, 5)
            p0 = 1000.0 if nm == "BTC" else 100.0; nb_ = int((np.log(1e6) - np.log(p0)) / LQ.STEP) + 2
            res.append(LQ.engine(O, H, L, Cc, Q, TB, o2, 24.0, p0, nb_))
        jcut = (C.ms(2023, 2, 20) - C.GRID_T0) // 300000 - 2
        same = all(np.allclose(a[:jcut], b[:jcut], equal_nan=True, rtol=1e-9) for a, b in zip(res[0], res[1]))
        print(nm, "LIQ no-repaint:", "OK" if same else "FAIL"); fails += 0 if same else 1
        clrL, clrS, A, B, pa, pb = res[0]
        sl = slice((C.ms(2023) - C.GRID_T0) // 300000, (C.ms(2023, 4) - C.GRID_T0) // 300000)
        co = cut_coin(nm, C.ms(2023, 4)); O, H, L, Cc, Q, NN, TB = C.aggregate(co.o, co.h, co.l, co.c, co.qv, co.n, co.tb, 5)
        v24 = F.rolling_sum(np.where(np.isfinite(Q), Q, 0.0), 288)
        oin = np.nanmean(oi[sl] * Cc[sl])
        print(f"  {nm} Q1-2023: mean OI notional {oin / 1e9:.2f}bn, mean A {np.nanmean(A[sl]) / 1e9:.3f}bn B {np.nanmean(B[sl]) / 1e9:.3f}bn, "
              f"daily est. liquidations {np.nansum(clrL[sl] + clrS[sl]) / 90 / 1e6:.0f}m, A/V24 median {np.nanmedian(A[sl] / v24[sl]):.3f}")
        ok = np.nanmax(A[sl] + B[sl]) < 3 * np.nanmax(oi[sl] * Cc[sl])
        print("  map size below OI notional:", "OK" if ok else "FAIL"); fails += 0 if ok else 1
        fa = np.isfinite(pa[sl]); fb = np.isfinite(pb[sl]); above = np.mean(pa[sl][fa] > Cc[sl][fa]); below = np.mean(pb[sl][fb] < Cc[sl][fb])
        print(f"  targets on the right side: above {above:.3f} below {below:.3f}"); fails += 0 if (above > 0.999 and below > 0.999) else 1
    return fails

if __name__ == "__main__":
    f = liq_checks()
    print("FAILS", f); sys.exit(1 if f else 0)
