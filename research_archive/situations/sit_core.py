"""Shared engine for the situation tests (PREREG_situations.md).
- the final funding-aware Kalman system's candidate entries and signals (exactly as longshort/ls_fundcap.py)
- simulate(): bar-by-bar replay of one trade with optional partial targets, breakeven, trailing stop, full take-profit,
  gap-aware stops, slippage, fast-bar slippage and cost multipliers (standard settings reproduce the stored results)
- sequence(): one trade per coin (the rule of ls_improve.trades); port(): the $10 portfolio of prereg_run.equity with
  optional per-trade weights, direction caps, a risk taper and a correlation cap
- statistics: day-clustered mean / se, kept-vs-removed separation, paired differences"""
import os, sys, pickle, math, collections, time
os.environ["TL_WITH_D6"] = "1"
import numpy as np
import tl_core as T, funding as FU
from ls_improve import trades, YEARS, year
from inv_run import seg
FEE = 14e-4; MINSTOP = 0.005; HOLD = 120; DAY = 86400000; H4 = T.H4
HOLDS = ("VAL", "FINAL", "UNSEEN", "OLD"); SEGS = ("DEV",) + HOLDS

# ---------------------------------------------------------------- signals
E = pickle.load(open("ls_entries.pkl", "rb"))
for r in E:
    r["favg"] = None; ft, fcum = FU.series(r["sym"])
    if ft:
        b = int(np.searchsorted(ft, r["t"] + 4 * 3600000, side="right"))
        if b >= 9: r["favg"] = (fcum[b] - fcum[b - 9]) / 9
E2 = [r for r in E if not (r["side"] == "L" and r["favg"] is not None and r["favg"] > 0.0003)]
FINAL = trades(E2, {"SHORT_FUND"})                                   # (te, sym, R, tx, t0, side), one per coin
CANDS = [r for r in E2 if r["btc"] == 1 and r["en"] == "KAL1" and (r["side"] == "L" or r["fund"] is True)]

def sequence(rows):
    """rows (te, sym, R, tx, t0, side): one open trade per coin, exactly the rule of ls_improve.trades"""
    keep, last = [], {}
    for x in sorted(rows):
        if x[0] < last.get(x[1], 0): continue
        last[x[1]] = x[3]; keep.append(x)
    return keep

# ---------------------------------------------------------------- price series (cached)
_ser = {}
CACHE = "sit_series.pkl"
if os.path.exists(CACHE): _ser = pickle.load(open(CACHE, "rb"))
def series(sym, inv=False):
    key = (sym, bool(inv))
    if key not in _ser:
        d = T.load4h(sym, inv)
        if d is None: _ser[key] = None
        else:
            t, o, h, l, c, v = d; a = T.atr14(h, l, c); z = T.kalman_trend(c)
            _ser[key] = dict(t=t, o=o, h=h, l=l, c=c, v=v, a=a, z=z, idx={int(x): k for k, x in enumerate(t)})
    return _ser[key]
def save_cache(): pickle.dump(_ser, open(CACHE, "wb"))

# ---------------------------------------------------------------- one trade, bar by bar
def simulate(sym, side, t0, targets=(), be_at=None, be_after_first=False, trail_at=None, full_tp=None,
             gap=False, slip=0.0, fastbar=0.0, fee_mult=1.0, fund_mult=1.0, detail=False):
    """Kalman trade from the signal bar t0: entry next open, 3-ATR stop (min 0.5%), exit at the next open after z < 0,
    at most 120 bars. Within a bar: stop first, then targets (limit fills), then the z exit at the close; stop changes
    apply from the next bar. Returns (net R, te, tx) or with detail=True also (parts, mfe in R)."""
    S = series(sym, side == "S"); t, o, h, l, c, a, z = S["t"], S["o"], S["h"], S["l"], S["c"], S["a"], S["z"]
    i = S["idx"][int(t0)]; n = len(c); e = i + 1; ep = o[e]; stop = c[i] - 3 * a[i]
    if ep - stop < MINSTOP * abs(ep): stop = ep - MINSTOP * abs(ep)
    risk = ep - stop; rp = risk / abs(ep); epf = ep + slip * abs(ep)
    tg = sorted(targets); rem = 1.0; parts = []; cur = stop; trail_on = False; peak = -1e300; mfe = 0.0
    for j in range(e, min(n, e + HOLD)):
        if l[j] <= cur:                                                  # 1. stop (market order)
            px = cur
            if gap and o[j] < cur: px = o[j]
            if fastbar and (h[j] - l[j]) > 2 * a[j]: px -= fastbar * a[j]
            px -= slip * abs(px); parts.append((rem, px, j)); rem = 0.0; break
        mfe = max(mfe, (h[j] - ep) / risk)
        if full_tp is not None and h[j] >= ep + full_tp * risk:          # 2. targets (limit orders)
            parts.append((rem, ep + full_tp * risk, j)); rem = 0.0; break
        hit = False
        while tg and h[j] >= ep + tg[0][0] * risk and rem > 1e-12:
            k, frac = tg.pop(0); q = min(frac, rem); parts.append((q, ep + k * risk, j)); rem -= q; hit = True
        if rem <= 1e-12: break
        if z[j] < 0 and j + 1 < n:                                       # 3. z exit at the next open
            px = o[j + 1] - slip * abs(o[j + 1]); parts.append((rem, px, j + 1)); rem = 0.0; break
        peak = max(peak, c[j])                                           # highest close since entry
        if be_at is not None and h[j] >= ep + be_at * risk: cur = max(cur, ep)      # 4. stop changes for the next bar
        if be_after_first and hit: cur = max(cur, ep)
        if trail_at is not None:
            if not trail_on and h[j] >= ep + trail_at * risk: trail_on = True
            if trail_on: cur = max(cur, peak - 3 * a[j])
    if rem > 1e-12:
        j = min(n - 1, e + HOLD - 1); px = c[j] - slip * abs(c[j]); parts.append((rem, px, j))
    te = int(t[e]); R = 0.0; txs = []
    for q, px, j in parts:
        tx = int(t[j]) + H4; f, _ = FU.paid(sym, side, te, tx)
        if f > 0: f *= fund_mult
        R += q * ((px - epf) / risk - f / rp); txs.append(tx)
    R -= FEE * fee_mult / rp
    out = (R, te, max(txs))
    return (out, parts, mfe, rp) if detail else out

def variant_trades(**kw):
    """all candidate entries re-simulated with one variant, then one trade per coin (as the system does)"""
    rows = []
    for r in CANDS:
        R, te, tx = simulate(r["sym"], r["side"], r["t"], **kw); rows.append((te, r["sym"], R, tx, r["t"], r["side"]))
    return sequence(rows)

# ---------------------------------------------------------------- portfolio
def port(tr, risk=0.005, maxpos=8, start=10.0, weight=None, dircap=None, taper=None, corrcap=None, record=False):
    """prereg_run.equity generalised. tr rows (te, sym, R, tx, t0, side). weight(row) -> risk multiplier;
    dircap = max open per direction; taper = function(n_open) -> risk; corrcap(row, open_rows) -> True to skip."""
    openp = []; eq = start; pk = start; dd = 0.0; taken = 0; rec = []
    for row in sorted(tr):
        te, sym, R, tx = row[:4]; sd = row[5]
        for p in sorted([p for p in openp if p[0] <= te], key=lambda p: p[:3]):
            openp.remove(p); eq += p[1]; pk = max(pk, eq); dd = min(dd, eq / pk - 1)
        if len(openp) >= maxpos or any(p[2] == sym for p in openp) or eq < 1: continue
        if dircap is not None and sum(1 for p in openp if p[3] == sd) >= dircap: continue
        if corrcap is not None and corrcap(row, [p[4] for p in openp]): continue
        rk = taper(len(openp)) if taper else risk
        if weight is not None: rk *= weight(row)
        pnl = eq * rk * R; openp.append((tx, pnl, sym, sd, row)); taken += 1
        if record: rec.append((row, pnl, eq))
    for p in sorted(openp, key=lambda p: p[:3]): eq += p[1]; pk = max(pk, eq); dd = min(dd, eq / pk - 1)
    return (eq, taken, dd * 100, rec) if record else (eq, taken, dd * 100)

def port_years(tr, **kw):
    """per calendar year of the signal, restarted from $10 each year: {year: (end equity, trades, max DD %)}"""
    return {y: port([x for x in tr if year(x[4]) == y], **kw) for y in YEARS}

def mar(eq, dd): return (eq / 10.0 - 1) / max(abs(dd) / 100, 1e-9)

def port_line(lab, tr, **kw):
    e1, n1, d1 = port(tr, **kw); e2, n2, d2 = port([x for x in tr if year(x[4]) >= 2024], **kw)
    py = port_years(tr, **kw)
    return dict(lab=lab, all=(e1, n1, d1), recent=(e2, n2, d2), years=py,
                text=f"  {lab:26s} " + " ".join(f"{py[y][0]:6.2f}" for y in YEARS) +
                     f" | ${e1:7.2f} ({n1:4d} trades, DD {d1:4.0f}%) | ${e2:6.2f} ({n2:4d} trades, DD {d2:4.0f}%)")
PORT_HEAD = "  " + " " * 26 + " " + " ".join(f"{y:>6d}" for y in YEARS) + " | 2020-2026                        | 2024-2026"

# ---------------------------------------------------------------- statistics
def mse(pairs):
    d = collections.defaultdict(list)
    for t, r in pairs: d[t // DAY].append(r)
    n = sum(len(v) for v in d.values())
    if n == 0: return 0, float("nan"), float("nan")
    m = sum(x for v in d.values() for x in v) / n
    if n < 2: return n, m, float("nan")
    return n, m, math.sqrt(sum((sum(v) - m * len(v)) ** 2 for v in d.values())) / n

def sep_t(k, x):
    nk, mk, sk = mse(k); nx, mx, sx = mse(x)
    if nk < 2 or nx < 2: return float("nan"), (nk, mk), (nx, mx)
    return (mk - mx) / math.sqrt(sk ** 2 + sx ** 2), (nk, mk), (nx, mx)

def segment(sym, t0): return seg({"t": t0, "sym": sym})
