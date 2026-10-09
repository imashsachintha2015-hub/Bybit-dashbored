"""Core of the scalp / high-frequency study (see PREREG_scalp_hf.md): data loading on a continuous 1m grid, indicators,
event statistics with day-clustered errors, the trade simulator (taker and maker execution) and the $10 portfolio."""
import os, json, glob, datetime
import numpy as np
import numba as nb

MIN = 60_000
DAY = 86_400_000
DATA = os.environ.get("HF_DATA", "/tmp/claude-0/-home-user-Bybit-dashbored/75f1764d-aab5-5961-97c3-7261f6eaa24e/scratchpad/hf")
FUND_DIR = os.environ.get("HF_FUND", "/tmp/claude-0/-home-user-Bybit-dashbored/75f1764d-aab5-5961-97c3-7261f6eaa24e/scratchpad/funding")
DESIGN = "BTC ETH SOL XRP DOGE BNB ADA LINK AVAX LTC".split()
UNSEEN = "DOT NEAR ATOM APT ARB OP SUI INJ TIA WLD".split()

# costs per side, as fractions (PREREG: taker 5.5 bps fee + 1.5 bps slippage, maker 2 bps)
TAKER = 0.0007
MAKER = 0.0002
HORIZONS = (1, 3, 5, 10, 20, 30, 60, 120)


def ms(y, m=1, d=1):
    return int(datetime.datetime(y, m, d, tzinfo=datetime.timezone.utc).timestamp() * 1000)


SEG = {"OLD": (ms(2021), ms(2023)), "DEV": (ms(2023), ms(2024, 7)), "VAL": (ms(2024, 7), ms(2025, 7)), "FINAL": (ms(2025, 7), ms(2026, 10))}


def seg_mask(t_ms, name):
    a, b = SEG[name]
    return (t_ms >= a) & (t_ms < b)


# ------------------------------------------------------------------ data
class Coin:
    """1-minute arrays on a continuous grid starting at a UTC midnight; missing minutes are NaN."""
    def __init__(self, name, t0, o, h, l, c, qv, n, tb):
        self.name, self.t0 = name, t0
        self.o, self.h, self.l, self.c, self.qv, self.n, self.tb = o, h, l, c, qv, n, tb
        self.N = len(o)

    def t(self, i):
        return self.t0 + np.asarray(i, dtype=np.int64) * MIN

    @property
    def times(self):
        return self.t0 + np.arange(self.N, dtype=np.int64) * MIN


def load_coin(name, first=(2021, 1), last=(2026, 9)):
    files = sorted(glob.glob(os.path.join(DATA, "k", name, "*.npz")))
    keep = []
    for f in files:
        y, m = map(int, os.path.basename(f)[:7].split("-"))
        if first <= (y, m) <= last: keep.append(f)
    if not keep: raise FileNotFoundError(name)
    ts, Xs = [], []
    for f in keep:
        d = np.load(f); ts.append(d["t"]); Xs.append(d["X"])
    t = np.concatenate(ts); X = np.concatenate(Xs).astype(np.float64)
    t0 = t[0] // DAY * DAY; t1 = t[-1] // MIN * MIN
    N = int((t1 - t0) // MIN) + 1
    idx = ((t - t0) // MIN).astype(np.int64)
    cols = []
    for j in range(7):
        a = np.full(N, np.nan); a[idx] = X[:, j]; cols.append(a)
    o, h, l, c, qv, n, tb = cols
    return Coin(name, t0, o, h, l, c, qv, n, tb)


def load_funding(name):
    """sorted arrays (ms, rate) of funding settlements"""
    d = json.load(open(os.path.join(FUND_DIR, name + ".json")))
    a = np.array(d, dtype=np.float64)
    a = a[np.argsort(a[:, 0])]
    return a[:, 0].astype(np.int64), a[:, 1]


# ------------------------------------------------------------------ bars and indicators
@nb.njit(cache=True)
def aggregate(o, h, l, c, qv, n, tb, k):
    """k-minute bars aligned to the grid start (a UTC midnight); a block with any missing minute is NaN"""
    m = len(o) // k
    O = np.full(m, np.nan); H = np.full(m, np.nan); L = np.full(m, np.nan); C = np.full(m, np.nan)
    Q = np.full(m, np.nan); NN = np.full(m, np.nan); TB = np.full(m, np.nan)
    for j in range(m):
        a = j * k; ok = True
        hi = -1e300; lo = 1e300; q = 0.0; nt = 0.0; tbb = 0.0
        for x in range(a, a + k):
            if np.isnan(o[x]) or np.isnan(c[x]) or np.isnan(h[x]) or np.isnan(l[x]): ok = False; break
            if h[x] > hi: hi = h[x]
            if l[x] < lo: lo = l[x]
            q += qv[x]; nt += n[x]; tbb += tb[x]
        if ok:
            O[j] = o[a]; C[j] = c[a + k - 1]; H[j] = hi; L[j] = lo; Q[j] = q; NN[j] = nt; TB[j] = tbb
    return O, H, L, C, Q, NN, TB


@nb.njit(cache=True)
def ema(x, n):
    out = np.full(len(x), np.nan); a = 2.0 / (n + 1.0); v = np.nan
    for i in range(len(x)):
        if np.isnan(x[i]): out[i] = v; continue
        v = x[i] if np.isnan(v) else a * x[i] + (1 - a) * v
        out[i] = v
    return out


@nb.njit(cache=True)
def atr_wilder(h, l, c, n):
    out = np.full(len(c), np.nan); v = np.nan; cnt = 0
    for i in range(1, len(c)):
        if np.isnan(h[i]) or np.isnan(l[i]) or np.isnan(c[i - 1]):
            out[i] = v if cnt >= n else np.nan; continue
        tr = max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
        v = tr if np.isnan(v) else (v * (n - 1) + tr) / n
        cnt += 1
        out[i] = v if cnt >= n else np.nan                                    # no value before n bars of history
    return out


@nb.njit(cache=True)
def rolling_mean_std(x, w):
    """causal trailing mean and std over w values (NaN-aware, needs >= w/2 valid)"""
    n = len(x); mu = np.full(n, np.nan); sd = np.full(n, np.nan)
    s = 0.0; s2 = 0.0; cnt = 0
    for i in range(n):
        if not np.isnan(x[i]): s += x[i]; s2 += x[i] * x[i]; cnt += 1
        j = i - w
        if j >= 0 and not np.isnan(x[j]): s -= x[j]; s2 -= x[j] * x[j]; cnt -= 1
        if cnt >= w // 2 and cnt > 1:
            m = s / cnt; v = s2 / cnt - m * m
            mu[i] = m; sd[i] = np.sqrt(v) if v > 0 else 0.0
    return mu, sd


@nb.njit(cache=True)
def to_1m(arr_k, k, N):
    """value of the last COMPLETED k-minute bar, at every 1m index (causal: bar j is known at the last minute of its block)"""
    out = np.full(N, np.nan)
    for i in range(N):
        j = (i + 1) // k - 1
        if j >= 0 and j < len(arr_k): out[i] = arr_k[j]
    return out


# ------------------------------------------------------------------ statistics
def cluster_stats(vals, clusters):
    """mean, day-clustered standard error, t, n of vals"""
    vals = np.asarray(vals, dtype=np.float64); ok = ~np.isnan(vals)
    vals = vals[ok]; cl = np.asarray(clusters)[ok]
    n = len(vals)
    if n < 2: return dict(n=n, mean=float("nan"), se=float("nan"), t=float("nan"))
    mean = vals.mean()
    _, inv = np.unique(cl, return_inverse=True)
    sums = np.bincount(inv, weights=vals - mean)
    se = np.sqrt((sums ** 2).sum()) / n
    return dict(n=n, mean=float(mean), se=float(se), t=float(mean / se) if se > 0 else float("nan"))


# ------------------------------------------------------------------ events: gross edge at fixed horizons
@nb.njit(cache=True)
def fixed_horizon(o, c, idx, side, horizons):
    """gross bps of each event at each horizon: side * (close after h minutes / next open - 1); NaN if a price is missing"""
    out = np.full((len(idx), len(horizons)), np.nan)
    N = len(o)
    for e in range(len(idx)):
        i = idx[e]
        if i + 1 >= N or np.isnan(o[i + 1]): continue
        for k in range(len(horizons)):
            x = i + horizons[k]
            if x < N and not np.isnan(c[x]):
                out[e, k] = side[e] * (c[x] / o[i + 1] - 1.0) * 1e4
    return out


@nb.njit(cache=True)
def cooldown(idx, minutes):
    """keep an event only if the previous kept event is at least `minutes` minutes earlier (idx sorted)"""
    keep = np.zeros(len(idx), dtype=np.bool_); last = -10 ** 12
    for e in range(len(idx)):
        if idx[e] - last >= minutes: keep[e] = True; last = idx[e]
    return keep


# ------------------------------------------------------------------ trade simulator
@nb.njit(cache=True)
def sim_trade(o, h, l, c, atr, i, side, stopfrac, tp_mult, tmax, maker, wait, pen_atr):
    """One trade from a signal at the close of 1m bar i.
    taker: enter at the next open. maker: limit at the signal close, fills within `wait` bars only if price trades through it
    by max(pen_atr*ATR, 1bp) (fill bar can only stop out); a limit take-profit needs a 0.02 ATR trade-through.
    Exit: stop (stop-market), take-profit at tp_mult * stop distance (0 = none), or the close of the tmax-th bar after the fill.
    returns (entry_idx, exit_idx, entry_px, exit_px, reason, exit_maker): reason 0 time, 1 stop, 2 target, -1 no fill / no data"""
    N = len(o)
    e = i + 1
    if e >= N or np.isnan(o[e]) or np.isnan(c[i]):
        return -1, -1, 0.0, 0.0, -1, False
    px_e = o[e]
    if maker:
        L = c[i]; a = atr[i]
        pen = max(pen_atr * a if not np.isnan(a) else 0.0, 1e-4 * L)
        filled = -1
        for b in range(e, min(e + wait, N)):
            if np.isnan(l[b]): continue
            if side == 1 and l[b] <= L - pen: filled = b; break
            if side == -1 and h[b] >= L + pen: filled = b; break
        if filled < 0: return -1, -1, 0.0, 0.0, -1, False
        e = filled; px_e = L
    stop = px_e * (1.0 - side * stopfrac)
    tp = px_e * (1.0 + side * stopfrac * tp_mult) if tp_mult > 0 else 0.0
    a = atr[i]
    tp_pen = 0.02 * a if (maker and not np.isnan(a)) else 0.0
    last = min(e + tmax - 1, N - 1)
    for b in range(e, last + 1):
        if np.isnan(h[b]) or np.isnan(l[b]): continue
        if b > e or not maker:
            gap = (side == 1 and o[b] <= stop) or (side == -1 and o[b] >= stop)
            if gap and b > e: return e, b, px_e, o[b], 1, False
        hit_stop = (side == 1 and l[b] <= stop) or (side == -1 and h[b] >= stop)
        if hit_stop: return e, b, px_e, stop, 1, False
        if b > e or not maker:
            if tp_mult > 0:
                hit_tp = (side == 1 and h[b] >= tp + tp_pen) or (side == -1 and l[b] <= tp - tp_pen)
                if hit_tp: return e, b, px_e, tp, 2, maker
    # time exit at the close of the last bar with a price
    for b in range(last, e - 1, -1):
        if not np.isnan(c[b]): return e, b, px_e, c[b], 0, False
    return -1, -1, 0.0, 0.0, -1, False


@nb.njit(cache=True)
def funding_paid(ft, fr, t_entry, t_exit, side):
    """sum of funding rates paid by this side for settlements in (t_entry, t_exit]"""
    s = 0.0
    lo = np.searchsorted(ft, t_entry, side="right"); hi = np.searchsorted(ft, t_exit, side="right")
    for k in range(lo, hi): s += fr[k]
    return side * s


@nb.njit(cache=True)
def simulate_events(o, h, l, c, atr, idx, side, stopmult, tp_mult, tmax, maker, wait, pen_atr, min_stop, t0, ft, fr, extra_slip):
    """run sim_trade for every event; returns arrays: entry_ms, exit_ms, ret_net (fraction of notional), stopfrac, reason
    cost per side: taker TAKER+extra_slip, maker MAKER (limit entry / take-profit), stops and time exits taker."""
    n = len(idx)
    ent = np.zeros(n, np.int64); ext = np.zeros(n, np.int64); ret = np.zeros(n); sf = np.zeros(n); rs = np.full(n, -1, np.int64)
    busy_until = -1
    for k in range(n):
        i = idx[k]
        if i + 1 <= busy_until: continue                                     # one position per coin at a time
        a = atr[i]
        if np.isnan(a) or np.isnan(c[i]) or c[i] <= 0: continue
        s = max(stopmult * a / c[i], min_stop)
        e, x, pe, px, reason, xmaker = sim_trade(o, h, l, c, atr, i, side[k], s, tp_mult, tmax, maker, wait, pen_atr)
        if reason < 0: continue
        gross = side[k] * (px / pe - 1.0)
        c_in = MAKER if maker else TAKER + extra_slip
        c_out = MAKER if xmaker else TAKER + extra_slip
        te = t0 + e * MIN; tx = t0 + (x + 1) * MIN
        ret[k] = gross - c_in - c_out - funding_paid(ft, fr, te, tx, side[k])
        ent[k] = te; ext[k] = tx; sf[k] = s; rs[k] = reason
        busy_until = x
    return ent, ext, ret, sf, rs


# ------------------------------------------------------------------ $10 portfolio
@nb.njit(cache=True)
def portfolio(ent, ext, ret, sf, coin, start, risk, max_open, max_pos_x, max_gross_x):
    """chronological compounding. Position size = risk*equity / stop distance, capped at max_pos_x * equity per position and
    max_gross_x * equity in total (the strategy runner's limits). P&L is booked at exit. returns (final equity, trades, max drawdown,
    wins, equity at each taken trade exit)"""
    n = len(ent)
    order = np.argsort(ent)
    eq = start; peak = start; mdd = 0.0
    open_exit = np.zeros(max_open, np.int64); open_coin = np.full(max_open, -1, np.int64)
    open_pnl = np.zeros(max_open); open_not = np.zeros(max_open)
    taken = 0; wins = 0
    curve = np.zeros(n)
    for q in range(n):
        k = order[q]
        if ent[k] == 0: continue
        for s in range(max_open):                                            # book exits up to this entry
            if open_coin[s] >= 0 and open_exit[s] <= ent[k]:
                eq += open_pnl[s]; open_coin[s] = -1; open_not[s] = 0.0
                if eq > peak: peak = eq
                dd = (peak - eq) / peak
                if dd > mdd: mdd = dd
                if eq <= 0.0: return 0.0, taken, 1.0, wins, curve[:taken]
        slot = -1; busy = False; gross = 0.0
        for s in range(max_open):
            if open_coin[s] < 0:
                if slot < 0: slot = s
            else:
                gross += open_not[s]
                if open_coin[s] == coin[k]: busy = True
        if slot < 0 or busy: continue
        notional = min(risk * eq / sf[k], max_pos_x * eq)
        room = max_gross_x * eq - gross
        if room <= 0: continue
        notional = min(notional, room)
        open_coin[slot] = coin[k]; open_exit[slot] = ext[k]; open_pnl[slot] = notional * ret[k]; open_not[slot] = notional
        taken += 1
        if ret[k] > 0: wins += 1
        curve[taken - 1] = eq
    for s in range(max_open):
        if open_coin[s] >= 0:
            eq += open_pnl[s]
            if eq > peak: peak = eq
            dd = (peak - eq) / peak
            if dd > mdd: mdd = dd
    return eq, taken, mdd, wins, curve[:taken]
