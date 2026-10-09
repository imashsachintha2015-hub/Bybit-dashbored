"""Signal generators of the 12 families (PREREG_scalp_hf.md + Addendum 1). Every generator returns, per variant key,
(idx, side[, adj_bps]) where idx is the 1m index of the closed signal bar (the entry is the next open).
Nothing here looks at a bar that is not closed."""
import datetime
from zoneinfo import ZoneInfo
import numpy as np
import numba as nb
import hf_core as C

NY = ZoneInfo("America/New_York")


# ------------------------------------------------------------------ helpers
@nb.njit(cache=True)
def rolling_sum(x, w):
    """trailing sum over w values; NaN if any value in the window is missing or non-finite (re-synced every 4096 steps)"""
    n = len(x); out = np.full(n, np.nan); s = 0.0; bad = 0
    for i in range(n):
        if i % 4096 == 0:
            s = 0.0; bad = 0
            for k in range(max(0, i - w + 1), i + 1):
                if np.isfinite(x[k]): s += x[k]
                else: bad += 1
        else:
            if np.isfinite(x[i]): s += x[i]
            else: bad += 1
            j = i - w
            if j >= 0:
                if np.isfinite(x[j]): s -= x[j]
                else: bad -= 1
        if i >= w - 1 and bad == 0: out[i] = s
    return out


@nb.njit(cache=True)
def rolling_max(x, w):
    n = len(x); out = np.full(n, np.nan)
    for i in range(w - 1, n):
        m = -1e300; ok = True
        for k in range(i - w + 1, i + 1):
            if np.isnan(x[k]): ok = False; break
            if x[k] > m: m = x[k]
        if ok: out[i] = m
    return out


@nb.njit(cache=True)
def rolling_min(x, w):
    n = len(x); out = np.full(n, np.nan)
    for i in range(w - 1, n):
        m = 1e300; ok = True
        for k in range(i - w + 1, i + 1):
            if np.isnan(x[k]): ok = False; break
            if x[k] < m: m = x[k]
        if ok: out[i] = m
    return out


def shift(x, k=1):
    out = np.full_like(x, np.nan)
    out[k:] = x[:-k]
    return out


@nb.njit(cache=True)
def daily_quantile(x, per_day, lookback, q):
    """threshold at every index = quantile q of the `lookback` values BEFORE the start of the index's day (constant within a day)"""
    n = len(x); out = np.full(n, np.nan)
    for d0 in range(0, n, per_day):
        a = d0 - lookback
        if a < 0: continue
        w = x[a:d0]
        w = w[~np.isnan(w)]
        if len(w) < lookback // 2: continue
        thr = np.quantile(w, q)
        for j in range(d0, min(d0 + per_day, n)): out[j] = thr
    return out


def trigger(cond):
    """indices where cond turns true"""
    cond = np.asarray(cond, dtype=bool)
    prev = np.r_[False, cond[:-1]]
    return np.nonzero(cond & ~prev)[0]


def thin(idx, side, minutes=15, adj=None):
    """15-minute cooldown per coin"""
    if len(idx) == 0: return idx.astype(np.int64), side, adj
    keep = C.cooldown(idx.astype(np.int64), minutes)
    return idx[keep].astype(np.int64), side[keep], (adj[keep] if adj is not None else None)


def logret(c, m):
    r = np.full_like(c, np.nan)
    r[m:] = np.log(c[m:] / c[:-m])
    return r


class Prep:
    """derived arrays of one coin, computed once"""
    def __init__(self, coin):
        self.coin = coin
        o, h, l, c, qv, n, tb = C.aggregate(coin.o, coin.h, coin.l, coin.c, coin.qv, coin.n, coin.tb, 5)
        self.b5 = dict(o=o, h=h, l=l, c=c, qv=qv, n=n, tb=tb)
        self.atr5 = C.atr_wilder(h, l, c, 14)
        self.atr1 = C.to_1m(self.atr5, 5, coin.N)


# ------------------------------------------------------------------ 1. FUND_SETTLE
def fam_fund_settle(pr, ft, fr):
    """hold the side that receives funding from 2 minutes before a settlement; adj = the settlement's funding received, in bps"""
    co = pr.coin; out = {}
    prev = np.r_[np.nan, fr[:-1]]
    for f_bps in (5, 10, 20, 30):
        sel = np.abs(prev) >= f_bps / 1e4
        T = ft[sel]
        i = (T - 2 * C.MIN - co.t0) // C.MIN - 1                           # signal bar: the minute before the entry minute
        ok = (i > 0) & (i < co.N - 200)
        side = np.where(prev[sel] > 0, -1, 1).astype(np.int64)[ok]          # positive funding: shorts receive
        adj = (-side * fr[sel][ok] * 1e4)                                    # funding received, bps
        out[("f", f_bps)] = (i[ok].astype(np.int64), side, adj)
    return out


# ------------------------------------------------------------------ 2. BTC_LEAD
def fam_btc_lead(prb, pr):
    out = {}
    cb, ca = prb.coin.c, pr.coin.c
    for m in (1, 3, 5):
        rb = logret(cb, m); ra = logret(ca, m)
        _, sd = C.rolling_mean_std(rb, 60)
        z = rb / shift(sd, 1)
        az = np.abs(np.nan_to_num(z, nan=0.0))
        for k in (2, 3, 4):
            idx = trigger(az >= k)
            sd_ = np.sign(rb[idx]).astype(np.int64)
            nz = sd_ != 0; idx = idx[nz]; sd_ = sd_[nz]
            for lag in (0, 1):
                keep = np.ones(len(idx), bool)
                if lag: keep = (sd_ * ra[idx] < sd_ * rb[idx]) & ~np.isnan(ra[idx])
                i2, s2, _ = thin(idx[keep], sd_[keep], 15)
                out[("m", m, "k", k, "lag", lag)] = (i2, s2, None)
    return out


# ------------------------------------------------------------------ 3. FLOW
def fam_flow(pr):
    co = pr.coin; out = {}
    for n_ in (3, 10, 30):
        qs = rolling_sum(co.qv, n_); ts_ = rolling_sum(co.tb, n_)
        imb = (2 * ts_ - qs) / qs
        mu, sd = C.rolling_mean_std(imb, 1440)
        z = (imb - shift(mu, 1)) / shift(sd, 1)
        qmu, _ = C.rolling_mean_std(qs, 1440)
        volx = qs / shift(qmu, 1)
        hi = rolling_max(co.c, n_); lo = rolling_min(co.c, n_)
        for k in (2, 3):
            idx = trigger(np.abs(np.nan_to_num(z, nan=0.0)) >= k)
            s_ = np.sign(imb[idx]).astype(np.int64)
            i2, s2, _ = thin(idx, s_, 15)
            out[("n", n_, "k", k, "cont")] = (i2, s2, None)
            at_ext = ((s_ > 0) & (co.c[idx] >= hi[idx])) | ((s_ < 0) & (co.c[idx] <= lo[idx]))
            ex = at_ext & (volx[idx] >= 2.0)
            i3, s3, _ = thin(idx[ex], -s_[ex], 15)
            out[("n", n_, "k", k, "exh")] = (i3, s3, None)
    return out


# ------------------------------------------------------------------ 4. OI_FLUSH (5m bars)
def fam_oi(pr, oi):
    b = pr.b5; co = pr.coin; out = {}
    C5, H5, L5, atr = b["c"], b["h"], b["l"], pr.atr5
    nb5 = min(len(oi), len(C5))
    oi = oi[:nb5]; C5 = C5[:nb5]; H5 = H5[:nb5]; L5 = L5[:nb5]; atr = atr[:nb5]
    hi6 = shift(rolling_max(H5, 6), 1); lo6 = shift(rolling_min(L5, 6), 1)
    for w in (1, 3):
        d_oi = np.full(nb5, np.nan); d_oi[w:] = oi[w:] / oi[:-w] - 1
        dp = np.full(nb5, np.nan); dp[w:] = C5[w:] - C5[:-w]
        for pct in (97.5, 99.0):
            q_lo = daily_quantile(d_oi, 288, 8640, (100 - pct) / 100)
            q_hi = daily_quantile(d_oi, 288, 8640, pct / 100)
            drop = d_oi <= q_lo
            big_up = (np.abs(np.nan_to_num(dp)) >= atr) & (dp > 0); big_dn = (np.abs(np.nan_to_num(dp)) >= atr) & (dp < 0)
            ev = np.nonzero(drop & (big_dn | big_up))[0]
            sd_ = np.where(dp[ev] < 0, 1, -1).astype(np.int64)                       # flush -> buy, squeeze -> sell
            i2, s2, _ = thin(ev * 5 + 4, sd_, 30)
            out[("w", w, "pct", pct, "flush")] = (i2, s2, None)
            rise = d_oi >= q_hi
            brk_up = C5 > hi6; brk_dn = C5 < lo6
            ev = np.nonzero(rise & (brk_up | brk_dn))[0]
            sd_ = np.where(brk_up[ev], 1, -1).astype(np.int64)
            i3, s3, _ = thin(ev * 5 + 4, sd_, 30)
            out[("w", w, "pct", pct, "build")] = (i3, s3, None)
    return out


# ------------------------------------------------------------------ 6. ORB
def session_starts(co):
    """UTC ms of the three session opens for every day of the grid: 00:00 UTC, 08:00 UTC, 09:30 New York"""
    days = np.arange(co.t0 // C.DAY, (co.t0 + co.N * C.MIN) // C.DAY)
    s0 = days * C.DAY; s1 = s0 + 8 * 3600_000
    s2 = np.empty(len(days), dtype=np.int64)
    for k, d in enumerate(days):
        dt = datetime.datetime.fromtimestamp(int(d) * 86400, datetime.timezone.utc)
        s2[k] = int(datetime.datetime(dt.year, dt.month, dt.day, 9, 30, tzinfo=NY).astimezone(datetime.timezone.utc).timestamp() * 1000)
    return {"utc00": s0, "utc08": s1, "ny0930": s2}


def fam_orb(pr, sess):
    co = pr.coin; out = {}
    for name, S in sess.items():
        for R in (15, 30):
            ii, ss = [], []
            for t in S:
                a = (t - co.t0) // C.MIN
                if a < 0 or a + R + 60 >= co.N: continue
                hh = co.h[a:a + R]; ll = co.l[a:a + R]
                if np.isnan(hh).any() or np.isnan(ll).any(): continue
                H, L = hh.max(), ll.min()
                seg = co.c[a + R:a + R + 60]
                up = np.nonzero(seg > H)[0]; dn = np.nonzero(seg < L)[0]
                u = up[0] if len(up) else 10 ** 9; d = dn[0] if len(dn) else 10 ** 9
                if u == d == 10 ** 9: continue
                ii.append(a + R + min(u, d)); ss.append(1 if u < d else -1)
            out[(name, "R", R)] = (np.array(ii, dtype=np.int64), np.array(ss, dtype=np.int64), None)
    return out


# ------------------------------------------------------------------ 7. VWAP_FADE
def fam_vwap(pr):
    co = pr.coin; out = {}
    num = rolling_sum(co.qv, 60); den = rolling_sum(co.qv / co.c, 60)
    with np.errstate(all="ignore"):
        vwap = np.where((num > 0) & (den > 0), num / den, np.nan)
        dev = (co.c - vwap) / vwap
    _, sd = C.rolling_mean_std(dev, 1440)
    z = dev / shift(sd, 1)
    q5 = rolling_sum(co.qv, 5); falling = q5 < shift(q5, 5)
    for k in (2, 3, 4):
        base = np.abs(np.nan_to_num(z, nan=0.0)) >= k
        for vol in (0, 1):
            cond = base & falling if vol else base
            idx = trigger(cond)
            i2, s2, _ = thin(idx, (-np.sign(dev[idx])).astype(np.int64), 15)
            out[("k", k, "vol", vol)] = (i2, s2, None)
    return out


# ------------------------------------------------------------------ 8. SWEEP
@nb.njit(cache=True)
def _sweep(h, l, c, qv, atr, N, vol_filter):
    ii = []; ss = []
    PH = np.full(len(h), np.nan); PL = np.full(len(h), np.nan)
    for i in range(N, len(h)):
        mx = -1e300; mn = 1e300; ok = True
        for k in range(i - N, i):
            if np.isnan(h[k]) or np.isnan(l[k]): ok = False; break
            if h[k] > mx: mx = h[k]
            if l[k] < mn: mn = l[k]
        if ok: PH[i] = mx; PL[i] = mn
    last = -10 ** 9
    for b in range(N, len(h) - 3):
        a = atr[b]
        if np.isnan(a) or np.isnan(PH[b]) or np.isnan(h[b]): continue
        if vol_filter:
            s = 0.0; cnt = 0
            for k in range(b - 30, b):
                if not np.isnan(qv[k]): s += qv[k]; cnt += 1
            if cnt < 20 or np.isnan(qv[b]) or qv[b] < 2.0 * s / cnt: continue
        if h[b] > PH[b] + 0.2 * a:
            for i in range(b, b + 3):
                if not np.isnan(c[i]) and c[i] < PH[b]:
                    if i - last >= 15: ii.append(i); ss.append(-1); last = i
                    break
        elif l[b] < PL[b] - 0.2 * a:
            for i in range(b, b + 3):
                if not np.isnan(c[i]) and c[i] > PL[b]:
                    if i - last >= 15: ii.append(i); ss.append(1); last = i
                    break
    return np.array(ii, dtype=np.int64), np.array(ss, dtype=np.int64)


def fam_sweep(pr):
    co = pr.coin; out = {}
    for N in (30, 60, 120):
        for vf in (0, 1):
            i, s = _sweep(co.h, co.l, co.c, co.qv, pr.atr1, N, vf)
            out[("N", N, "vol", vf)] = (i, s, None)
    return out


# ------------------------------------------------------------------ 9. SQUEEZE (5m)
def fam_squeeze(pr):
    b = pr.b5; out = {}
    c = b["c"]; q = b["qv"]
    mu, sd = C.rolling_mean_std(c, 20)
    up = mu + 2 * sd; dn = mu - 2 * sd; bw = 4 * sd / mu
    thr = daily_quantile(bw, 288, 864, 0.20)
    sq_prev = shift(bw, 1) <= thr
    qmu, _ = C.rolling_mean_std(q, 20)
    volok = q >= 1.5 * shift(qmu, 1)
    long_ = sq_prev & (c > up); short_ = sq_prev & (c < dn)
    for vol in (0, 1):
        L = long_ & volok if vol else long_
        S = short_ & volok if vol else short_
        j = np.nonzero(L | S)[0]
        s_ = np.where(L[j], 1, -1).astype(np.int64)
        i2, s2, _ = thin(j * 5 + 4, s_, 30)
        out[("vol", vol)] = (i2, s2, None)
    return out


# ------------------------------------------------------------------ 10. PULLBACK (5m)
@nb.njit(cache=True)
def adx(h, l, c, n):
    out = np.full(len(c), np.nan)
    tr_s = 0.0; p_s = 0.0; m_s = 0.0; dx_s = np.nan; cnt = 0; dxn = 0
    for i in range(1, len(c)):
        if np.isnan(h[i]) or np.isnan(l[i]) or np.isnan(c[i - 1]) or np.isnan(h[i - 1]) or np.isnan(l[i - 1]):
            continue
        up = h[i] - h[i - 1]; dn = l[i - 1] - l[i]
        pdm = up if (up > dn and up > 0) else 0.0; mdm = dn if (dn > up and dn > 0) else 0.0
        tr = max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
        if cnt < n:
            tr_s += tr; p_s += pdm; m_s += mdm; cnt += 1
            if cnt < n: continue
        else:
            tr_s = tr_s - tr_s / n + tr; p_s = p_s - p_s / n + pdm; m_s = m_s - m_s / n + mdm
        pdi = 100 * p_s / tr_s if tr_s > 0 else 0.0; mdi = 100 * m_s / tr_s if tr_s > 0 else 0.0
        dx = 100 * abs(pdi - mdi) / (pdi + mdi) if (pdi + mdi) > 0 else 0.0
        if np.isnan(dx_s): dx_s = dx; dxn = 1
        elif dxn < n: dx_s = (dx_s * dxn + dx) / (dxn + 1); dxn += 1
        else: dx_s = (dx_s * (n - 1) + dx) / n
        if dxn >= n: out[i] = dx_s
    return out


def fam_pullback(pr):
    b = pr.b5; out = {}
    c, h, l = b["c"], b["h"], b["l"]
    e9 = C.ema(c, 9); e21 = C.ema(c, 21); e55 = C.ema(c, 55)
    ax = adx(h, l, c, 14)
    up = (e9 > e21) & (e21 > e55); dn = (e9 < e21) & (e21 < e55)
    touch_up = np.zeros(len(c), bool); touch_dn = np.zeros(len(c), bool)
    for k in range(1, 7):                                                   # a touch of EMA 21 within the previous 6 bars
        touch_up |= (shift((l <= e21).astype(float), k) == 1)
        touch_dn |= (shift((h >= e21).astype(float), k) == 1)
    lc = (c > e9) & (shift(c, 1) <= shift(e9, 1)) & up & touch_up
    sc = (c < e9) & (shift(c, 1) >= shift(e9, 1)) & dn & touch_dn
    for adx_f in (0, 1):
        okx = (ax >= 20) if adx_f else np.ones(len(c), bool)
        j = np.nonzero((lc | sc) & okx)[0]
        s_ = np.where(lc[j], 1, -1).astype(np.int64)
        i2, s2, _ = thin(j * 5 + 4, s_, 30)
        out[("adx", adx_f)] = (i2, s2, None)
    return out


# ------------------------------------------------------------------ 11. RSI2_FADE (1m)
@nb.njit(cache=True)
def rsi_wilder(c, n):
    out = np.full(len(c), np.nan); ag = np.nan; al = np.nan; cnt = 0
    for i in range(1, len(c)):
        if np.isnan(c[i]) or np.isnan(c[i - 1]): continue
        d = c[i] - c[i - 1]; g = d if d > 0 else 0.0; ls = -d if d < 0 else 0.0
        if cnt < n:
            ag = g if np.isnan(ag) else ag + g; al = ls if np.isnan(al) else al + ls; cnt += 1
            if cnt == n: ag /= n; al /= n
            else: continue
        else:
            ag = (ag * (n - 1) + g) / n; al = (al * (n - 1) + ls) / n
        out[i] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    return out


def fam_rsi2(pr):
    co = pr.coin; out = {}
    r = rsi_wilder(co.c, 2)
    mu, sd = C.rolling_mean_std(co.c, 20)
    below = co.c < mu - 2.5 * sd; above = co.c > mu + 2.5 * sd
    q30, _ = C.rolling_mean_std(co.qv, 30)
    volx = co.qv >= 2.0 * shift(q30, 1)
    for var, (lo, hi, vf) in {1: (5, 95, False), 2: (2, 98, True)}.items():
        L = (r <= lo) & below; S = (r >= hi) & above
        if vf: L &= volx; S &= volx
        idx = np.nonzero(L | S)[0]
        i2, s2, _ = thin(idx, np.where(L[idx], 1, -1).astype(np.int64), 15)
        out[("var", var)] = (i2, s2, None)
    return out


# ------------------------------------------------------------------ 12. XSEC (needs all design coins)
def fam_xsec(preps):
    """every 15 minutes: rank the coins by the past m-minute return in ATR units; returns {variant: {coin: (idx, side)}}"""
    names = list(preps)
    N = preps[names[0]].coin.N
    out = {}
    dec = np.arange(14, N, 15)
    for m in (15, 30, 60):
        R = np.full((len(names), len(dec)), np.nan)
        for a, nm in enumerate(names):
            co = preps[nm].coin
            ret = np.full(N, np.nan); ret[m:] = co.c[m:] / co.c[:-m] - 1
            R[a] = (ret / (preps[nm].atr1 / co.c))[dec]
        valid = ~np.isnan(R)
        rank = np.where(valid, R, np.nan)
        order = np.argsort(np.where(valid, R, np.inf), axis=0)            # ascending, NaN last
        nv = valid.sum(axis=0)
        for mode, sgn in (("rev", 1), ("mom", -1)):
            res = {nm: ([], []) for nm in names}
            for col in np.nonzero(nv >= 8)[0]:
                bottom = order[:2, col]; top = order[nv[col] - 2:nv[col], col]
                for a in bottom: res[names[a]][0].append(dec[col]); res[names[a]][1].append(sgn)
                for a in top: res[names[a]][0].append(dec[col]); res[names[a]][1].append(-sgn)
            out[("m", m, mode)] = {nm: (np.array(v[0], dtype=np.int64), np.array(v[1], dtype=np.int64), None) for nm, v in res.items()}
    return out
