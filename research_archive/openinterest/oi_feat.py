"""Causal open-interest features at a 4H signal, exactly as PREREG_openinterest.md. Reads oi/{SYM}.npz (fetch_oi.py build).
For a signal on the 4H bar opening at t (ms; it closes at t + 4h and the trade enters at that open), only hourly rows whose hour
ends by t + 3h are used, i.e. rows starting at or before t + 2h. A window needs at least 90% of its hourly rows."""
import os
import numpy as np
H = 3600000
NAMES = ("OI24", "OI30", "LSR", "TK24")
_cache = {}

def grid(sym):
    """hourly arrays on a regular grid (missing hours = NaN); non-positive levels count as missing"""
    if sym not in _cache:
        p = os.path.join("oi", f"{sym}.npz")
        if not os.path.exists(p): _cache[sym] = None
        else:
            z = np.load(p, allow_pickle=False); hr = z["hour"].astype(np.int64)
            g0 = int(hr[0]); n = int((hr[-1] - g0) // H) + 1; idx = ((hr - g0) // H).astype(np.int64)
            G = dict(g0=g0, n=n)
            for k in ("oi", "oiv", "ls", "tk"):
                a = np.full(n, np.nan); a[idx] = z[k]; G[k] = a
            for k in ("oi", "oiv", "ls"): G[k][~(G[k] > 0)] = np.nan
            _cache[sym] = G
    return _cache[sym]

def cut_index(G, t):
    """index of the last hourly row that may be used for a signal on the 4H bar opening at t"""
    return int((t + 2 * H - G["g0"]) // H)

def feats(sym, t, G=None):
    G = grid(sym) if G is None else G
    out = dict.fromkeys(NAMES)
    if G is None: return out
    k = cut_index(G, t)
    if k < 0 or k >= G["n"]: return out
    oi, ls, tk = G["oi"], G["ls"], G["tk"]
    ok = lambda a, lo: lo >= 0 and np.isfinite(a[lo:k + 1]).mean() >= 0.9
    if np.isfinite(oi[k]) and k >= 24 and np.isfinite(oi[k - 24]) and ok(oi, k - 24):
        out["OI24"] = float(np.log(oi[k] / oi[k - 24]))
    if np.isfinite(oi[k]) and ok(oi, k - 719):
        out["OI30"] = float(np.log(oi[k] / np.nanmean(oi[k - 719:k + 1])))
    if np.isfinite(ls[k]) and ok(ls, k - 719):
        out["LSR"] = float(np.log(ls[k] / np.nanmedian(ls[k - 719:k + 1])))
    if ok(tk, k - 23):
        out["TK24"] = float(np.nanmean(tk[k - 23:k + 1]))
    return out

# the eight pre-registered filters: (side, feature, keep-condition)
FILTERS = {
    "S_RETAIL_LONG": ("S", "LSR", lambda x: x > 0), "S_OI_BUILD": ("S", "OI24", lambda x: x > 0),
    "S_OI_HIGH": ("S", "OI30", lambda x: x > 0), "S_TAKER_SELL": ("S", "TK24", lambda x: x < 0),
    "L_RETAIL_SHORT": ("L", "LSR", lambda x: x < 0), "L_OI_BUILD": ("L", "OI24", lambda x: x > 0),
    "L_OI_HIGH": ("L", "OI30", lambda x: x > 0), "L_TAKER_BUY": ("L", "TK24", lambda x: x > 0),
}
