"""Tests 4 and 6 of PREREG_situations.md (committed before running): liquidation flush setups (open interest) and
portfolio heat rules."""
import math, time, collections
import numpy as np
import sit_core as C, oi_feat as OF
from ls_improve import YEARS, year, btc_tags
H4 = C.H4; LN09 = math.log(0.9)

# ============================================================== test 4: liquidation flush
BT = btc_tags()["tr"]
def flush_trades(sym, inv, btc_need=None):
    S = C.series(sym, inv)
    if S is None or OF.grid(sym) is None: return []
    t, o, h, l, c, a = S["t"], S["o"], S["h"], S["l"], S["c"], S["a"]; n = len(c); side = "S" if inv else "L"
    def event(i):
        if i < 6 or not np.isfinite(a[i]) or c[i - 6] - c[i] < 3 * a[i]: return False
        v = OF.feats(sym, int(t[i]))["OI24"]
        return v is not None and v <= LN09
    out = []; busy_until = -1; prev = False
    for i in range(6, n - 1):
        ev = event(i); first = ev and not prev; prev = ev
        if not first or i <= busy_until: continue
        if btc_need is not None and BT.get(int(t[i]), 0) * (-1 if inv else 1) != btc_need: continue
        e = i + 1; ep = o[e]; stop = l[i - 5:i + 1].min() - 0.5 * a[i]
        if ep - stop < C.MINSTOP * abs(ep): stop = ep - C.MINSTOP * abs(ep)
        risk = ep - stop; rp = risk / abs(ep); tp = ep + 2 * risk; g = None
        for j in range(e, min(n, e + 18)):
            if l[j] <= stop: g, jx = -1.0, j; break
            if h[j] >= tp: g, jx = 2.0, j; break
        if g is None: jx = min(n - 1, e + 17); g = (c[jx] - ep) / risk
        te, tx = int(t[e]), int(t[jx]) + H4; f, _ = C.FU.paid(sym, side, te, tx)
        out.append((te, sym, g - C.FEE / rp - f / rp, tx, int(t[i]), side)); busy_until = jx
    return out

SETUPS = {"LONG_FLUSH": (False, None), "SHORT_SQUEEZE_END": (True, None), "LONG_FLUSH_BTCUP": (False, 1), "SHORT_SQUEEZE_BTCDOWN": (True, 1)}
SYMS = sorted({x[1] for x in C.FINAL})
HOLD3 = ("VAL", "FINAL", "UNSEEN")
print("=== TEST 4: liquidation flush (open interest fell >= 10% in 24h while price moved >= 3 ATR in 6 bars; target 2R, stop beyond the 6-bar extreme, 18 bars) ===")
res4 = {}; flush_lists = {}
for name, (inv, need) in SETUPS.items():
    tr = sorted(x for s in SYMS for x in flush_trades(s, inv, need)); flush_lists[name] = tr
    cells = []; pos = counted = 0
    for sg in C.SEGS:
        n, m, se = C.mse([(x[4], x[2]) for x in tr if C.segment(x[1], x[4]) == sg])
        cells.append(f"{m:+.3f} ({n:3d})" if n else "   -      ")
        if sg in HOLD3 and n >= 20: counted += 1; pos += m > 0
    n, m, se = C.mse([(x[4], x[2]) for x in tr if C.segment(x[1], x[4]) in HOLD3]); t_ = m / se if n > 1 and se > 0 else float("nan")
    win = np.mean([x[2] > 0 for x in tr]) * 100 if tr else 0
    if n < 30: v = "too rare"
    else: v = "PASS strict" if (m > 0 and t_ >= 2.9 and pos == 3) else "PASS" if (m > 0 and t_ >= 2.0 and pos == 3) else "fail"
    res4[name] = v
    print(f"  {name:22s} " + " | ".join(f"{sg} {cl}" for sg, cl in zip(C.SEGS, cells)) +
          f"\n  {'':22s} pooled holdouts {m:+.3f}R (n {n}, t {t_:+.2f}), positive in {pos} of {counted} counted holdouts, {len(tr)} events in all, {win:.0f}% winners -> {v}")
print("\n  $10 standalone (0.5% risk, max 8 open, one per coin; open-interest data from 2021-12, BTC from 2020-09):")
print(C.PORT_HEAD)
for name, tr in flush_lists.items(): print(C.port_line(name, C.sequence(tr))["text"])
for name, v in res4.items():
    if v.startswith("PASS"):
        both = C.sequence(C.FINAL + flush_lists[name]); print(C.port_line(f"system + {name}", both)["text"] + "   (add-on)")

# ============================================================== test 6: portfolio heat
print("\n=== TEST 6: too many trades in one direction (portfolio rules on the system's signals) ===")
R4 = {}
for sym in SYMS:
    S = C.series(sym, False); lr = np.full(len(S["c"]), np.nan); lr[1:] = np.log(S["c"][1:] / S["c"][:-1]); R4[sym] = (S["t"], lr, S["idx"])
_corr = {}
def corr(x, y, t0):
    k = (x, y, t0) if x < y else (y, x, t0)
    if k not in _corr:
        tx_, rx, ix = R4[x]; ty, ry, iy = R4[y]; i = ix.get(int(t0))
        if i is None or i < 180: _corr[k] = None
        else:
            ts = tx_[i - 179:i + 1]; a = rx[i - 179:i + 1]; b = np.array([ry[iy[int(s)]] if int(s) in iy else np.nan for s in ts])
            ok = np.isfinite(a) & np.isfinite(b)
            _corr[k] = float(np.corrcoef(a[ok], b[ok])[0, 1]) if ok.sum() >= 150 else None
    return _corr[k]
def corrcap(row, open_rows):
    sym, t0, sd = row[1], row[4], row[5]; k = 0
    for r in open_rows:
        if r[5] != sd: continue
        v = corr(sym, r[1], t0)
        if v is not None and v > 0.7: k += 1
    return k >= 3
RULES = {"DIR_CAP4": dict(dircap=4), "DIR_CAP6": dict(dircap=6),
         "RISK_TAPER": dict(taper=lambda n: 0.005 if n < 4 else 0.0035 if n < 6 else 0.0025), "CORR_CAP": dict(corrcap=corrcap)}
base = C.port_line("current rule (max 8)", C.FINAL)
print(C.PORT_HEAD + " | MAR 2020-26 / 2024-26 | years with higher MAR")
print(base["text"] + f" | {C.mar(*base['all'][::2]):5.2f} / {C.mar(*base['recent'][::2]):5.2f}")
res6 = {}
for name, kw in RULES.items():
    L = C.port_line(name, C.FINAL, **kw)
    m1, m2 = C.mar(*L["all"][::2]), C.mar(*L["recent"][::2])
    ym = sum(1 for y in YEARS if C.mar(L["years"][y][0], L["years"][y][2]) > C.mar(base["years"][y][0], base["years"][y][2]) + 1e-9)
    ok = m1 > C.mar(*base["all"][::2]) and m2 > C.mar(*base["recent"][::2]) and ym >= 4
    res6[name] = "PASS" if ok else "fail"
    print(L["text"] + f" | {m1:5.2f} / {m2:5.2f} | {ym}/7 -> {res6[name]}")
cnt = collections.Counter()
_, _, _, REC = C.port(C.FINAL, record=True)
for row, pnl, eq in REC:
    same = sum(1 for r2, p2, e2 in REC if r2[5] == row[5] and r2[0] <= row[0] < r2[3] and r2 is not row)
    cnt[min(same, 7)] += 1
print("  how many same-direction trades were already open when the current system took a trade: " +
      ", ".join(f"{k}: {cnt[k]}" for k in range(8)))
print("\nverdicts test 4: " + ", ".join(f"{k} {v}" for k, v in res4.items()) + " | test 6: " + ", ".join(f"{k} {v}" for k, v in res6.items()))
