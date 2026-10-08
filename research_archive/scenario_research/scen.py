"""Scenario setup research harness (research_archive/PROMPT_scenario_setup_research.md).
1H execution, 1D bias (closed days only), BTC daily context. Long logic; shorts = price inversion.
Each worker returns, per coin/side, every scenario event with tags and its outcome for each target variant
(gross R, risk %, hold hours, entry/exit time), so costs and filters are applied later without re-simulating."""
import json, os, sys, math, random
from multiprocessing import Pool
D = "d4"; HR = 3600000; DAY = 86400000
HOLD = 96; FILL_PEN = 0.05
FIBS = (0.382, 0.5, 0.635, 0.705, 0.786)           # 0.635 = golden pocket mid (0.618-0.65), 0.705 = OTE mid

def load(sym, inv):
    rows = json.load(open(f"{D}/{sym}_1H.json"))
    t = [r[0] for r in rows]; o = [r[1] for r in rows]; h = [r[2] for r in rows]; l = [r[3] for r in rows]; c = [r[4] for r in rows]; v = [r[5] for r in rows]
    if inv: o, h, l, c = [-x for x in o], [-x for x in l], [-x for x in h], [-x for x in c]
    return t, o, h, l, c, v

def ema(x, n):
    a = 2 / (n + 1); out = [x[0]]
    for z in x[1:]: out.append(out[-1] + a * (z - out[-1]))
    return out

def daily_trend(t, o, h, l, c):
    """trend of the last COMPLETE day at the close of each 1H bar: +1 up, -1 down, 0 none; plus day-start map."""
    days = {}; order = []
    for i, ts in enumerate(t):
        d = ts // DAY
        if d not in days: days[d] = [o[i], h[i], l[i], c[i]]; order.append(d)
        else:
            x = days[d]; x[1] = max(x[1], h[i]); x[2] = min(x[2], l[i]); x[3] = c[i]
    C = [days[d][3] for d in order]; e20 = ema(C, 20); e50 = ema(C, 50)
    tr = {}
    for k, d in enumerate(order):
        tr[d] = 1 if (C[k] > e50[k] and e20[k] > e50[k]) else (-1 if (C[k] < e50[k] and e20[k] < e50[k]) else 0)
        if k < 50: tr[d] = 0
    out = []
    for i, ts in enumerate(t):
        d = ts // DAY
        done = d if (ts + HR) % DAY == 0 else d - 1          # last day fully closed at this bar's close
        out.append(tr.get(done, 0))
    return out, tr

def sim(h, l, c, t, e, ep, stop, tp, limit_fill):
    """entry at bar e (price ep). limit_fill: fill bar can only stop out. returns (grossR, exit_index)."""
    n = len(c); risk = ep - stop
    if limit_fill and l[e] <= stop: return -1.0, e
    start = e + 1 if limit_fill else e
    for j in range(start, min(n, e + HOLD)):
        if l[j] <= stop: return -1.0, j
        if h[j] >= tp: return (tp - ep) / risk, j
    j = min(n - 1, e + HOLD - 1)
    return (c[j] - ep) / risk, j

def vp(tp_, v, a, b, bins=30):
    lo = min(tp_[a:b + 1]); hi = max(tp_[a:b + 1])
    if hi <= lo: return None
    w = (hi - lo) / bins; H = [0.0] * bins
    for k in range(a, b + 1): H[min(bins - 1, int((tp_[k] - lo) / w))] += v[k]
    p = max(range(bins), key=lambda z: H[z]); tot = sum(H); cur = H[p]; lo_i = hi_i = p
    while cur < 0.7 * tot and (lo_i > 0 or hi_i < bins - 1):
        up = H[hi_i + 1] if hi_i < bins - 1 else -1; dn = H[lo_i - 1] if lo_i > 0 else -1
        if up >= dn: hi_i += 1; cur += up
        else: lo_i -= 1; cur += dn
    return dict(poc=lo + (p + 0.5) * w, val=lo + lo_i * w, vah=lo + (hi_i + 1) * w)

def worker(args):
    sym, inv, btc_tr = args
    t, o, h, l, c, v = load(sym, inv); n = len(t)
    if n < 2000: return sym, inv, [], None
    tr = [h[0] - l[0]] + [max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])) for i in range(1, n)]
    atr = ema(tr, 27)                                     # ~ Wilder 14
    ch = [0.0] + [c[i] - c[i - 1] for i in range(1, n)]
    up = ema([max(x, 0) for x in ch], 27); dn = ema([max(-x, 0) for x in ch], 27)
    rsi = [100 - 100 / (1 + a / max(b, 1e-12)) for a, b in zip(up, dn)]
    tr1d, _ = daily_trend(t, o, h, l, c)
    tp_ = [(h[i] + l[i] + c[i]) / 3 for i in range(n)]
    cv = [0.0]
    for x in v: cv.append(cv[-1] + x)
    vavg = lambda i, k: (cv[i] - cv[max(0, i - k)]) / max(1, i - max(0, i - k))
    SH = []; SL = []                                      # confirmed swings (bar, price)
    ev = []; last = {}
    def add(fam, i, kind, level, stop, targets, expiry=48, cancel_above=None, extra=None):
        if fam in last and i - last[fam] < 6: return
        last[fam] = i
        day = (t[i] // DAY) if (t[i] + HR) % DAY == 0 else (t[i] // DAY - 1)
        rec = dict(fam=fam, sym=sym, side="S" if inv else "L", t=t[i], i=i, kind=kind, tr1d=tr1d[i], btc=btc_tr.get(day, 0),
                   tr1h=1 if (len(SL) >= 2 and len(SH) >= 2 and SL[-1][1] > SL[-2][1] and SH[-1][1] > SH[-2][1]) else
                        (-1 if (len(SL) >= 2 and len(SH) >= 2 and SL[-1][1] < SL[-2][1] and SH[-1][1] < SH[-2][1]) else 0),
                   res={})
        if extra: rec.update(extra)
        # forward returns from next open (baseline, gross, price fraction, long orientation)
        if i + 73 < n:
            e0 = o[i + 1]; rec["fwd"] = {k: (c[i + k] - e0) / abs(e0) for k in (4, 24, 72)}
        # entry
        if kind == "mkt":
            e = i + 1
            if e >= n - 1: return
            ep = o[e]; filled = True
        else:
            e = None
            for w in range(1, expiry + 1):
                q = i + w
                if q >= n - 1: break
                if cancel_above is not None and h[q] > cancel_above: break
                if c[q] < stop: break
                if l[q] <= level - FILL_PEN * atr[i]: e = q; break
            if e is None:
                rec["res"] = None; ev.append(rec); return
            ep = level
        risk = ep - stop
        if risk <= 0 or risk / abs(ep) < 0.003: rec["res"] = None; ev.append(rec); return
        rp = risk / abs(ep)
        for nm, tpx in targets.items():
            if nm == "2R": tp = ep + 2 * risk
            elif nm == "3R": tp = ep + 3 * risk
            else:
                if tpx is None or (tpx - ep) < 1.0 * risk: continue
                tp = tpx
            R, x = sim(h, l, c, t, e, ep, stop, tp, kind == "lmt")
            rec["res"][nm] = (R, rp, (t[x] - t[e]) / HR + 1, t[e], t[x] + HR)
        ev.append(rec)

    for i in range(3, n - 2):
        # confirm swings (k=3) at bar i for pivot j=i-3
        j = i - 3
        if j >= 3:
            if h[j] > max(h[j - 3:j]) and h[j] >= max(h[j + 1:i + 1]): SH.append((j, h[j])); new_sh = True
            else: new_sh = False
            if l[j] < min(l[j - 3:j]) and l[j] <= min(l[j + 1:i + 1]): SL.append((j, l[j])); new_sl = True
            else: new_sl = False
        else:
            new_sh = new_sl = False
        if i < 300: continue
        a = atr[i]
        if a <= 0: continue
        # ---- TREND PULLBACK family: impulse L -> H just confirmed (fib levels, golden pocket, OTE, POC, AVWAP)
        if new_sh and SL:
            jh, H = SH[-1]; prev = next((s for s in reversed(SL) if s[0] < jh), None)
            if prev:
                jl, L = prev
                if H - L >= 4 * a and L <= min(l[jl:jh + 1]) and H >= max(h[jl:i + 1]):
                    tg = {"2R": None, "struct": H, "ext": H + 0.272 * (H - L)}
                    for f in FIBS:
                        lvl = H - f * (H - L)
                        if lvl < c[i]:
                            add(f"FIB{f}", i, "lmt", lvl, L - 0.1 * a, tg, cancel_above=H)
                    z = vp(tp_, v, jl, jh)
                    if z and L < z["poc"] < c[i]: add("POC_IMPULSE", i, "lmt", z["poc"], L - 0.1 * a, tg, cancel_above=H)
                    if z and L < z["val"] < c[i]: add("VAL_IMPULSE", i, "lmt", z["val"], L - 0.1 * a, tg, cancel_above=H)
                    av = sum(tp_[k] * v[k] for k in range(jl, i + 1)) / max(1e-12, sum(v[jl:i + 1]))
                    if L < av < c[i]: add("AVWAP_FROM_LOW", i, "lmt", av, L - 0.1 * a, tg, cancel_above=H)
        # ---- RANGE / ACCUMULATION family (range of the previous 48 bars)
        RH = max(h[i - 48:i]); RL = min(l[i - 48:i]); hgt = RH - RL
        if 2 * a <= hgt <= 8 * a:
            mid = (RL + RH) / 2
            if l[i] <= RL + 0.2 * hgt and c[i] > RL and c[i] < mid:
                add("RANGE_FADE_LOW", i, "mkt", None, RL - 0.3 * a, {"2R": None, "struct": RH - 0.1 * hgt})
            if c[i] > RH and v[i] >= 1.5 * vavg(i, 20):
                add("RANGE_BREAK_VOL", i, "mkt", None, mid, {"2R": None, "struct": RH + hgt})
            prior_drop = (c[i - 48] - max(h[i - 144:i - 48])) <= -6 * a
            vol_decl = vavg(i, 24) < (cv[i - 24] - cv[i - 48]) / 24
            if prior_drop and vol_decl and l[i] < RL - 0.1 * a and c[i] > RL:
                add("ACCUM_SPRING", i, "mkt", None, l[i] - 0.1 * a, {"2R": None, "struct": RH})
            if (not prior_drop) and l[i] < RL - 0.1 * a and c[i] > RL:
                add("RANGE_SWEEP_RECLAIM", i, "mkt", None, l[i] - 0.1 * a, {"2R": None, "struct": RH})
        # ---- REVERSAL: CHoCH after an extended decline; divergence
        hi96 = max(h[i - 96:i + 1]); jmax = i - 96 + h[i - 96:i + 1].index(hi96); lo_since = min(l[jmax:i + 1])
        if hi96 - lo_since >= 10 * a and SH:
            lh = SH[-1] if SH[-1][0] > jmax else None
            if lh and c[i] > lh[1] >= c[i - 1]:
                tgt = hi96 - 0.5 * (hi96 - lo_since)
                add("REVERSAL_CHOCH_MKT", i, "mkt", None, lo_since - 0.1 * a, {"2R": None, "struct": tgt})
                add("REVERSAL_CHOCH_RETEST", i, "lmt", lh[1], lo_since - 0.1 * a, {"2R": None, "struct": tgt}, expiry=24)
        if new_sl and len(SL) >= 2:
            (j1, p1), (j0, p0) = SL[-1], SL[-2]
            if 6 <= j1 - j0 <= 72 and p1 < p0 and rsi[j1] > rsi[j0] + 3:
                add("RSI_DIVERGENCE", i, "mkt", None, p1 - 0.1 * a, {"2R": None, "struct": max(h[j0:j1 + 1])})
        # ---- LIQUIDITY / STOP HUNT: equal lows swept and reclaimed
        rec_sl = []
        for s_ in reversed(SL):
            if i - s_[0] > 120: break
            rec_sl.append(s_[1])
        cand = [p for p in rec_sl if l[i] < p - 0.1 * a and c[i] > p]
        eq = None
        for p in cand:
            if sum(1 for q in rec_sl if abs(q - p) <= 0.25 * a) >= 2: eq = p; break
        if len(rec_sl) >= 2:
            if eq is not None and SH:
                add("STOPHUNT_EQUAL_LOWS", i, "mkt", None, l[i] - 0.1 * a, {"2R": None, "struct": SH[-1][1]})
        # ---- FAKEOUT: body close below the last swing low, back above within 3 bars
        if SL:
            lvl = SL[-1][1]
            for k in (1, 2, 3):
                if i - k > SL[-1][0] + 3 and c[i - k] < lvl and all(c[m] < lvl for m in range(i - k, i)) and c[i] > lvl:
                    add("FAKEOUT_BODY", i, "mkt", None, min(l[i - k:i + 1]) - 0.1 * a, {"2R": None, "struct": SH[-1][1] if SH else None})
                    break
        # ---- TREND STATE baseline samples (no trade logic beyond 2R market entry)
        if i % 24 == 0:
            add("STATE_SAMPLE", i, "mkt", None, c[i] - 1.5 * a, {"2R": None})
    arrays = dict(t=t, o=o, h=h, l=l, c=c, atr=atr, tr1d=tr1d)
    return sym, inv, ev, arrays

def btc_daily(inv):
    t, o, h, l, c, v = load("BTC", inv); _, tr = daily_trend(t, o, h, l, c); return tr

def run(coins):
    btcL = btc_daily(False); btcS = btc_daily(True)
    jobs = [(s, inv, btcS if inv else btcL) for s in coins for inv in (False, True)]
    with Pool(4) as p: out = p.map(worker, jobs)
    return out

if __name__ == "__main__":
    import pickle, time
    coins = sorted(f.split("_")[0] for f in os.listdir(D) if f.endswith("_1H.json") and len(json.load(open(f"{D}/{f}"))) > 15000)
    t0 = time.time(); out = run(coins)
    ev = [e for _, _, E, _ in out for e in E]; arr = {(s, inv): a for s, inv, _, a in out if a}
    pickle.dump(dict(ev=ev, coins=coins), open("scen_ev.pkl", "wb")); pickle.dump(arr, open("scen_arr.pkl", "wb"))
    import collections
    print("coins", len(coins), "events", len(ev), "secs", int(time.time() - t0))
    print(collections.Counter(e["fam"] for e in ev))
