"""Funding-aware Kalman trend (4H) -- the signal and trade logic of research_archive/longshort, in pure Python.

Every function is a direct port of the research code so live signals match the backtest:
  kalman()        tl_core.kalman_trend (local linear trend on log price, z = slope / its standard error)
  atr14()         tl_core.atr14 (Wilder) ; daily_trend() tl_core.daily_trend (BTC's last complete UTC day)
  coin_trades()   ls_improve.series + ls_improve.sim + ls_improve.trades for one coin (KAL1 entries, BTC filter,
                  funding rules, one open trade per coin, 3-ATR stop, exit at the next open after z crosses 0,
                  120 bars at most)
The rules: long when z crosses above +1 while BTC's daily trend is up, unless the last 3 days of funding average more
than 0.03% per 8h; short when z crosses below -1 while BTC's daily trend is down and that funding average is above 0.
Parity with the research signals: research_archive/live_module/parity_out.txt.
"""
import bisect
import math

H4 = 4 * 3600 * 1000
DAY = 86400 * 1000
LAM = 1e-4                 # slope noise, as a multiple of the observation noise
SPAN = 100                 # EWMA span of the observation noise; z is defined from bar SPAN + 1
Z_IN = 1.0                 # entry threshold
STOP_ATR = 3.0
MINSTOP = 0.005            # stops closer than 0.5% of the entry are widened to 0.5%
HOLD = 120                 # bars (20 days)
WARMUP = 201               # first bar index that may signal (as in the research)
FUND_CAP_LONG = 0.0003     # skip longs when funding averages more than 0.03% per 8h over the last 3 days
FUND_WINDOW = 72 * 3600 * 1000
FEE = 0.0014               # 14 bps per round trip, as in the research


# ------------------------------------------------------------------ dashboard settings (shared by the engine and the server)
SETTINGS_KEY = "kalman_trend_settings"
DEFAULT_SETTINGS = {
    "riskPct": 0.5,            # risk per trade, % of equity (the tested value)
    "maxOpen": 8,              # open positions at most (the tested value)
    "live": False,             # live orders; also needs KALMAN_LIVE=1 on the server
    "oiFilter": False,         # longs only when open interest is above its 30-day mean (research_archive/openinterest)
    "corrCap": False,          # skip when 3+ open same-direction trades correlate > 0.7 (research_archive/situations)
    "maxRiskOnMinPct": 2.0,    # when the exchange minimum forces a bigger position, allow at most this risk, else skip
    "leverage": 10,            # margin only: the risk is set by the stop and the size
    "paperStartEquity": 10.0,
    "paperResetToken": 0,      # change it (while flat) to restart the paper account
}
LIMITS = {"riskPct": (0.05, 2.0), "maxOpen": (1, 12), "maxRiskOnMinPct": (0.1, 5.0), "leverage": (1, 20),
          "paperStartEquity": (1.0, 1e7), "paperResetToken": (0, 1e15)}


def clean_settings(raw):
    """defaults + validated, clamped user values; unknown keys dropped"""
    s = dict(DEFAULT_SETTINGS)
    for k, v in (raw or {}).items():
        if k not in DEFAULT_SETTINGS: continue
        d = DEFAULT_SETTINGS[k]
        try:
            if isinstance(d, bool): s[k] = v if isinstance(v, bool) else str(v).strip().lower() in ("1", "true", "yes", "on")
            elif isinstance(d, int): s[k] = int(float(v))
            else: s[k] = float(v)
        except Exception: continue
        if k in LIMITS and not isinstance(d, bool):
            lo, hi = LIMITS[k]; s[k] = type(d)(min(hi, max(lo, s[k])))
    return s


# ------------------------------------------------------------------ indicators
def kalman(closes, lam=LAM, span=SPAN):
    """(z, level): z[i] = slope / se for i > span (NaN before); level[i] = filtered price (exp of the log level)."""
    n = len(closes); z = [math.nan] * n; level = [math.nan] * n
    if n == 0: return z, level
    y = [math.log(abs(c)) * (1.0 if c > 0 else -1.0) for c in closes]
    x0, x1 = y[0], 0.0
    p00, p01, p11 = 1.0, 0.0, 1.0
    var = None; a = 2 / (span + 1)
    level[0] = closes[0]
    for i in range(1, n):
        r2 = (y[i] - y[i - 1]) ** 2
        var = r2 if var is None else var + a * (r2 - var)
        R = max(var, 1e-10)
        x0, x1 = x0 + x1, x1                                            # predict
        p00, p01, p11 = p00 + 2 * p01 + p11, p01 + p11, p11 + lam * R
        S = p00 + R; k0 = p00 / S; k1 = p01 / S                         # update
        e = y[i] - x0
        x0, x1 = x0 + k0 * e, x1 + k1 * e
        p00, p01, p11 = p00 - k0 * p00, p01 - k0 * p01, p11 - k1 * p01
        if i > span: z[i] = x1 / math.sqrt(max(p11, 1e-18))
        level[i] = math.exp(x0) if closes[i] > 0 else -math.exp(-x0)
    return z, level


def rma(xs, n):
    out = [0.0] * len(xs)
    if not xs: return out
    out[0] = xs[0]
    for i in range(1, len(xs)): out[i] = out[i - 1] + (xs[i] - out[i - 1]) / n
    return out


def ema(xs, n):
    out = [0.0] * len(xs)
    if not xs: return out
    a = 2 / (n + 1); out[0] = xs[0]
    for i in range(1, len(xs)): out[i] = out[i - 1] + a * (xs[i] - out[i - 1])
    return out


def atr14(h, l, c):
    tr = []
    for i in range(len(c)):
        pc = c[i - 1] if i else c[0]
        tr.append(max(h[i] - l[i], max(abs(h[i] - pc), abs(l[i] - pc))))
    return rma(tr, 14)


def daily_trend(t, c):
    """+1 / -1 / 0 per 4H bar: the trend of the last COMPLETE UTC day (6 bars) ending at or before the bar's close,
    close vs EMA50 and EMA20 vs EMA50 on daily closes; 0 for the first 50 complete days."""
    days = []                                                            # (day, index of its 6th bar)
    k = 0; n = len(t)
    while k < n:
        d = t[k] // DAY; j = k
        while j < n and t[j] // DAY == d: j += 1
        if j - k == 6: days.append((d, k + 5))
        k = j
    C = [c[ix] for _, ix in days]
    e20, e50 = ema(C, 20), ema(C, 50)
    tr = [(1 if (C[m] > e50[m] and e20[m] > e50[m]) else -1 if (C[m] < e50[m] and e20[m] < e50[m]) else 0) for m in range(len(C))]
    for m in range(min(50, len(tr))): tr[m] = 0
    ends = [(d + 1) * DAY for d, _ in days]
    out = []
    for ti in t:
        m = bisect.bisect_right(ends, ti + H4) - 1
        out.append(tr[m] if m >= 0 else 0)
    return out


# ------------------------------------------------------------------ funding
def funding_avg(times, rates, t_close):
    """8h-equivalent average funding over the 72 hours before t_close: the sum of every payment in (t_close - 72h,
    t_close] divided by 9. For 8-hour funding this is exactly the research's 'average of the last 9 payments'.
    None when the history does not cover the window."""
    if not times or times[0] > t_close - FUND_WINDOW + 8 * 3600 * 1000: return None
    a = bisect.bisect_right(times, t_close - FUND_WINDOW); b = bisect.bisect_right(times, t_close)
    if b - a < 3: return None
    return sum(rates[a:b]) / 9.0


def funding_paid(times, rates, side, te, tx):
    """fraction of notional paid (negative = received) by a position between te and tx: longs pay positive rates"""
    if not times: return 0.0
    a = bisect.bisect_right(times, te); b = bisect.bisect_right(times, tx); s = sum(rates[a:b])
    return s if side == "L" else -s


# ------------------------------------------------------------------ rules
def crossing(z_prev, z_now):
    """+1 long crossing, -1 short crossing, 0 none"""
    if z_prev is None or z_now is None or math.isnan(z_prev) or math.isnan(z_now): return 0
    if z_now > Z_IN and z_prev <= Z_IN: return 1
    if z_now < -Z_IN and z_prev >= -Z_IN: return -1
    return 0


def entry_allowed(side, btc_trend, favg):
    """(allowed, reason). Long: BTC 1D trend up and funding not overheated. Short: BTC 1D trend down and funding > 0."""
    if side == "L":
        if btc_trend != 1: return False, "BTC daily trend not up"
        if favg is not None and favg > FUND_CAP_LONG: return False, "funding overheated"
        return True, "ok"
    if btc_trend != -1: return False, "BTC daily trend not down"
    if favg is None: return False, "no funding history"
    if not favg > 0: return False, "funding not positive"
    return True, "ok"


def initial_stop(side, close_i, atr_i, entry):
    s = 1 if side == "L" else -1
    stop = close_i - s * STOP_ATR * atr_i
    if s * (entry - stop) < MINSTOP * abs(entry): stop = entry - s * MINSTOP * abs(entry)
    return stop


def exit_now(side, z_j):
    return (z_j < 0) if side == "L" else (z_j > 0)


# ------------------------------------------------------------------ one coin's trades (backtest == live logic)
def _walk(t, o, h, l, c, z, i, side, stop):
    """trade entered at the open of bar i+1 after a signal at bar i, walked bar by bar: stop first, then the trend
    flip at the close (exit at the next open), at most HOLD bars. exit_i stays None while the trade is open."""
    n = len(c); e = i + 1; s = 1 if side == "L" else -1; ep = o[e]; risk = s * (ep - stop)
    tr = dict(side=side, signal_i=i, entry_i=e, entry_t=int(t[e]), entry_p=ep, stop=stop, risk=risk,
              exit_i=None, exit_t=None, exit_p=None, reason=None, r_gross=None)
    for j in range(e, min(n, e + HOLD)):
        if (l[j] <= stop) if s == 1 else (h[j] >= stop):
            tr.update(exit_i=j, exit_t=int(t[j]) + H4, exit_p=stop, reason="STOP", r_gross=-1.0); return tr
        if exit_now(side, z[j]) and j + 1 < n:
            tr.update(exit_i=j + 1, exit_t=int(t[j + 1]) + H4, exit_p=o[j + 1], reason="TREND_FLIP",
                      r_gross=s * (o[j + 1] - ep) / risk); return tr
        if exit_now(side, z[j]) and j + 1 >= n:
            tr.update(pending_exit=True); return tr                      # flip at the last closed bar: exit at the next open
    if e + HOLD - 1 <= n - 1:
        j = e + HOLD - 1
        tr.update(exit_i=j, exit_t=int(t[j]) + H4, exit_p=c[j], reason="TIME", r_gross=s * (c[j] - ep) / risk)
    return tr


def coin_trades(t, o, h, l, c, z, a, btc_trend_at, favg_at, start=WARMUP, last_signal_i=None):
    """the system's trades for one coin, in time order, with the one-open-trade-per-coin rule.
    btc_trend_at(bar open time) -> -1/0/1 ; favg_at(bar close time) -> float or None.
    The last trade may still be open (exit_i None). Signals at bars >= last_signal_i are not taken (None = all)."""
    out = []; busy_until = -1                                            # exit time of the previous trade
    n = len(c); top = n if last_signal_i is None else min(n, last_signal_i)
    for i in range(max(start, 1), top):
        sgn = crossing(z[i - 1], z[i])
        if not sgn or i + 1 >= n: continue
        side = "L" if sgn == 1 else "S"
        te = int(t[i + 1])
        if te < busy_until: continue
        ok, _ = entry_allowed(side, btc_trend_at(int(t[i])), favg_at(int(t[i]) + H4))
        if not ok: continue
        stop = initial_stop(side, c[i], a[i], o[i + 1])
        tr = _walk(t, o, h, l, c, z, i, side, stop)
        out.append(tr)
        if tr["exit_t"] is None: break                                   # still open: blocks the coin
        busy_until = tr["exit_t"]
    return out


def net_r(tr, f_times, f_rates, fee=FEE):
    """net R of a closed trade: gross R minus fees and funding, both in units of the risk"""
    rp = tr["risk"] / abs(tr["entry_p"])
    f = funding_paid(f_times, f_rates, tr["side"], tr["entry_t"], tr["exit_t"])
    return tr["r_gross"] - fee / rp - f / rp


# ------------------------------------------------------------------ optional risk rules (research_archive/openinterest, situations)
def oi_above_mean(oi_points, t_signal):
    """L_OI_HIGH: open interest above its 30-day mean, from hourly points [(hour_start_ms, oi)], using only hours that
    end one hour before the signal bar's close (start <= t_signal + 2h). None if under 90% of the 720 hours exist."""
    cut = t_signal + 2 * 3600 * 1000
    pts = [(ts, v) for ts, v in oi_points if ts <= cut and v and v > 0]
    if not pts: return None
    last_ts, last_v = pts[-1]
    if last_ts < cut - 3600 * 1000: return None
    window = [v for ts, v in pts if ts > cut - 720 * 3600 * 1000]
    if len(window) < 648: return None
    return math.log(last_v / (sum(window) / len(window))) > 0


def correlation(xs, ys):
    n = len(xs)
    if n < 2: return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs); syy = sum((y - my) ** 2 for y in ys); sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return sxy / math.sqrt(sxx * syy) if sxx > 0 and syy > 0 else None


def returns_by_time(t, c, t_end, bars=180):
    """{bar open time: 4H log return} for the `bars` bars ending at t_end"""
    out = {}
    k = bisect.bisect_right(t, t_end) - 1
    for j in range(max(1, k - bars + 1), k + 1): out[int(t[j])] = math.log(c[j] / c[j - 1])
    return out


def corr_cap_blocks(new_returns, open_returns_list, rho=0.7, k=3, min_shared=150):
    """CORR_CAP: True when k or more open same-direction trades are in coins correlated > rho with the new coin"""
    hits = 0
    for other in open_returns_list:
        keys = [ts for ts in new_returns if ts in other]
        if len(keys) < min_shared: continue
        r = correlation([new_returns[ts] for ts in keys], [other[ts] for ts in keys])
        if r is not None and r > rho: hits += 1
    return hits >= k


# ------------------------------------------------------------------ sizing
def order_qty(equity, risk_pct, entry, stop, qty_step, min_qty, min_notional, max_risk_on_min_pct):
    """(qty, risk_usd, note): the position that risks risk_pct of equity at the stop, rounded down to the lot step.
    Below the exchange minimum the minimum is used only if its risk stays within max_risk_on_min_pct."""
    dist = abs(entry - stop)
    if dist <= 0 or entry <= 0 or equity <= 0 or qty_step <= 0: return 0.0, 0.0, "invalid inputs"
    want = equity * risk_pct / 100.0
    steps = math.floor(want / dist / qty_step + 1e-9)
    qty = steps * qty_step
    q_min = max(min_qty, math.ceil((min_notional / entry) / qty_step - 1e-9) * qty_step)
    note = "ok"
    if qty < q_min - 1e-12:
        if q_min * dist <= equity * max_risk_on_min_pct / 100.0:
            qty = q_min; note = "raised to the exchange minimum"
        else:
            return 0.0, 0.0, f"below the exchange minimum (needs {q_min * dist / equity * 100:.1f}% risk)"
    dec = max(0, -int(math.floor(math.log10(qty_step)))) if qty_step < 1 else 0
    qty = round(qty, dec)
    return qty, qty * dist, note
