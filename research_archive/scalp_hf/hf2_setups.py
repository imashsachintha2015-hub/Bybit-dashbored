"""Round 2 (PREREG_round2.md): the 25 setups (long and short), the RANDOM control, and the 1m trade walk with structural stops and
level targets. Output per coin and timeframe: one row per setup event with its four exits simulated independently; the one-position-
per-coin-per-cell rule is applied later, per cell (hf2_eval.py).

Addendum 1 (written before any round-2 run on any data; details the pre-registration left open):
- "Fresh" conditions so a setup fires once per event rather than on every bar of the same move: BRK_DON / BRK_PD / BRK_ASIA / SQZ_BRK need
  the previous close on the other side of the level; EMA_PB needs the previous low above EMA20 (a new touch).
- Once per UTC day per side: BRK_PD, BRK_ASIA, FAKE_PD, SWEEP_PD, SWEEP_ASIA, VP_FADE. Once per session: ORB. Once per swing / leg / pool:
  FIB_PB, CHOCH, RSI_DIV, CVD_DIV, SWEEP_SWING, SWEEP_EQ.
- SWEEP_SWING and FIB_PB consider a swing only while it is at most 96 bars old; RSI_DIV / CVD_DIV at most 48 bars.
- Keltner uses ATR14. The ORB range is built from the signal bars covering the first 15 minutes of the session.
- A target T on the wrong side of the entry counts as "closer than 1R" (the LVL exit then uses 1R).
- RANDOM: each bar is a candidate with probability k/240 (about one per 4 hours), side by coin flip, S = close -+ 1 ATR, seed fixed per coin.
"""
import os, sys
import numpy as np
import numba as nb
import hf_core as C
import hf2_feat as FT

SETUPS = ["BRK_DON", "BRK_PD", "BRK_ASIA", "BRK_RETEST", "SQZ_BRK", "ORB", "FAKE_DON", "FAKE_PD", "SWEEP_PD", "SWEEP_ASIA",
          "SWEEP_EQ", "SWEEP_SWING", "FIB_PB", "VWAP_PB", "EMA_PB", "ST_FLIP", "MACD_X", "CHOCH", "RSI_DIV", "CVD_DIV", "EXHAUST",
          "OI_FLUSH", "VP_FADE", "CONF_BOUNCE", "BB_FADE", "RANDOM"]
SCENARIO = {"BRK_DON": "breakout", "BRK_PD": "breakout", "BRK_ASIA": "breakout", "BRK_RETEST": "breakout", "SQZ_BRK": "breakout",
            "ORB": "breakout", "FAKE_DON": "fakeout", "FAKE_PD": "fakeout", "SWEEP_PD": "liquidity", "SWEEP_ASIA": "liquidity",
            "SWEEP_EQ": "liquidity", "SWEEP_SWING": "liquidity", "FIB_PB": "continuation", "VWAP_PB": "continuation",
            "EMA_PB": "continuation", "ST_FLIP": "continuation", "MACD_X": "continuation", "CHOCH": "reversal", "RSI_DIV": "reversal",
            "CVD_DIV": "reversal", "EXHAUST": "reversal", "OI_FLUSH": "reversal", "VP_FADE": "range", "CONF_BOUNCE": "range",
            "BB_FADE": "range", "RANDOM": "control"}
EXITS = ["R15", "R3", "LVL", "TRAIL"]
NLEV = 16
OUT = os.path.join(C.DATA, "r2")


@nb.njit(cache=True)
def put(ej, es, eid, eS, eT, k, j, side, sid, S, T):
    if k < len(ej):
        ej[k] = j; es[k] = side; eid[k] = sid; eS[k] = S; eT[k] = T
    return k + 1


@nb.njit(cache=True)
def nearest_round(p):
    u = 10.0 ** (np.floor(np.log10(p)) - 1)
    return np.floor(p / u) * u, np.ceil(p / u) * u


@nb.njit(cache=True)
def scan(k, O, H, L, Cc, atr, rv, e20, e50, e200, rsi, adx, bbm, bbu, bbl, kcu, kcl, dh48, dl48, dh20, dl20, st, stdir, hist, cvd,
         ph, pl, vwap, vsd, wvwap, pdh, pdl, pdc, poc, vah, val, asiah, asial, pwh, pwl, day, mod, oich, oiq, lev):
    n = len(Cc); cap = 2 * n
    ej = np.zeros(cap, np.int64); es = np.zeros(cap, np.int64); eid = np.zeros(cap, np.int64)
    eS = np.zeros(cap); eT = np.zeros(cap); m = 0
    NS = 8
    shp = np.full(NS, np.nan); shi = np.full(NS, -1, np.int64); shr = np.full(NS, np.nan); shc = np.full(NS, np.nan)
    slp = np.full(NS, np.nan); sli = np.full(NS, -1, np.int64); slr = np.full(NS, np.nan); slc = np.full(NS, np.nan)
    # breakout trackers [long, short]
    bj = np.full(2, -10 ** 9, np.int64); blv = np.zeros(2); bht = np.zeros(2); bmid = np.zeros(2); bext = np.zeros(2)
    bret = np.zeros(2, np.bool_); bfake = np.zeros(2, np.bool_)
    pj = np.full(2, -10 ** 9, np.int64); pext = np.zeros(2); pfake = np.zeros(2, np.bool_)
    done_pd = np.zeros(2, np.bool_); done_asia = np.zeros(2, np.bool_); done_fpd = np.zeros(2, np.bool_)
    done_spd = np.zeros(2, np.bool_); done_sasia = np.zeros(2, np.bool_); done_vp = np.zeros(2, np.bool_)
    sess = np.array([0, 480, 810]); orh = np.full(3, np.nan); orl = np.full(3, np.nan); ordone = np.zeros(3, np.bool_); orbad = np.zeros(3, np.bool_)
    fib_done = np.full(2, -1, np.int64); choch_done = np.full(2, -1, np.int64); rdiv_done = np.full(2, -1, np.int64)
    cdiv_done = np.full(2, -1, np.int64); ssw_done = np.full(2, -1, np.int64)
    for j in range(210, n):
        # ---- confirm pivots (known 3 bars after the pivot)
        p = j - 3
        if ph[p]:
            for q in range(NS - 1): shp[q] = shp[q + 1]; shi[q] = shi[q + 1]; shr[q] = shr[q + 1]; shc[q] = shc[q + 1]
            shp[NS - 1] = H[p]; shi[NS - 1] = p; shr[NS - 1] = rsi[p]; shc[NS - 1] = cvd[p]
        if pl[p]:
            for q in range(NS - 1): slp[q] = slp[q + 1]; sli[q] = sli[q + 1]; slr[q] = slr[q + 1]; slc[q] = slc[q + 1]
            slp[NS - 1] = L[p]; sli[NS - 1] = p; slr[NS - 1] = rsi[p]; slc[NS - 1] = cvd[p]
        if day[j] != day[j - 1]:
            done_pd[:] = False; done_asia[:] = False; done_fpd[:] = False; done_spd[:] = False; done_sasia[:] = False; done_vp[:] = False
            orh[:] = np.nan; orl[:] = np.nan; ordone[:] = False; orbad[:] = False
        o = O[j]; h = H[j]; l = L[j]; c = Cc[j]; a = atr[j]
        # ORB ranges are built even when this bar cannot trade
        for s in range(3):
            if mod[j] >= sess[s] and mod[j] < sess[s] + 15:
                if not (np.isfinite(h) and np.isfinite(l)): orbad[s] = True
                else:
                    orh[s] = h if np.isnan(orh[s]) else max(orh[s], h); orl[s] = l if np.isnan(orl[s]) else min(orl[s], l)
        if not (np.isfinite(o) and np.isfinite(h) and np.isfinite(l) and np.isfinite(c) and np.isfinite(a) and a > 0): continue
        cp = Cc[j - 1]; rvj = rv[j] if np.isfinite(rv[j]) else 0.0
        for sd in range(2):
            sg = 1 if sd == 0 else -1
            # mirrored prices: for the short side work on negated prices so one code path serves both
            if sg == 1:
                hh, ll, cc, oo, cpp = h, l, c, o, cp
            else:
                hh, ll, cc, oo, cpp = -l, -h, -c, -o, -cp
            # ---------- 3 BRK_RETEST and 6 FAKE_DON on an earlier BRK_DON break
            dj = j - bj[sd]
            if 1 <= dj <= 12:
                lv = blv[sd]
                if bfake[sd] and dj <= 3 and cc < lv:
                    m = put(ej, es, eid, eS, eT, m, j, -sg, 6, sg * (max(bext[sd], hh) + 0.1 * a), sg * bmid[sd])
                    bfake[sd] = False; bret[sd] = False
                elif cc < lv:
                    bret[sd] = False; bfake[sd] = False
                elif bret[sd] and ll <= lv + 0.1 * a:
                    m = put(ej, es, eid, eS, eT, m, j, sg, 3, sg * (ll - 0.1 * a), sg * (lv + bht[sd]))
                    bret[sd] = False
                if dj >= 3: bfake[sd] = False
                bext[sd] = max(bext[sd], hh)
            # ---------- 0 BRK_DON
            if sg == 1: dh, dl, dhp = dh48[j], dl48[j], dh48[j - 1]
            else: dh, dl, dhp = -dl48[j], -dh48[j], -dl48[j - 1]
            if np.isfinite(dh) and np.isfinite(dhp) and np.isfinite(cpp) and cc > dh and cpp <= dhp:
                lo2 = min(ll, L[j - 1] if sg == 1 else -H[j - 1])
                m = put(ej, es, eid, eS, eT, m, j, sg, 0, sg * (lo2 - 0.1 * a), sg * (dh + (dh - dl)))
                bj[sd] = j; blv[sd] = dh; bht[sd] = dh - dl; bmid[sd] = (dh + dl) / 2; bext[sd] = hh; bret[sd] = True; bfake[sd] = True
            # ---------- 1 BRK_PD and 7 FAKE_PD
            if sg == 1: ph_, pl_ = pdh[j], pdl[j]
            else: ph_, pl_ = -pdl[j], -pdh[j]
            if np.isfinite(ph_) and np.isfinite(pl_):
                dj = j - pj[sd]
                if pfake[sd] and 1 <= dj <= 3:
                    if cc < ph_:
                        if not done_fpd[sd]:
                            m = put(ej, es, eid, eS, eT, m, j, -sg, 7, sg * (max(pext[sd], hh) + 0.1 * a), sg * (ph_ + pl_) / 2)
                            done_fpd[sd] = True
                        pfake[sd] = False
                    pext[sd] = max(pext[sd], hh)
                    if dj >= 3: pfake[sd] = False
                if (not done_pd[sd]) and np.isfinite(cpp) and cc > ph_ and cpp <= ph_:
                    m = put(ej, es, eid, eS, eT, m, j, sg, 1, sg * (ll - 0.1 * a), sg * (ph_ + 0.5 * (ph_ - pl_)))
                    done_pd[sd] = True; pj[sd] = j; pext[sd] = hh; pfake[sd] = True
                # ---------- 8 SWEEP_PD (wick above PDH, close back below: short; mirrored for long at PDL)
                if (not done_spd[sd]) and oo <= ph_ and hh > ph_ and cc < ph_ and rvj >= 1.5:
                    m = put(ej, es, eid, eS, eT, m, j, -sg, 8, sg * (hh + 0.1 * a), sg * (ph_ + pl_) / 2)
                    done_spd[sd] = True
            # ---------- 2 BRK_ASIA and 9 SWEEP_ASIA (08:00-16:00 UTC)
            if sg == 1: ah, al = asiah[j], asial[j]
            else: ah, al = -asial[j], -asiah[j]
            if np.isfinite(ah) and np.isfinite(al) and mod[j] >= 480 and mod[j] < 960:
                if (not done_asia[sd]) and np.isfinite(cpp) and cc > ah and cpp <= ah:
                    m = put(ej, es, eid, eS, eT, m, j, sg, 2, sg * (ll - 0.1 * a), sg * (ah + (ah - al)))
                    done_asia[sd] = True
                if (not done_sasia[sd]) and oo <= ah and hh > ah and cc < ah and rvj >= 1.5:
                    m = put(ej, es, eid, eS, eT, m, j, -sg, 9, sg * (hh + 0.1 * a), sg * al)
                    done_sasia[sd] = True
            # ---------- 4 SQZ_BRK
            if sg == 1: d20, d20p = dh20[j], dh20[j - 1]
            else: d20, d20p = -dl20[j], -dl20[j - 1]
            if np.isfinite(d20) and np.isfinite(d20p) and np.isfinite(cpp) and cc > d20 and cpp <= d20p:
                cnt = 0; bh = -1e300; bl = 1e300; okb = True
                for q in range(j - 8, j):
                    if not (np.isfinite(bbu[q]) and np.isfinite(kcu[q])): okb = False; break
                    if bbu[q] < kcu[q] and bbl[q] > kcl[q]: cnt += 1
                    qh = H[q] if sg == 1 else -L[q]; ql = L[q] if sg == 1 else -H[q]
                    bh = max(bh, qh); bl = min(bl, ql)
                if okb and cnt >= 6:
                    m = put(ej, es, eid, eS, eT, m, j, sg, 4, sg * (bl - 0.1 * a), sg * (d20 + (bh - bl)))
            # ---------- 10 SWEEP_EQ (equal highs in the last 48 bars; short when swept, long at equal lows)
            if sg == 1: pp, pi = shp, shi
            else: pp, pi = -slp, sli
            if rvj >= 1.5:
                fired = False
                for q1 in range(NS):
                    if fired: break
                    if pi[q1] < 0 or pi[q1] < j - 48: continue
                    for q2 in range(q1 + 1, NS):
                        if pi[q2] < 0 or abs(pp[q1] - pp[q2]) > 0.1 * a: continue
                        pool = max(pp[q1], pp[q2]); first = min(pi[q1], pi[q2]); last = max(pi[q1], pi[q2])
                        if not (oo <= pool and hh > pool and cc < pool): continue
                        swept = False; lowest = 1e300
                        for q in range(first, j):
                            qh = H[q] if sg == 1 else -L[q]; ql = L[q] if sg == 1 else -H[q]
                            if q > last and qh > pool: swept = True; break
                            if np.isfinite(ql) and ql < lowest: lowest = ql
                        if swept: continue
                        m = put(ej, es, eid, eS, eT, m, j, -sg, 10, sg * (hh + 0.1 * a), sg * lowest)
                        fired = True; break
            # ---------- 11 SWEEP_SWING (newest swing high, at most 96 bars old)
            idx = pi[NS - 1]
            if idx >= 0 and j - idx <= 96 and ssw_done[sd] != idx and rvj >= 2.0:
                sh = pp[NS - 1]
                if oo <= sh and hh > sh and cc < sh:
                    swept = False
                    for q in range(idx + 1, j):
                        qh = H[q] if sg == 1 else -L[q]
                        if qh > sh: swept = True; break
                    if not swept:
                        tl = (slp[NS - 1] if sg == 1 else -shp[NS - 1])
                        m = put(ej, es, eid, eS, eT, m, j, -sg, 11, sg * (hh + 0.1 * a), sg * tl if np.isfinite(tl) else np.nan)
                        ssw_done[sd] = idx
            # ---------- 12 FIB_PB (leg: swing low -> swing high, newest pivot is the high)
            if sg == 1: hp, hi_, lp, li_ = shp[NS - 1], shi[NS - 1], slp[NS - 1], sli[NS - 1]
            else: hp, hi_, lp, li_ = -slp[NS - 1], sli[NS - 1], -shp[NS - 1], shi[NS - 1]
            if hi_ >= 0 and li_ >= 0 and hi_ > li_ and j - hi_ <= 96 and fib_done[sd] != hi_:
                leg = hp - lp
                if leg >= 3 * a:
                    bad = False
                    for q in range(hi_ + 1, j):
                        qh = H[q] if sg == 1 else -L[q]; ql = L[q] if sg == 1 else -H[q]
                        if qh > hp or ql < hp - 0.786 * leg: bad = True; break
                    if bad: fib_done[sd] = hi_
                    elif ll <= hp - 0.5 * leg and ll >= hp - 0.786 * leg and cc > hp - 0.618 * leg and cc > oo:
                        m = put(ej, es, eid, eS, eT, m, j, sg, 12, sg * (lp - 0.1 * a), sg * (hp + 0.272 * leg))
                        fib_done[sd] = hi_
            # ---------- 13 VWAP_PB
            if sg == 1: vw, vs = vwap[j], vsd[j]
            else: vw, vs = -vwap[j], vsd[j]
            if np.isfinite(vw) and day[j - 12] == day[j]:
                ok = True
                for q in range(j - 12, j):
                    cq = Cc[q] if sg == 1 else -Cc[q]; vq = vwap[q] if sg == 1 else -vwap[q]
                    if not (np.isfinite(cq) and np.isfinite(vq) and cq > vq): ok = False; break
                if ok and ll <= vw and cc > vw:
                    m = put(ej, es, eid, eS, eT, m, j, sg, 13, sg * (ll - 0.1 * a), sg * (vw + 2 * vs))
            # ---------- 14 EMA_PB
            if sg == 1: a20, a50, a200, a20p, lprev = e20[j], e50[j], e200[j], e20[j - 1], L[j - 1]
            else: a20, a50, a200, a20p, lprev = -e20[j], -e50[j], -e200[j], -e20[j - 1], -H[j - 1]
            if np.isfinite(a200) and a20 > a50 and a50 > a200 and ll <= a20 and cc > a20 and cc > oo and np.isfinite(lprev) and lprev > a20p:
                sw = lp if np.isfinite(lp) else ll
                tgt = hp if (np.isfinite(hp) and hp > cc) else np.nan
                m = put(ej, es, eid, eS, eT, m, j, sg, 14, sg * (min(ll, sw) - 0.1 * a), sg * tgt)
            # ---------- 15 ST_FLIP
            if stdir[j] == sg and stdir[j - 1] == -sg and np.isfinite(st[j]):
                m = put(ej, es, eid, eS, eT, m, j, sg, 15, st[j], np.nan)
            # ---------- 16 MACD_X
            if np.isfinite(hist[j]) and np.isfinite(hist[j - 1]) and np.isfinite(e200[j]) and sg * hist[j] > 0 and sg * hist[j - 1] <= 0 and sg * (c - e200[j]) > 0:
                lo10 = 1e300
                for q in range(j - 9, j + 1):
                    ql = L[q] if sg == 1 else -H[q]
                    if np.isfinite(ql) and ql < lo10: lo10 = ql
                tgt = hp if (np.isfinite(hp) and hp > cc) else np.nan
                m = put(ej, es, eid, eS, eT, m, j, sg, 16, sg * (lo10 - 0.1 * a), sg * tgt)
            # ---------- 17 CHOCH (for the long: lower highs and lower lows, then a close above the last swing high)
            if sg == 1: h1, h2, i2, l1, l2 = shp[NS - 2], shp[NS - 1], shi[NS - 1], slp[NS - 2], slp[NS - 1]
            else: h1, h2, i2, l1, l2 = -slp[NS - 2], -slp[NS - 1], sli[NS - 1], -shp[NS - 2], -shp[NS - 1]
            if i2 >= 0 and np.isfinite(h1) and np.isfinite(l1) and h2 < h1 and l2 < l1 and choch_done[sd] != i2 and np.isfinite(cpp):
                if cc > h2 and cpp <= h2:
                    m = put(ej, es, eid, eS, eT, m, j, sg, 17, sg * (l2 - 0.1 * a), sg * h1)
                    choch_done[sd] = i2
            # ---------- 18 RSI_DIV / 19 CVD_DIV (long: undercut of the newest swing low with a higher RSI / CVD)
            if sg == 1: sp, si, sr, sc_, rj, cj = slp[NS - 1], sli[NS - 1], slr[NS - 1], slc[NS - 1], rsi[j], cvd[j]
            else: sp, si, sr, sc_, rj, cj = -shp[NS - 1], shi[NS - 1], -shr[NS - 1], -shc[NS - 1], -rsi[j], -cvd[j]
            if si >= 0 and j - si <= 48 and ll < sp and cc > oo:
                tgt = hp if (np.isfinite(hp) and hp > cc) else np.nan
                if rdiv_done[sd] != si and np.isfinite(sr) and np.isfinite(rj) and rj > sr:
                    m = put(ej, es, eid, eS, eT, m, j, sg, 18, sg * (ll - 0.1 * a), sg * tgt); rdiv_done[sd] = si
                if cdiv_done[sd] != si and cj > sc_:
                    m = put(ej, es, eid, eS, eT, m, j, sg, 19, sg * (ll - 0.1 * a), sg * tgt); cdiv_done[sd] = si
            # ---------- 20 EXHAUST
            if np.isfinite(vw) and np.isfinite(vs) and vs > 0 and ll < vw - 3 * vs and rvj >= 3.0 and hh > ll and (min(oo, cc) - ll) >= 0.5 * (hh - ll):
                m = put(ej, es, eid, eS, eT, m, j, sg, 20, sg * (ll - 0.1 * a), sg * vw)
            # ---------- 21 OI_FLUSH (OI drop in the trailing-30-day lowest 2.5% with a 1.5 ATR fall over 3 bars: long; rise: short)
            if np.isfinite(oich[j]) and np.isfinite(oiq[j]) and oich[j] <= oiq[j] and np.isfinite(Cc[j - 3]) and np.isfinite(vw):
                if sg * (c - Cc[j - 3]) <= -1.5 * a and cc > oo:
                    m = put(ej, es, eid, eS, eT, m, j, sg, 21, sg * (ll - 0.25 * a), sg * vw)
            # ---------- 22 VP_FADE (long at VAL: open inside value, low reaches VAL, close back above)
            if sg == 1: vh, vl, pc_ = vah[j], val[j], poc[j]
            else: vh, vl, pc_ = -val[j], -vah[j], -poc[j]
            if np.isfinite(vh) and np.isfinite(vl) and (not done_vp[sd]) and oo > vl and oo < vh and ll <= vl and cc > vl:
                m = put(ej, es, eid, eS, eT, m, j, sg, 22, sg * (ll - 0.1 * a), sg * pc_)
                done_vp[sd] = True
            # ---------- 24 BB_FADE
            if sg == 1: bb_l, bb_m = bbl[j], bbm[j]
            else: bb_l, bb_m = -bbu[j], -bbm[j]
            if np.isfinite(bb_l) and np.isfinite(adx[j]) and adx[j] < 20 and ll < bb_l and cc > bb_l:
                m = put(ej, es, eid, eS, eT, m, j, sg, 24, sg * (ll - 0.1 * a), sg * bb_m)
            # ---------- 23 CONF_BOUNCE
            if hh > ll and (cc - ll) >= (2.0 / 3.0) * (hh - ll) and cc > oo:
                best = 0
                for q in range(NLEV):
                    lv = sg * lev[j, q]
                    if not np.isfinite(lv) or lv >= cc or abs(ll - lv) > 0.25 * a: continue
                    sc = 0
                    for r in range(NLEV):
                        lr = sg * lev[j, r]
                        if np.isfinite(lr) and abs(lr - lv) <= 0.25 * a: sc += 1
                    if sc > best: best = sc
                if best >= 3:
                    tgt = 1e300
                    for q in range(NLEV):
                        lv = sg * lev[j, q]
                        if not np.isfinite(lv) or lv <= cc: continue
                        sc = 0
                        for r in range(NLEV):
                            lr = sg * lev[j, r]
                            if np.isfinite(lr) and abs(lr - lv) <= 0.25 * a: sc += 1
                        if sc >= 2 and lv < tgt: tgt = lv
                    m = put(ej, es, eid, eS, eT, m, j, sg, 23, sg * (ll - 0.25 * a), sg * tgt if tgt < 1e299 else np.nan)
        # ---------- 5 ORB (one trade per session, the first close beyond the range within 2 hours)
        for s in range(3):
            if ordone[s] or orbad[s] or np.isnan(orh[s]): continue
            if mod[j] >= sess[s] + 15 and mod[j] + k <= sess[s] + 135:
                if c > orh[s]:
                    m = put(ej, es, eid, eS, eT, m, j, 1, 5, orl[s], orh[s] + (orh[s] - orl[s])); ordone[s] = True
                elif c < orl[s]:
                    m = put(ej, es, eid, eS, eT, m, j, -1, 5, orh[s], orl[s] - (orh[s] - orl[s])); ordone[s] = True
    m = min(m, cap)
    return ej[:m], es[:m], eid[:m], eS[:m], eT[:m]


def level_matrix(tf):
    """the 16 confluence levels per bar: PDH PDL PDC PWH PWL asiaH asiaL POC VAH VAL dVWAP wVWAP fib0.5 fib0.618 round-below round-above"""
    n = tf.n; lev = np.full((n, NLEV), np.nan)
    for q, x in enumerate((tf.pdh, tf.pdl, tf.pdc, tf.pwh, tf.pwl, tf.asiah, tf.asial, tf.poc, tf.vah, tf.val, tf.vwap, tf.wvwap)):
        lev[:, q] = x
    f5, f6 = fib_levels(tf.H, tf.L, tf.ph, tf.pl)
    lev[:, 12] = f5; lev[:, 13] = f6
    with np.errstate(invalid="ignore", divide="ignore"):
        u = 10.0 ** (np.floor(np.log10(tf.C)) - 1)
        lev[:, 14] = np.floor(tf.C / u) * u; lev[:, 15] = np.ceil(tf.C / u) * u
    return lev


@nb.njit(cache=True)
def fib_levels(H, L, ph, pl):
    """0.5 and 0.618 retracements of the newest confirmed leg (between the newest swing high and the newest swing low), known at j"""
    n = len(H); f5 = np.full(n, np.nan); f6 = np.full(n, np.nan)
    hp = np.nan; hi = -1; lp = np.nan; li = -1
    for j in range(3, n):
        p = j - 3
        if ph[p]: hp = H[p]; hi = p
        if pl[p]: lp = L[p]; li = p
        if hi >= 0 and li >= 0:
            leg = hp - lp
            if hi > li: f5[j] = hp - 0.5 * leg; f6[j] = hp - 0.618 * leg
            else: f5[j] = lp + 0.5 * leg; f6[j] = lp + 0.618 * leg
    return f5, f6


def scan_tf(tf):
    lev = level_matrix(tf)
    ej, es, eid, eS, eT = scan(tf.k, tf.O, tf.H, tf.L, tf.C, tf.atr, tf.relvol, tf.e20, tf.e50, tf.e200, tf.rsi, tf.adx, tf.bbm, tf.bbu,
                               tf.bbl, tf.kcu, tf.kcl, tf.dh48, tf.dl48, tf.dh20, tf.dl20, tf.st, tf.stdir, tf.hist, tf.cvd, tf.ph, tf.pl,
                               tf.vwap, tf.vsd, tf.wvwap, tf.pdh, tf.pdl, tf.pdc, tf.poc, tf.vah, tf.val, tf.asiah, tf.asial, tf.pwh,
                               tf.pwl, tf.day, tf.mod, tf.oich, tf.oiq, lev)
    # RANDOM control
    rng = np.random.default_rng(abs(hash(tf.name)) % (2 ** 31) + tf.k)
    ok = np.isfinite(tf.C) & np.isfinite(tf.atr)
    ok[:210] = False
    jr = np.nonzero(ok & (rng.random(tf.n) < tf.k / 240.0))[0]
    sr = np.where(rng.random(len(jr)) < 0.5, 1, -1)
    Sr = tf.C[jr] - sr * tf.atr[jr]
    ej = np.concatenate([ej, jr]); es = np.concatenate([es, sr]); eid = np.concatenate([eid, np.full(len(jr), 25)])
    eS = np.concatenate([eS, Sr]); eT = np.concatenate([eT, np.full(len(jr), np.nan)])
    o = np.lexsort((eid, ej))
    return ej[o], es[o], eid[o], eS[o], eT[o], lev


# ------------------------------------------------------------------ trade walk on 1m bars
@nb.njit(cache=True)
def walk(o, h, l, c, i1, last, side, stop, tgt, trail):
    """returns (exit 1m index, exit price, reason: 0 time, 1 stop, 2 target). trail > 0: chandelier at the extreme since entry - trail"""
    ext = -1e300
    st = stop
    for b in range(i1, last + 1):
        if np.isnan(h[b]) or np.isnan(l[b]) or np.isnan(o[b]): continue
        if side == 1:
            if b > i1 and o[b] <= st: return b, o[b], 1
            if l[b] <= st: return b, st, 1
            if tgt > 0 and h[b] >= tgt: return b, max(tgt, o[b]) if b > i1 else tgt, 2
            if trail > 0:
                if h[b] > ext: ext = h[b]
                st = max(st, ext - trail)
        else:
            if b > i1 and o[b] >= st: return b, o[b], 1
            if h[b] >= st: return b, st, 1
            if tgt > 0 and l[b] <= tgt: return b, min(tgt, o[b]) if b > i1 else tgt, 2
            if trail > 0:
                if ext < -1e299 or l[b] < ext: ext = l[b]
                st = min(st, ext + trail)
    for b in range(last, i1 - 1, -1):
        if not np.isnan(c[b]): return b, c[b], 0
    return -1, 0.0, -1


@nb.njit(cache=True)
def simulate(o, h, l, c, k, ej, es, eS, eT, atr, t0, ft, fr, maker, pen_atr):
    """every event x 4 exits. returns: entry_ms, stopfrac, d_atr, ok, and per exit: exit_ms, gross, funding, reason.
    maker: limit entry at the signal close, filled only on a trade-through of max(pen_atr ATR, 1bp) within 3 bars (else no trade)."""
    n = len(ej); N = len(o)
    ent = np.zeros(n, np.int64); sf = np.full(n, np.nan); datr = np.full(n, np.nan); ok = np.zeros(n, np.bool_)
    xms = np.zeros((n, 4), np.int64); gr = np.full((n, 4), np.nan); fu = np.zeros((n, 4)); rs = np.full((n, 4), -1, np.int64)
    for e in range(n):
        j = ej[e]; side = es[e]; i1 = (j + 1) * k
        if i1 >= N or np.isnan(o[i1]): continue
        a = atr[j]; pe = o[i1]
        if maker:
            Lm = c[i1 - 1]; pen = max(pen_atr * a * (5.0 / k) ** 0.5, 1e-4 * Lm)   # round-1 rule (0.05 ATR of 5m bars), ATR scaled to 5m
            fill = -1
            for b in range(i1, min(i1 + 3, N)):
                if np.isnan(l[b]): continue
                if (side == 1 and l[b] <= Lm - pen) or (side == -1 and h[b] >= Lm + pen): fill = b; break
            if fill < 0: continue
            i1 = fill; pe = Lm
        d = side * (pe - eS[e])
        if d <= 0: continue
        dmin = max(0.5 * a, 0.0015 * pe); dmax = min(4.0 * a, 0.04 * pe)
        if d > dmax: continue
        if d < dmin: d = dmin
        stop = pe - side * d
        last = min(i1 + 48 * k - 1, N - 1)
        T = eT[e]
        lvl = 0.0
        if np.isfinite(T) and side * (T - pe) >= d: lvl = T
        elif np.isfinite(T): lvl = pe + side * d
        else: lvl = pe + side * 2 * d
        tg = (pe + side * 1.5 * d, pe + side * 3.0 * d, lvl, 0.0)
        ok[e] = True; ent[e] = t0 + i1 * 60000; sf[e] = d / pe; datr[e] = d / a
        for x in range(4):
            tr = 3.0 * a if x == 3 else 0.0
            xb, px, r = walk(o, h, l, c, i1, last, side, stop, tg[x], tr)
            if r < 0: ok[e] = False; break
            gr[e, x] = side * (px / pe - 1.0)
            te = t0 + i1 * 60000; tx = t0 + (xb + 1) * 60000
            xms[e, x] = tx; fu[e, x] = C.funding_paid(ft, fr, te, tx, side); rs[e, x] = r
    return ent, sf, datr, ok, xms, gr, fu, rs


# ------------------------------------------------------------------ per coin
def features(tf, ej, es, lev, ft, fr, ent):
    """ML features known at the signal close (PREREG_round2.md, kitchen sink)"""
    j = ej; a = tf.atr[j]; c = tf.C[j]
    with np.errstate(invalid="ignore", divide="ignore"):
        L = lev[j]; within = np.abs(L - c[:, None]) <= 0.25 * a[:, None]
        conf = within.sum(1)
        def dist(x): return es * (c - x[j]) / a
        k_last = np.searchsorted(ft, ent, side="right") - 1
        fund = np.where(k_last >= 0, fr[np.clip(k_last, 0, len(fr) - 1)], np.nan)
        hour = ((tf.t[j] // 3_600_000) % 24).astype(float)
        wd = (((tf.t[j] // C.DAY) + 4) % 7).astype(float)
        X = np.column_stack([tf.regime[j], tf.voltag[j], tf.adx1h[j], tf.relvol[j], tf.rsi[j] * es, tf.adx[j], dist(tf.vwap), dist(tf.pdh),
                             dist(tf.pdl), dist(tf.poc), dist(tf.e200), conf, hour, wd, fund * es, tf.oich[j] * es, tf.btc1h[j] * es,
                             tf.btc24h[j] * es, tf.r24h[j] * es, tf.r1h[j] * es, a / c])
    return X.astype(np.float32)


FEAT_NAMES = ["regime", "voltag", "adx1h", "relvol", "rsi_s", "adx", "d_vwap", "d_pdh", "d_pdl", "d_poc", "d_e200", "conf", "hour", "wd",
              "fund_s", "oich_s", "btc1h_s", "btc24h_s", "r24h_s", "r1h_s", "atrp"]


def run_coin(name, btc_c=None, ks=(5, 15)):
    co = C.load_coin(name); ft, fr = C.load_funding(name)
    try: oi = C.load_oi(name)
    except Exception: oi = None
    os.makedirs(OUT, exist_ok=True)
    for k in ks:
        tf = FT.TF(co, k, oi, btc_c)
        ej, es, eid, eS, eT, lev = scan_tf(tf)
        ent, sf, datr, ok, xms, gr, fu, rs = simulate(co.o, co.h, co.l, co.c, k, ej, es, eS, eT, tf.atr, co.t0, ft, fr, False, 0.05)
        X = features(tf, ej, es, lev, ft, fr, ent)
        m = ok
        np.savez_compressed(os.path.join(OUT, f"ev_{name}_{k}.npz"), j=ej[m], side=es[m], sid=eid[m], S=eS[m], T=eT[m], ent=ent[m],
                            sf=sf[m], datr=datr[m], xms=xms[m], gr=gr[m], fu=fu[m], rs=rs[m], regime=tf.regime[ej[m]],
                            relvol=tf.relvol[ej[m]], X=X[m])
        print(name, k, "events", len(ej), "simulated", int(m.sum()), flush=True)


if __name__ == "__main__":
    names = sys.argv[1].split(",")
    btc = C.load_coin("BTC").c
    for nm in names: run_coin(nm, btc)
