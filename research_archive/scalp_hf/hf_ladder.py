"""FREQ_LADDER (PREREG_scalp_hf.md, family 5): the production Kalman trend rules (backend_lib/kalman_trend.py) on bars of
240, 120, 60, 30, 15 and 5 minutes. Only the bar size changes; every other rule is the production one: z entry +-1 from a Kalman local linear trend
(lam 1e-4, span 100), BTC daily-trend filter, funding filter (72h average / 9), stop 3 ATR14 with a 0.5% minimum, exit at the next open after z
crosses 0, 120 bars at most, 14 bps round trip plus real funding, one trade per coin, 0.5% risk, at most 8 open.
usage: python3 hf_ladder.py [SEGS=DEV,VAL,FINAL,OLD] [design|unseen]"""
import sys, math
import numpy as np
import numba as nb
import hf_core as C

LAM, SPAN, Z_IN, STOP_ATR, MINSTOP, HOLD, WARMUP, FEE = 1e-4, 100, 1.0, 3.0, 0.005, 120, 201, 0.0014
FUND_CAP, FUND_WINDOW = 0.0003, 72 * 3600_000


@nb.njit(cache=True)
def kalman_z(c):
    n = len(c); z = np.full(n, np.nan)
    y = np.log(np.abs(c))
    x0 = y[0]; x1 = 0.0; p00 = 1.0; p01 = 0.0; p11 = 1.0
    var = -1.0; a = 2.0 / (SPAN + 1)
    for i in range(1, n):
        r2 = (y[i] - y[i - 1]) ** 2
        var = r2 if var < 0 else var + a * (r2 - var)
        R = max(var, 1e-10)
        x0 = x0 + x1
        p00, p01, p11 = p00 + 2 * p01 + p11, p01 + p11, p11 + LAM * R
        S = p00 + R; k0 = p00 / S; k1 = p01 / S
        e = y[i] - x0
        x0 += k0 * e; x1 += k1 * e
        p00, p01, p11 = p00 - k0 * p00, p01 - k0 * p01, p11 - k1 * p01
        if i > SPAN: z[i] = x1 / math.sqrt(max(p11, 1e-18))
    return z


@nb.njit(cache=True)
def atr14_rma(h, l, c):
    n = len(c); out = np.zeros(n); tr0 = h[0] - l[0]; out[0] = tr0
    for i in range(1, n):
        tr = max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
        out[i] = out[i - 1] + (tr - out[i - 1]) / 14.0
    return out


def ffill(x):
    x = x.copy(); m = np.isnan(x)
    idx = np.where(~m, np.arange(len(x)), 0); np.maximum.accumulate(idx, out=idx)
    return x[idx], m


def btc_daily_trend(btc):
    """+1/-1/0 per UTC day (index = day number since the grid start) from the daily closes: close vs EMA50 and EMA20 vs EMA50, 0 for the first 50 days"""
    nd = btc.N // 1440
    cd = np.full(nd, np.nan)
    c, _ = ffill(btc.c)
    for d in range(nd): cd[d] = c[d * 1440 + 1439]
    e20 = C.ema(cd, 20); e50 = C.ema(cd, 50)
    tr = np.where((cd > e50) & (e20 > e50), 1, np.where((cd < e50) & (e20 < e50), -1, 0)).astype(np.int64)
    tr[:50] = 0
    return tr


@nb.njit(cache=True)
def run_coin(t, o, h, l, c, z, a, valid, trend_bar, ft, fr, B):
    """returns arrays of trades: entry_ms, exit_ms, ret_net, stopfrac, reason (1 stop, 0 time, 3 flip)"""
    n = len(c); cap = 20000
    ent = np.zeros(cap, np.int64); ext = np.zeros(cap, np.int64); ret = np.zeros(cap); sf = np.zeros(cap); rs = np.zeros(cap, np.int64)
    k = 0; busy = -1
    for i in range(max(WARMUP, 1), n - 1):
        if not valid[i] or np.isnan(z[i]) or np.isnan(z[i - 1]): continue
        sgn = 0
        if z[i] > Z_IN and z[i - 1] <= Z_IN: sgn = 1
        elif z[i] < -Z_IN and z[i - 1] >= -Z_IN: sgn = -1
        if sgn == 0: continue
        te = t[i + 1]
        if te < busy: continue
        tr = trend_bar[i]
        tc = t[i] + B * 60000                                              # close time of the signal bar
        lo = np.searchsorted(ft, tc - FUND_WINDOW, side="right"); hi = np.searchsorted(ft, tc, side="right")
        have = len(ft) > 0 and ft[0] <= tc - FUND_WINDOW + 8 * 3600_000 and hi - lo >= 3
        favg = 0.0
        if have:
            for q in range(lo, hi): favg += fr[q]
            favg /= 9.0
        if sgn == 1:
            if tr != 1: continue
            if have and favg > FUND_CAP: continue
        else:
            if tr != -1: continue
            if not have or not favg > 0: continue
        if not valid[i + 1]: continue
        ep = o[i + 1]; stop = c[i] - sgn * STOP_ATR * a[i]
        if sgn * (ep - stop) < MINSTOP * ep: stop = ep - sgn * MINSTOP * ep
        risk = sgn * (ep - stop)
        xi = -1; xt = 0; xp = 0.0; reason = -1
        for j in range(i + 1, min(n, i + 1 + HOLD)):
            if (sgn == 1 and l[j] <= stop) or (sgn == -1 and h[j] >= stop):
                xi, xt, xp, reason = j, t[j] + B * 60000, stop, 1; break
            if (sgn == 1 and z[j] < 0) or (sgn == -1 and z[j] > 0):
                if j + 1 < n: xi, xt, xp, reason = j + 1, t[j + 1] + B * 60000, o[j + 1], 3
                break
        else:
            j = i + HOLD
            if j <= n - 1: xi, xt, xp, reason = j, t[j] + B * 60000, c[j], 0
        if reason < 0: continue
        gross = sgn * (xp / ep - 1.0)
        f = 0.0
        a0 = np.searchsorted(ft, t[i + 1], side="right"); b0 = np.searchsorted(ft, xt, side="right")
        for q in range(a0, b0): f += fr[q]
        f *= sgn
        if k < cap:
            ent[k] = t[i + 1]; ext[k] = xt; ret[k] = gross - FEE - f; sf[k] = risk / ep; rs[k] = reason; k += 1
        busy = xt
    return ent[:k], ext[:k], ret[:k], sf[:k], rs[:k]


def coin_rung(co, ft, fr, trend_days, B):
    O, H, L, Cc, *_ = C.aggregate(co.o, co.h, co.l, co.c, co.qv, co.n, co.tb, B)
    valid = ~(np.isnan(O) | np.isnan(H) | np.isnan(L) | np.isnan(Cc))
    first = np.argmax(valid)
    O, _ = ffill(O); H, _ = ffill(H); L, _ = ffill(L); Cc, _ = ffill(Cc)
    O[:first] = Cc[:first] = H[:first] = L[:first] = Cc[first]; valid[:first] = False
    t = co.t0 + np.arange(len(O), dtype=np.int64) * B * 60000
    z = kalman_z(Cc); a = atr14_rma(H, L, Cc)
    day = np.clip(((t + B * 60000) // C.DAY) - 1 - co.t0 // C.DAY, 0, len(trend_days) - 1)
    trend_bar = trend_days[day]
    return run_coin(t, O, H, L, Cc, z, a, valid, trend_bar, ft, fr, B)


if __name__ == "__main__":
    segs = (sys.argv[1] if len(sys.argv) > 1 else "DEV,VAL,FINAL,OLD").split(","); which = sys.argv[2] if len(sys.argv) > 2 else "design"
    names = C.DESIGN if which == "design" else C.UNSEEN
    btc = C.load_coin("BTC"); trend_days = btc_daily_trend(btc)
    res = {B: [] for B in (240, 120, 60, 30, 15, 5)}
    for cid, nm in enumerate(names):
        co = btc if nm == "BTC" else C.load_coin(nm); ft, fr = C.load_funding(nm)
        for B in res:
            e, x, r, s, rs = coin_rung(co, ft, fr, trend_days, B)
            res[B].append((e, x, r, s, np.full(len(e), cid), rs))
        print(nm, flush=True)
    for seg in segs:
        print(f"\n# FREQ_LADDER, segment {seg}, {which} coins: production Kalman rules per bar size ($10, 0.5% risk, max 8 open, 14 bps + funding)")
        print(f"{'bar':>5s} {'trades':>7s} {'/month':>7s} {'win%':>5s} {'avg net R':>9s} {'bps/trade':>9s} {'t':>6s} {'$10 ->':>9s} {'taken':>6s} {'maxDD':>6s}")
        for B, parts in res.items():
            e = np.concatenate([p[0] for p in parts]); x = np.concatenate([p[1] for p in parts]); r = np.concatenate([p[2] for p in parts])
            s = np.concatenate([p[3] for p in parts]); cid = np.concatenate([p[4] for p in parts])
            m = C.seg_mask(e, seg); e, x, r, s, cid = e[m], x[m], r[m], s[m], cid[m]
            if len(e) < 2: print(f"{B:5d} {len(e):7d}"); continue
            st = C.cluster_stats(r * 1e4, e // C.DAY)
            eq, taken, mdd, wins, _ = C.portfolio(e, x, r, s, cid, 10.0, 0.005, 8, 3.0, 6.0)
            months = (C.SEG[seg][1] - C.SEG[seg][0]) / (30.44 * C.DAY)
            print(f"{B:5d} {len(e):7d} {len(e) / months:7.0f} {np.mean(r > 0) * 100:5.1f} {np.mean(r / s):+9.3f} {st['mean']:9.1f} {st['t']:6.1f} {eq:9.2f} {taken:6d} {mdd * 100:5.0f}%")
