"""
AMD-FVG 1H setup (research_archive/discovery_loop) -- frozen parameters for forward testing.

Accumulation (compressed range) -> Manipulation (sweep below the range low)
-> Distribution (displacement candle leaving a fair value gap) -> limit entry at the
gap's 50% level, stop below the sweep, target 2R, 96-bar timeout. Shorts are the exact
mirror (computed on price-inverted bars). Pure functions, no I/O; identical logic to
research_archive/discovery_loop/disc.py (family "AMD") so forward results are
comparable with the backtest.
"""

FEE_LIMIT = 8e-4           # round-trip cost assumed for limit entries
FILL_PEN = 0.05            # price must trade this many ATR through the limit to count as filled

# Frozen, pre-declared configurations. PRIMARY is the one that was selected and tested
# out of sample; NO_GATE is the same rule without the counter-drift gate, logged as a
# secondary comparison (it did as well or better on unseen data).
PRIMARY = dict(name="AMD_PRIMARY", gate=2, mr=0.008, tp=2.0, tmax=96, N=48, D=0.8, v=1.2,
               w=12, W=6, comp=10, s=0.6, b=0.1)
NO_GATE = dict(PRIMARY, name="AMD_NO_GATE", gate=0)
CONFIGS = (PRIMARY, NO_GATE)


def _ema(x, p):
    k = 2 / (p + 1)
    r = [x[0]]
    for v in x[1:]:
        r.append(v * k + r[-1] * (1 - k))
    return r


def prep(bars, invert=False):
    """bars: list of (t_ms, o, h, l, c, v) closed candles, oldest first."""
    if invert:
        bars = [(b[0], -b[1], -b[3], -b[2], -b[4], b[5]) for b in bars]
    n = len(bars)
    t = [b[0] for b in bars]; o = [b[1] for b in bars]; h = [b[2] for b in bars]
    l = [b[3] for b in bars]; c = [b[4] for b in bars]; v = [b[5] for b in bars]
    tr = [h[0] - l[0]] + [max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])) for i in range(1, n)]
    a = _ema(tr, 20); vm = _ema(v, 30)
    atr = [a[0]] + a[:-1]
    vrel = [v[i] / max(vm[i - 1], 1e-12) if i > 0 else 1 for i in range(n)]
    rmax = {}; rmin = {}
    for N in (12, 24, 48, 96):
        rmax[N] = [None] * n; rmin[N] = [None] * n
        for i in range(N, n):
            rmax[N][i] = max(h[i - N:i]); rmin[N][i] = min(l[i - N:i])
    return dict(n=n, t=t, o=o, h=h, l=l, c=c, v=v, atr=atr, vrel=vrel, rmax=rmax, rmin=rmin)


def _drift_ok(A, i, gate):
    if gate == 0:
        return True
    if i < 96:
        return False
    up = A["c"][i] > A["c"][i - 96]
    return up if gate == 1 else (not up)


def _outcome(A, e, ep, stop, tp, tmax, fee):
    """Limit fill at bar e. Fill bar may only stop out. Returns (status, R, exit_index)."""
    h, l, c, n = A["h"], A["l"], A["c"], A["n"]
    risk = ep - stop
    rp = risk / abs(ep)
    if l[e] <= stop:
        return "CLOSED", -1 - fee / rp, e, "STOP"
    for j in range(e + 1, min(n, e + tmax)):
        if l[j] <= stop:
            return "CLOSED", -1 - fee / rp, j, "STOP"
        if h[j] >= tp:
            return "CLOSED", (tp - ep) / risk - fee / rp, j, "TARGET"
    if e + tmax - 1 <= n - 1:
        j = e + tmax - 1
        return "CLOSED", (c[j] - ep) / risk - fee / rp, j, "TIMEOUT"
    j = n - 1
    return "OPEN", (c[j] - ep) / risk - fee / rp, j, "MARK"


def scan(A, P, side="L", fee=FEE_LIMIT, fill_pen=FILL_PEN, since_t=None):
    """Return every AMD setup in A with its current state.
    Each record: signal_t (FVG completion bar open time), status PENDING/FILLED-OPEN/CLOSED/CANCELLED/EXPIRED,
    entry/stop/target in real prices, R (net, current mark for open trades)."""
    out = []; n = A["n"]; N = P["N"]; last = -99
    h, l, c, o, atr, vr, t = A["h"], A["l"], A["c"], A["o"], A["atr"], A["vrel"], A["t"]
    sg = 1 if side == "L" else -1
    for i in range(110, n):
        if i - last < 6:
            continue
        at = atr[i]
        if at <= 0 or not _drift_ok(A, i, P["gate"]):
            continue
        if not (l[i] > h[i - 2] and (c[i - 1] - o[i - 1]) >= P["D"] * atr[i - 1] and vr[i - 1] >= P["v"]):
            continue
        hit = None
        for j in range(i - 2 - P["W"], i - 1):
            RL = A["rmin"][N][j]; RH0 = A["rmax"][N][j]
            if RL is None or (RH0 - RL) / atr[j] > P["comp"]:
                continue
            if l[j] < RL - P["s"] * atr[j] and c[i - 1] > RL:
                hit = (j, RL, RH0); break
        if not hit:
            continue
        j, RL, RH = hit
        sl = min(l[j:i]) - P["b"] * at
        zb, zt = h[i - 2], l[i]; ce = (zb + zt) / 2
        risk = ce - sl
        if risk <= 0 or risk / abs(ce) < P["mr"]:
            continue
        tp = ce + P["tp"] * risk
        rec = dict(config=P["name"], side="LONG" if sg == 1 else "SHORT", signal_t=t[i],
                   entry=sg * ce, stop=sg * sl, target=sg * tp, risk_pct=risk / abs(ce) * 100)
        filled = None; state = None
        for w in range(1, P["w"] + 1):
            q = i + w
            if q >= n:
                state = "PENDING"; break
            if c[q] < zb:
                state = "CANCELLED"; break
            if l[q] <= ce - fill_pen * atr[i]:
                filled = q; break
        else:
            state = "EXPIRED"
        if filled is not None:
            st, R, xj, why = _outcome(A, filled, ce, sl, tp, P["tmax"], fee)
            rec.update(status=st, fill_t=t[filled], exit_t=t[xj], exit_reason=why, R=round(R, 4))
            last = i
        else:
            rec.update(status=state, fill_t=None, exit_t=None, exit_reason=None, R=None)
            # the backtest only advanced the 6-bar spacing after a *filled* trade
        if since_t is None or rec["signal_t"] >= since_t:
            out.append(rec)
    return out
