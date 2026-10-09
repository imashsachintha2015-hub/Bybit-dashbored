"""Round 2 checks, on the debug slice only (BTC and ETH, data through 2023-03; trades inspected 2023-01..03):
1. no repaint: events and levels computed on data cut at a time T equal those computed on more data, for every bar before T
2. levels: PDH/PDL equal the previous day's 1m extremes; VWAP equals a direct computation; POC lies inside the day range, VAL <= POC <= VAH
3. trade walk: stop exits lose exactly the stop distance (or more on a gap); targets gain the target distance; time exits are within range
4. per-setup event counts in the slice (sanity)"""
import sys
import numpy as np
import hf_core as C
import hf2_feat as FT
import hf2_setups as S

T_END = C.ms(2023, 4)


def load_cut(name, cut_ms):
    co = C.load_coin(name, first=(2022, 9), last=(2023, 3))
    m = co.times >= cut_ms
    for a in (co.o, co.h, co.l, co.c, co.qv, co.n, co.tb): a[m] = np.nan
    return co


def main():
    fails = 0
    btc = C.load_coin("BTC", first=(2022, 9), last=(2023, 3))
    for name in ("BTC", "ETH"):
        ft, fr = C.load_funding(name); oi = C.load_oi(name)
        full = load_cut(name, T_END)
        for k in (5, 15):
            tf = FT.TF(full, k, oi, btc.c)
            ej, es, eid, eS, eT, lev = S.scan_tf(tf)
            sl = (tf.t[ej] >= C.ms(2023)) & (tf.t[ej] < T_END)
            cnt = np.bincount(eid[sl], minlength=len(S.SETUPS))
            print(f"\n{name} {k}m events 2023-01..03 per setup:")
            print("  " + "  ".join(f"{S.SETUPS[i]}={cnt[i]}" for i in range(len(S.SETUPS))))
            # 1. no repaint: cut at 2023-02-15 12:07 (not on a bar boundary)
            cut = C.ms(2023, 2, 15) + 12 * 3600_000 + 7 * 60_000
            co2 = load_cut(name, cut)
            tf2 = FT.TF(co2, k, oi, btc.c)
            ej2, es2, eid2, eS2, eT2, lev2 = S.scan_tf(tf2)
            jcut = (cut - C.GRID_T0) // (k * C.MIN) - 1                            # last bar fully closed before the cut
            a = (ej < jcut) & (eid < 25); b = (ej2 < jcut) & (eid2 < 25)
            same = (a.sum() == b.sum()) and np.array_equal(ej[a], ej2[b]) and np.array_equal(eid[a], eid2[b]) and \
                np.allclose(eS[a], eS2[b], equal_nan=True) and np.allclose(eT[a], eT2[b], equal_nan=True)
            for nm_ in ("regime", "vwap", "pdh", "poc", "vah", "asiah", "e200", "rsi", "adx", "st", "oich", "dh48"):
                x1 = getattr(tf, nm_)[:jcut]; x2 = getattr(tf2, nm_)[:jcut]
                if not np.allclose(np.asarray(x1, float), np.asarray(x2, float), equal_nan=True):
                    print("  REPAINT in", nm_); fails += 1
            print(f"  no-repaint events before the cut: {'OK' if same else 'FAIL'} ({a.sum()} vs {b.sum()})"); fails += 0 if same else 1
            # 2. levels
            d = C.ms(2023, 2, 10); jd = (d - C.GRID_T0) // (k * C.MIN) + 2
            i0 = (d - C.GRID_T0) // C.MIN - 1440
            ph = np.nanmax(full.h[i0:i0 + 1440]); pl = np.nanmin(full.l[i0:i0 + 1440])
            ok = np.isclose(tf.pdh[jd], ph) and np.isclose(tf.pdl[jd], pl) and tf.val[jd] <= tf.poc[jd] <= tf.vah[jd] and pl <= tf.poc[jd] <= ph
            i_c = tf.close_1m[jd]; dd = (i_c // 1440) * 1440
            tp = (full.h[dd:i_c + 1] + full.l[dd:i_c + 1] + full.c[dd:i_c + 1]) / 3; v = full.qv[dd:i_c + 1] / tp
            ok &= np.isclose(np.nansum(tp * v) / np.nansum(v), tf.vwap[jd])
            print(f"  levels PDH/PDL/POC/VWAP: {'OK' if ok else 'FAIL'}"); fails += 0 if ok else 1
            # 3. trade walk
            ent, sf, datr, okk, xms, gr, fu, rs = S.simulate(full.o, full.h, full.l, full.c, k, ej, es, eS, eT, tf.atr, full.t0, ft, fr, False, 0.05)
            m = okk & sl
            bad = 0
            for x in range(4):
                stopx = m & (rs[:, x] == 1)
                bad += int(np.sum(gr[stopx, x] > -sf[stopx] + 1e-9 if x != 3 else gr[stopx, x] > 3 * sf[stopx] * 10))
                if x < 2:
                    tg = m & (rs[:, x] == 2); mult = (1.5, 3.0)[x]
                    bad += int(np.sum(gr[tg, x] < mult * sf[tg] - 1e-9))
                tm = m & (rs[:, x] == 0)
                bad += int(np.sum(np.abs(gr[tm, x]) > 0.5))
            hold = (xms[m, 0] - ent[m]) / 60000
            print(f"  trade walk: {int(m.sum())} trades, bad exits {bad}, max hold {hold.max():.0f} min (limit {48 * k}), "
                  f"stop% median {np.median(sf[m]) * 100:.2f}, d/ATR median {np.median(datr[m]):.2f}")
            fails += bad
            if hold.max() > 48 * k: fails += 1
            # gross R per exit (slice only, for sanity, no selection)
            for x, nm_ in enumerate(S.EXITS):
                r = (gr[m, x] - 0.0014 - fu[m, x]) / sf[m]
                print(f"    {nm_}: mean net R {np.mean(r):+.3f}  win {np.mean(r > 0) * 100:.0f}%  reasons {np.bincount(rs[m, x], minlength=3)}")
    print("\nFAILS:", fails)
    return fails


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
