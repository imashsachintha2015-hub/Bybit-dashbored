"""Round 2 (PREREG_round2.md): bars, indicators, levels and regime tags at 5m / 15m resolution, all causal.
Index j = signal bar j of k minutes, covering 1m indices [j*k, (j+1)*k); it closes at 1m index (j+1)*k - 1 and a trade enters at the
1m open of index (j+1)*k. Every array here holds, at j, only what is known at the close of bar j."""
import numpy as np
import numba as nb
import hf_core as C
import hf_fam as F

PER_DAY = 1440


# ------------------------------------------------------------------ 1m-level anchored statistics
@nb.njit(cache=True)
def anchored_vwap(h, l, c, qv, period, offset):
    """VWAP and volume-weighted sd anchored every `period` minutes (offset shifts the anchor), at every 1m index (inclusive)"""
    n = len(c); vw = np.full(n, np.nan); sd = np.full(n, np.nan)
    sv = 0.0; spv = 0.0; spv2 = 0.0
    for i in range(n):
        if (i + offset) % period == 0: sv = 0.0; spv = 0.0; spv2 = 0.0
        if np.isfinite(c[i]) and np.isfinite(qv[i]) and qv[i] > 0:
            tp = (h[i] + l[i] + c[i]) / 3.0
            v = qv[i] / tp
            sv += v; spv += v * tp; spv2 += v * tp * tp
        if sv > 0:
            m = spv / sv; vw[i] = m
            var = spv2 / sv - m * m
            sd[i] = np.sqrt(var) if var > 0 else 0.0
    return vw, sd


@nb.njit(cache=True)
def daily_levels(h, l, c, qv, nbins):
    """per UTC day d (index of the grid day): high, low, close, POC, VAH, VAL of day d itself, and the Asia (00-08 UTC) high/low.
    NaN when the day has fewer than 1300 valid minutes (Asia: 440 of 480)."""
    nd = len(c) // PER_DAY
    out = np.full((nd, 8), np.nan)       # 0 H, 1 L, 2 C, 3 POC, 4 VAH, 5 VAL, 6 asiaH, 7 asiaL
    vol = np.zeros(nbins)
    for d in range(nd):
        a = d * PER_DAY; hi = -1e300; lo = 1e300; cnt = 0; last = np.nan; ah = -1e300; al = 1e300; acnt = 0
        for i in range(a, a + PER_DAY):
            if np.isfinite(h[i]) and np.isfinite(l[i]) and np.isfinite(c[i]):
                cnt += 1; last = c[i]
                if h[i] > hi: hi = h[i]
                if l[i] < lo: lo = l[i]
                if i - a < 480:
                    acnt += 1
                    if h[i] > ah: ah = h[i]
                    if l[i] < al: al = l[i]
        if acnt >= 440: out[d, 6] = ah; out[d, 7] = al
        if cnt < 1300 or hi <= lo: continue
        out[d, 0] = hi; out[d, 1] = lo; out[d, 2] = last
        w = (hi - lo) / nbins
        vol[:] = 0.0
        for i in range(a, a + PER_DAY):
            if not (np.isfinite(h[i]) and np.isfinite(l[i]) and np.isfinite(qv[i])): continue
            b0 = int((l[i] - lo) / w); b1 = int((h[i] - lo) / w)
            b0 = min(max(b0, 0), nbins - 1); b1 = min(max(b1, 0), nbins - 1)
            share = qv[i] / (b1 - b0 + 1)
            for b in range(b0, b1 + 1): vol[b] += share
        tot = vol.sum()
        if tot <= 0: continue
        p = int(np.argmax(vol)); lo_b = p; hi_b = p; acc = vol[p]
        while acc < 0.7 * tot and (lo_b > 0 or hi_b < nbins - 1):
            up = vol[hi_b + 1] if hi_b < nbins - 1 else -1.0
            dn = vol[lo_b - 1] if lo_b > 0 else -1.0
            if up >= dn: hi_b += 1; acc += up
            else: lo_b -= 1; acc += dn
        out[d, 3] = lo + (p + 0.5) * w; out[d, 4] = lo + (hi_b + 1) * w; out[d, 5] = lo + lo_b * w
    return out


# ------------------------------------------------------------------ TF-level indicators
@nb.njit(cache=True)
def supertrend(h, l, c, atr, mult):
    n = len(c); line = np.full(n, np.nan); dirn = np.zeros(n, np.int64)
    up = np.nan; dn = np.nan; d = 1
    for i in range(n):
        if not (np.isfinite(h[i]) and np.isfinite(l[i]) and np.isfinite(c[i]) and np.isfinite(atr[i])):
            dirn[i] = 0; continue
        mid = (h[i] + l[i]) / 2.0
        bu = mid - mult * atr[i]; bd = mid + mult * atr[i]
        pc = c[i - 1] if i > 0 else np.nan
        if np.isnan(up): up = bu
        elif np.isfinite(pc) and pc > up: up = max(bu, up)
        else: up = bu
        if np.isnan(dn): dn = bd
        elif np.isfinite(pc) and pc < dn: dn = min(bd, dn)
        else: dn = bd
        if d == 1 and c[i] < up: d = -1
        elif d == -1 and c[i] > dn: d = 1
        dirn[i] = d; line[i] = up if d == 1 else dn
    return line, dirn


@nb.njit(cache=True)
def rolling_mean_std_strict(x, w):
    """trailing mean / population sd over exactly the last w values (NaN if any is missing)"""
    n = len(x); mu = np.full(n, np.nan); sd = np.full(n, np.nan)
    for i in range(w - 1, n):
        s = 0.0; s2 = 0.0; ok = True
        for k in range(i - w + 1, i + 1):
            if not np.isfinite(x[k]): ok = False; break
            s += x[k]; s2 += x[k] * x[k]
        if ok:
            m = s / w; v = s2 / w - m * m; mu[i] = m; sd[i] = np.sqrt(v) if v > 0 else 0.0
    return mu, sd


@nb.njit(cache=True)
def pivots(h, l, k):
    """swing high/low flags at the pivot bar p (extreme of p-k..p+k, first of ties); they become known at p+k"""
    n = len(h); ph = np.zeros(n, np.bool_); pl = np.zeros(n, np.bool_)
    for p in range(k, n - k):
        okh = np.isfinite(h[p]); okl = np.isfinite(l[p])
        for q in range(p - k, p + k + 1):
            if not (np.isfinite(h[q]) and np.isfinite(l[q])): okh = False; okl = False; break
            if q < p:
                if h[q] >= h[p]: okh = False
                if l[q] <= l[p]: okl = False
            elif q > p:
                if h[q] > h[p]: okh = False
                if l[q] < l[p]: okl = False
        ph[p] = okh; pl[p] = okl
    return ph, pl


def prev_window_max(x, w):
    """max of x[j-w .. j-1] (excludes bar j)"""
    return F.shift(F.rolling_max(x, w), 1)


def prev_window_min(x, w):
    return F.shift(F.rolling_min(x, w), 1)


class TF:
    """all signal-bar arrays of one coin at one timeframe k (5 or 15 minutes)"""
    def __init__(self, co, k, oi=None, btc_c1m=None):
        self.k = k; self.name = co.name
        O, H, L, Cc, Q, NN, TB = C.aggregate(co.o, co.h, co.l, co.c, co.qv, co.n, co.tb, k)
        self.O, self.H, self.L, self.C, self.Q, self.TB = O, H, L, Cc, Q, TB
        n = len(O); self.n = n
        self.t = co.t0 + np.arange(n, dtype=np.int64) * k * C.MIN               # bar open time
        self.close_1m = (np.arange(n, dtype=np.int64) + 1) * k - 1              # 1m index of the bar's last minute
        self.atr = C.atr_wilder(H, L, Cc, 14)
        self.e20 = C.ema(Cc, 20); self.e50 = C.ema(Cc, 50); self.e200 = C.ema(Cc, 200)
        self.rsi = F.rsi_wilder(Cc, 14)
        self.adx = F.adx(H, L, Cc, 14)
        self.bbm, sd = rolling_mean_std_strict(Cc, 20)
        self.bbu = self.bbm + 2 * sd; self.bbl = self.bbm - 2 * sd
        self.kcu = self.e20 + 1.5 * self.atr; self.kcl = self.e20 - 1.5 * self.atr
        self.dh48 = prev_window_max(H, 48); self.dl48 = prev_window_min(L, 48)
        self.dh20 = prev_window_max(H, 20); self.dl20 = prev_window_min(L, 20)
        self.st, self.stdir = supertrend(H, L, Cc, C.atr_wilder(H, L, Cc, 10), 3.0)
        m = C.ema(Cc, 12) - C.ema(Cc, 26); self.hist = m - C.ema(m, 9)
        qm = F.shift(F.rolling_sum(Q, 20), 1) / 20.0
        with np.errstate(invalid="ignore", divide="ignore"): self.relvol = Q / qm
        delta = np.where(np.isfinite(TB) & np.isfinite(Q), 2 * TB - Q, 0.0)
        self.cvd = np.cumsum(delta)
        self.ph, self.pl = pivots(H, L, 3)
        # daily / weekly anchored VWAP at the bar close
        vw, vsd = anchored_vwap(co.h, co.l, co.c, co.qv, PER_DAY, 0)
        ww, wsd = anchored_vwap(co.h, co.l, co.c, co.qv, 7 * PER_DAY, 4 * PER_DAY)   # grid day 0 = Friday; anchors on Mondays 00:00 UTC
        ci = self.close_1m
        self.vwap, self.vsd, self.wvwap = vw[ci], vsd[ci], ww[ci]
        # previous-day levels, Asia range (usable from 08:00 UTC), previous-week high/low
        dl = daily_levels(co.h, co.l, co.c, co.qv, 100)
        day = (np.arange(n, dtype=np.int64) * k) // PER_DAY
        self.day = day; self.mod = (np.arange(n, dtype=np.int64) * k) % PER_DAY      # minute of day of the bar open
        prev = np.clip(day - 1, 0, len(dl) - 1); okp = day >= 1
        def pd(col): return np.where(okp, dl[prev, col], np.nan)
        self.pdh, self.pdl, self.pdc, self.poc, self.vah, self.val = (pd(i) for i in range(6))
        cur = np.clip(day, 0, len(dl) - 1)
        asia_ok = self.mod + k > 480                                            # the bar closes after 08:00
        asia_ok &= self.mod >= 480
        self.asiah = np.where(asia_ok, dl[cur, 6], np.nan); self.asial = np.where(asia_ok, dl[cur, 7], np.nan)
        nd = len(dl); week = (np.arange(nd) + 4) // 7
        nw = week.max() + 1; wh = np.full(nw, np.nan); wl = np.full(nw, np.nan)
        for w in range(nw):
            sel = dl[week == w]
            if np.isfinite(sel[:, 0]).sum() >= 5: wh[w] = np.nanmax(sel[:, 0]); wl[w] = np.nanmin(sel[:, 1])
        bw = (day + 4) // 7; okw = bw >= 1; pw = np.clip(bw - 1, 0, nw - 1)
        self.pwh = np.where(okw, wh[pw], np.nan); self.pwl = np.where(okw, wl[pw], np.nan)
        # 1H regime: last completed 1H bar at this bar's close
        O1, H1, L1, C1, *_ = C.aggregate(co.o, co.h, co.l, co.c, co.qv, co.n, co.tb, 60)
        e50 = C.ema(C1, 50); e200 = C.ema(C1, 200); ax = F.adx(H1, L1, C1, 14); a1 = C.atr_wilder(H1, L1, C1, 14)
        hb = (ci + 1) // 60 - 1; hb = np.clip(hb, 0, len(C1) - 1)
        rise = e50 - F.shift(e50, 5)
        up = (C1 > e200) & (e50 > e200) & (rise > 0) & (ax >= 20)
        dn = (C1 < e200) & (e50 < e200) & (rise < 0) & (ax >= 20)
        rng = ax < 20
        reg1 = np.where(up, 0, np.where(dn, 1, np.where(rng, 2, 3)))
        reg1[200:] = reg1[200:]; reg1[:200] = 3
        self.regime = reg1[hb]
        self.adx1h = ax[hb]
        with np.errstate(invalid="ignore", divide="ignore"): atrp = a1 / C1
        med = F.daily_quantile(atrp, 24, 720, 0.5)
        with np.errstate(invalid="ignore", divide="ignore"): ratio = atrp / med
        self.voltag = np.where(ratio[hb] < 0.8, 0, np.where(ratio[hb] > 1.25, 2, 1))
        # returns for the ML features: own 1h / 24h, BTC 1h / 24h, all at the bar close
        def ret_back(c1m, mins):
            prevc = np.full(len(c1m), np.nan); prevc[mins:] = c1m[:-mins]
            with np.errstate(invalid="ignore", divide="ignore"): r = c1m / prevc - 1
            return r[ci]
        self.r1h = ret_back(co.c, 60); self.r24h = ret_back(co.c, 1440)
        if btc_c1m is not None: self.btc1h = ret_back(btc_c1m, 60); self.btc24h = ret_back(btc_c1m, 1440)
        else: self.btc1h = self.r1h; self.btc24h = self.r24h
        # open interest change over the bar: the newest known stamp is (close - 5 min); OI stamped T is known at T + 5 min
        self.oich = np.full(n, np.nan); self.oiq = np.full(n, np.nan)
        if oi is not None:
            s_new = (ci + 1) // 5 - 1; s_old = s_new - k // 5
            okk = (s_old >= 0) & (s_new < len(oi))
            a = np.full(n, np.nan); b = np.full(n, np.nan)
            a[okk] = oi[s_new[okk]]; b[okk] = oi[s_old[okk]]
            with np.errstate(invalid="ignore", divide="ignore"): ch = np.log(a / b)
            ch[~np.isfinite(ch)] = np.nan
            self.oich = ch
            per_day = PER_DAY // k
            self.oiq = F.daily_quantile(ch, per_day, 30 * per_day, 0.025)
            self.oiqh = F.daily_quantile(ch, per_day, 30 * per_day, 0.975)
        else:
            self.oiqh = np.full(n, np.nan)
