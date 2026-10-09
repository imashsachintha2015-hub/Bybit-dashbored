"""Tests 1, 5, 7, 8, 9 of PREREG_situations.md (committed before running): filters on the final system's signals.
cond(sym, t0, side) -> True = the situation holds (skip / remove), False = keep, None = no data."""
import math, time, json, calendar, collections
import numpy as np
import sit_core as C, inv_core as I
from ls_improve import YEARS, year
H4 = C.H4; DAYMS = 86400000
ms = lambda s: calendar.timegm(time.strptime(s, "%Y-%m-%d")) * 1000

# ------------------------------------------------ test 1: chop measures (real prices, data up to the signal bar's close)
def idx(sym, t0):
    S = C.series(sym, False); return S, S["idx"].get(int(t0))

def er180(sym, t0, n=180):
    S, i = idx(sym, t0)
    if i is None or i < n: return None
    y = np.log(S["c"][i - n:i + 1]); den = np.abs(np.diff(y)).sum()
    return abs(y[-1] - y[0]) / den if den > 0 else None

_vr = {}
def vr120(sym, t0):
    S, i = idx(sym, t0)
    if sym not in _vr: _vr[sym] = I.variance_ratio(S["c"], 6, 120)
    if i is None or not np.isfinite(_vr[sym][i]): return None
    return float(_vr[sym][i])

def pe3(sym, t0, win=180):
    S, i = idx(sym, t0)
    if i is None or i < win - 1: return None
    w = S["c"][i - win + 1:i + 1]
    perm = np.argsort(np.stack([w[:-2], w[1:-1], w[2:]], 1), axis=1, kind="stable")
    _, cnt = np.unique(perm[:, 0] * 9 + perm[:, 1] * 3 + perm[:, 2], return_counts=True)
    N = cnt.sum(); p = cnt / N
    return float((-(p * np.log(p)).sum() + (len(cnt) - 1) / (2 * N)) / math.log(6))     # Miller-Madow, normalised

def squeeze(sym, t0, n=180):
    S, i = idx(sym, t0)
    if i is None or i < n: return None
    h, l, c = S["h"], S["l"], S["c"]
    tr = np.maximum(h[i - n + 1:i + 1] - l[i - n + 1:i + 1], np.maximum(np.abs(h[i - n + 1:i + 1] - c[i - n:i]), np.abs(l[i - n + 1:i + 1] - c[i - n:i])))
    m = tr.mean(); return float(S["a"][i] / m) if m > 0 else None

hist = collections.defaultdict(list)                                     # the system's own signals per coin (shadow history)
for te, sym, R, tx, t0, sd in C.FINAL: hist[sym].append((tx, R))
for s in hist: hist[s].sort()
def whipsaw(sym, t0):
    prev = [R for tx, R in hist[sym] if tx <= t0 + H4]
    return None if len(prev) < 2 else (prev[-1] < 0 and prev[-2] < 0)

_, _, _, REC = C.port(C.FINAL, record=True)
closed = sorted((row[3], row[2]) for row, pnl, eq in REC)                # (exit time, R) of trades the $10 portfolio took
def cold(sym, t0):
    prev = [R for tx, R in closed if tx <= t0 + H4]
    return None if len(prev) < 10 else sum(1 for R in prev[-10:] if R > 0) <= 2

# ------------------------------------------------ test 5: relative strength
def ret30(sym, t0):
    S = C.series(sym, False); i = S["idx"].get(int(t0)); j = S["idx"].get(int(t0) - 180 * H4)
    return None if i is None or j is None else float(np.log(S["c"][i] / S["c"][j]))
ALTS = sorted({x[1] for x in C.FINAL} - {"BTC"})
_season = {}
def alt_share(t0):
    if t0 not in _season:
        b = ret30("BTC", t0); rs = [ret30(s, t0) for s in ALTS]; rs = [r for r in rs if r is not None]
        _season[t0] = None if b is None or len(rs) < 10 else sum(1 for r in rs if r > b) / len(rs)
    return _season[t0]

# ------------------------------------------------ tests 7-9
FOMC = set(json.load(open("fomc_dates.json")))
FOMC_WIN = set()
for d in FOMC:
    t = ms(d); FOMC_WIN |= {t - DAYMS, t}
    if d in ("2020-03-02", "2020-03-15"): FOMC_WIN.add(t + DAYMS)
def first_bar(sym): return int(C.series(sym, False)["t"][0])

def gt(v, x): return None if v is None else v > x
def lt(v, x): return None if v is None else v < x
FILTERS = {   # name: (family, applies(sym, side), cond(sym, t0, side) -> True = remove)
    "COIN_CHOP":    ("1 chop", lambda s, d: True, lambda s, t, d: lt(er180(s, t), 1 / math.sqrt(180))),
    "BTC_CHOP":     ("1 chop", lambda s, d: True, lambda s, t, d: lt(er180("BTC", t), 1 / math.sqrt(180))),
    "MEAN_REVERT":  ("1 chop", lambda s, d: True, lambda s, t, d: lt(vr120(s, t), 1.0)),
    "DISORDER":     ("1 chop", lambda s, d: True, lambda s, t, d: gt(pe3(s, t), 0.967)),
    "SQUEEZE":      ("1 chop", lambda s, d: True, lambda s, t, d: lt(squeeze(s, t), 0.75)),
    "COIN_WHIPSAW": ("1 chop", lambda s, d: True, lambda s, t, d: whipsaw(s, t)),
    "SYSTEM_COLD":  ("1 chop", lambda s, d: True, lambda s, t, d: cold(s, t)),
    "RS_LEADER":    ("5 relative strength", lambda s, d: d == "L" and s != "BTC",
                     lambda s, t, d: None if ret30(s, t) is None or ret30("BTC", t) is None else not (ret30(s, t) > ret30("BTC", t))),
    "ALT_SEASON":   ("5 relative strength", lambda s, d: d == "L" and s != "BTC",
                     lambda s, t, d: None if alt_share(t) is None else not (alt_share(t) > 0.5)),
    "RS_LAGGARD":   ("5 relative strength", lambda s, d: d == "S" and s != "BTC",
                     lambda s, t, d: None if ret30(s, t) is None or ret30("BTC", t) is None else not (ret30(s, t) < ret30("BTC", t))),
    "WEEKEND":      ("7-9 lower priority", lambda s, d: True, lambda s, t, d: time.gmtime((t + H4) / 1000).tm_wday >= 5),
    "NEW_LISTING":  ("7-9 lower priority", lambda s, d: True,
                     lambda s, t, d: first_bar(s) >= ms("2020-02-01") and t < first_bar(s) + 180 * DAYMS),
    "FOMC":         ("7-9 lower priority", lambda s, d: True, lambda s, t, d: ((t + H4) // DAYMS) * DAYMS in FOMC_WIN),
}

_cc = {}
def cond(name, sym, t0, side):
    k = (name, sym, int(t0), side)
    if k not in _cc:
        v = FILTERS[name][2](sym, int(t0), side)
        _cc[k] = None if v is None else bool(v)                 # numpy booleans fail "is True" checks
    return _cc[k]

fmt = lambda nm: f"{nm[1]:+.3f} ({nm[0]:4d})" if nm[0] else "    -        "
results = {}
print(f"parity: final system ${C.port(C.FINAL)[0]:.2f} ({C.port(C.FINAL)[1]} trades); signals {len(C.FINAL)}; candidate entries {len(C.CANDS)}")
print("25 t-based tests this round: about 0.6 PASS and 0.05 PASS strict expected by luck\n")
for fam in ("1 chop", "5 relative strength", "7-9 lower priority"):
    print(f"=================== test {fam}: average net R per signal (n) | benchmark = signals with data | kept | removed (the situation) ===")
    for name, (f_, app, _) in FILTERS.items():
        if f_ != fam: continue
        sig = [x for x in C.FINAL if app(x[1], x[5])]
        rows = {sg: [] for sg in C.SEGS}
        for te, sym, R, tx, t0, sd in sig:
            sg = C.segment(sym, t0); v = cond(name, sym, t0, sd)
            if sg in rows and v is not None: rows[sg].append((t0, R, v, sd))
        print(f"  {name}")
        wins = counted = 0
        for sg in C.SEGS:
            b = [(t, R) for t, R, v, sd in rows[sg]]; k = [(t, R) for t, R, v, sd in rows[sg] if not v]; x = [(t, R) for t, R, v, sd in rows[sg] if v]
            nb, mb, _ = C.mse(b); nk, mk, _ = C.mse(k); nx, mx, _ = C.mse(x); tag = ""
            if sg in C.HOLDS:
                if nb >= 20: counted += 1; w = nk > 0 and mk > mb; wins += w; tag = "win" if w else "loss"
                else: tag = "too few"
            print(f"    {sg:6s} bench {fmt((nb, mb))} | kept {fmt((nk, mk))} | removed {fmt((nx, mx))}  {tag}")
        H = [r for sg in C.HOLDS for r in rows[sg]]
        t_, (nk, mk), (nx, mx) = C.sep_t([(t, R) for t, R, v, sd in H if not v], [(t, R) for t, R, v, sd in H if v])
        verdict = "PASS strict" if t_ >= 2.9 and wins >= 3 else "PASS" if t_ >= 2.0 and wins >= 3 else "fail"
        results[name] = verdict
        A = [r for sg in C.SEGS for r in rows[sg]]
        side_txt = " | ".join(f"{'longs' if s_ == 'L' else 'shorts'} kept {C.mse([(t, R) for t, R, v, sd in A if sd == s_ and not v])[1]:+.3f} vs removed "
                              f"{C.mse([(t, R) for t, R, v, sd in A if sd == s_ and v])[1]:+.3f} (t {C.sep_t([(t, R) for t, R, v, sd in A if sd == s_ and not v], [(t, R) for t, R, v, sd in A if sd == s_ and v])[0]:+.2f})"
                              for s_ in ("L", "S") if any(sd == s_ for t, R, v, sd in A))
        print(f"    pooled holdouts: kept {mk:+.3f}R (n {nk}) vs removed {mx:+.3f}R (n {nx}), separation t = {t_:+.2f}; "
              f"wins {wins} of {counted} counted -> {verdict}   [removes {nx / max(nk + nx, 1) * 100:.0f}% of holdout signals]")
        print(f"    all segments by side (information): {side_txt}")
    print()

print("=================== $10 portfolios (0.5% risk, max 8 open, one per coin): per-year restarts, 2020-2026 and 2024-2026 compounded ===")
print(C.PORT_HEAD)
base = C.port_line("final system", C.FINAL); print(base["text"])
for name, (fam, app, _) in FILTERS.items():
    rows = []
    for r in C.CANDS:
        if app(r["sym"], r["side"]) and cond(name, r["sym"], r["t"], r["side"]) is True: continue
        R, te, tx = r["res"]["std"]; rows.append((te, r["sym"], R, tx, r["t"], r["side"]))
    L = C.port_line(f"skip {name}", C.sequence(rows))
    yrs = sum(1 for y in YEARS if L["years"][y][0] > base["years"][y][0] + 1e-9)
    print(L["text"] + f" | better {yrs}/7 years | {results[name]}")
    if fam == "1 chop":
        H = C.port_line(f"half-risk {name}", C.FINAL, weight=lambda row, nm=name, ap=app: 0.5 if (ap(row[1], row[5]) and cond(nm, row[1], row[4], row[5]) is True) else 1.0)
        print(H["text"] + f" | better {sum(1 for y in YEARS if H['years'][y][0] > base['years'][y][0] + 1e-9)}/7 years")
print("\nverdicts: " + ", ".join(f"{k} {v}" for k, v in results.items()))
