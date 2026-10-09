"""Round 2: INVENTED situations and Fibonacci-like tools, same event format / costs / tags as scen.py.
New tools : VFIB (volume-Fibonacci), TFIB (time-Fibonacci), LFIB grid (data-learned depth+stop), VU ladder (SBGZ), harmonics.
New situations: CME weekend gap, Dalton 80% value-area rule, prev-day/week high-low sweeps & breakouts, weekend range on Monday,
round-number sweeps. Long logic; shorts = price inversion (scen.load)."""
import json, os, math, datetime
from multiprocessing import Pool
import scen
from scen import load, ema, daily_trend, vp, HR, DAY, FILL_PEN

HOLD = 96

def sim(h, l, c, t, e, ep, stop, tp, limit_fill, hold=HOLD):
    n = len(c); risk = ep - stop
    if limit_fill and l[e] <= stop: return -1.0, e
    start = e + 1 if limit_fill else e
    for j in range(start, min(n, e + hold)):
        if l[j] <= stop: return -1.0, j
        if h[j] >= tp: return (tp - ep) / risk, j
    j = min(n - 1, e + hold - 1)
    return (c[j] - ep) / risk, j

def us_dst(ts_ms):
    d = datetime.datetime.utcfromtimestamp(ts_ms / 1000); y = d.year
    mar = datetime.datetime(y, 3, 8); mar += datetime.timedelta(days=(6 - mar.weekday()) % 7)      # 2nd Sunday of March
    nov = datetime.datetime(y, 11, 1); nov += datetime.timedelta(days=(6 - nov.weekday()) % 7)     # 1st Sunday of November
    return mar <= d < nov

def round_step(p): return 10 ** (math.floor(math.log10(abs(p))) - 1)

def worker(args):
    sym, inv, btc_tr = args
    t, o, h, l, c, v = load(sym, inv); n = len(t)
    if n < 2000: return sym, inv, []
    tr = [h[0] - l[0]] + [max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])) for i in range(1, n)]
    atr = ema(tr, 27); vu = ema(tr, 100)
    tr1d, _ = daily_trend(t, o, h, l, c)
    tp_ = [(h[i] + l[i] + c[i]) / 3 for i in range(n)]
    cv = [0.0]
    for x in v: cv.append(cv[-1] + x)
    vavg = lambda i, k: (cv[i] - cv[max(0, i - k)]) / max(1, i - max(0, i - k))
    # ---- calendar aggregates: days and weeks (UTC), value areas of complete days
    day_of = [ts // DAY for ts in t]; first_bar = {}; dayH = {}; dayL = {}; day_bars = {}
    for i, d in enumerate(day_of):
        first_bar.setdefault(d, i); day_bars.setdefault(d, []).append(i)
        dayH[d] = max(dayH.get(d, -1e30), h[i]); dayL[d] = min(dayL.get(d, 1e30), l[i])
    dayVA = {}
    for d, bars in day_bars.items():
        if len(bars) >= 20:
            z = vp(tp_, v, bars[0], bars[-1], bins=24)
            if z: dayVA[d] = z
    wk = lambda d: (d - 4) // 7                       # weeks start Monday 00:00 UTC
    wkH = {}; wkL = {}
    for d in dayH:
        w = wk(d); wkH[w] = max(wkH.get(w, -1e30), dayH[d]); wkL[w] = min(wkL.get(w, 1e30), dayL[d])
    weekday = lambda d: (d + 3) % 7                    # Monday = 0
    SH = []; SL = []; Z = []                           # confirmed swings and an alternating zigzag
    ev = []; last = {}; pend = []
    done_day = {}

    def add(fam, i, kind, level, stop, targets, expiry=48, cancel_above=None, hold=HOLD, cool=6):
        if fam in last and i - last[fam] < cool: return
        last[fam] = i
        dd = (t[i] // DAY) if (t[i] + HR) % DAY == 0 else (t[i] // DAY - 1)
        rec = dict(fam=fam, sym=sym, side="S" if inv else "L", t=t[i], i=i, kind=kind, tr1d=tr1d[i], btc=btc_tr.get(dd, 0), tr1h=0, res={})
        if i + 73 < n:
            e0 = o[i + 1]; rec["fwd"] = {k: (c[i + k] - e0) / abs(e0) for k in (4, 24, 72)}
        if kind == "mkt":
            e = i + 1
            if e >= n - 1: return
            ep = o[e]
        else:
            e = None
            for w in range(1, expiry + 1):
                q = i + w
                if q >= n - 1: break
                if cancel_above is not None and h[q] > cancel_above: break
                if c[q] < stop: break
                if l[q] <= level - FILL_PEN * atr[i]: e = q; break
            if e is None: rec["res"] = None; ev.append(rec); return
            ep = level
        risk = ep - stop
        if risk <= 0 or risk / abs(ep) < 0.003: rec["res"] = None; ev.append(rec); return
        rp = risk / abs(ep)
        for nm, tpx in targets.items():
            if nm == "2R": tp = ep + 2 * risk
            else:
                if tpx is None or (tpx - ep) < 1.0 * risk: continue
                tp = tpx
            R, x = sim(h, l, c, t, e, ep, stop, tp, kind == "lmt", hold)
            rec["res"][nm] = (R, rp, (t[x] - t[e]) / HR + 1, t[e], t[x] + HR)
        ev.append(rec)

    def zz_add(typ, j, p):
        if Z and Z[-1][0] == typ:
            if (typ == "H" and p > Z[-1][2]) or (typ == "L" and p < Z[-1][2]): Z[-1] = (typ, j, p); return True
            return False
        Z.append((typ, j, p)); return True

    for i in range(3, n - 2):
        j = i - 3; new_sh = new_sl = False
        if j >= 3:
            if h[j] > max(h[j - 3:j]) and h[j] >= max(h[j + 1:i + 1]): SH.append((j, h[j])); new_sh = zz_add("H", j, h[j])
            if l[j] < min(l[j - 3:j]) and l[j] <= min(l[j + 1:i + 1]): SL.append((j, l[j])); new_sl = zz_add("L", j, l[j])
        if i < 300: continue
        a = atr[i]; u = vu[i]
        if a <= 0: continue
        d = day_of[i]
        # =================== NEW FIBONACCI-LIKE TOOLS on a just-confirmed impulse L -> H ===================
        if new_sh and SL:
            jh, H = SH[-1]; prev = next((s for s in reversed(SL) if s[0] < jh), None)
            if prev:
                jl, L = prev
                if H - L >= 4 * a and L <= min(l[jl:jh + 1]) and H >= max(h[jl:i + 1]):
                    tg = {"2R": None, "struct": H, "ext": H + 0.272 * (H - L)}
                    # (1) VOLUME-FIBONACCI: price below which (1-r) of the impulse's volume traded
                    pv = sorted((tp_[k], v[k]) for k in range(jl, jh + 1)); tot = sum(x[1] for x in pv)
                    if tot > 0:
                        for r in (0.382, 0.5, 0.618, 0.786):
                            acc = 0.0; lvl = None
                            for p_, w_ in pv:
                                acc += w_
                                if acc >= (1 - r) * tot: lvl = p_; break
                            if lvl is not None and L < lvl < c[i]:
                                add(f"VFIB{r}", i, "lmt", lvl, L - 0.1 * a, tg, cancel_above=H)
                    # (2) DATA-LEARNED retracement grid: entry depth x stop (below the low, or 0.15 impulse deeper)
                    for dpt in (0.30, 0.40, 0.45, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90):
                        lvl = H - dpt * (H - L)
                        if lvl < c[i]:
                            add(f"LFIB{dpt:.2f}_stopL", i, "lmt", lvl, L - 0.1 * a, tg, cancel_above=H)
                            if dpt + 0.15 < 1.0:
                                add(f"LFIB{dpt:.2f}_stopT", i, "lmt", lvl, H - (dpt + 0.15) * (H - L) - 0.1 * a, tg, cancel_above=H)
                    # (3) SBGZ volatility ladder (depth in VU = EMA-100 of true range): 4.0 / 4.7 / 5.7 VU, stop past 6.9 VU
                    for k in (4.0, 4.7, 5.7):
                        lvl = H - k * u
                        if lvl < c[i] and lvl > L - 3 * u:
                            add(f"VULADDER{k}", i, "lmt", lvl, H - 7.0 * u, tg, cancel_above=H)
                    # (4) TIME-FIBONACCI: register the impulse; triggers fire later when the pullback has lasted r x impulse time
                    D = jh - jl
                    if D >= 3: pend.append(dict(L=L, H=H, jl=jl, jh=jh, D=D, fired=set()))
        for pd in list(pend):
            if h[i] > pd["H"] or l[i] < pd["L"] or i - pd["jh"] > 3 * pd["D"] + 3: pend.remove(pd); continue
            for r in (0.382, 0.618, 1.0):
                if r not in pd["fired"] and i - pd["jh"] >= math.ceil(r * pd["D"]) and c[i] > o[i]:
                    pd["fired"].add(r)
                    add(f"TFIB{r}", i, "mkt", None, min(l[pd["jh"]:i + 1]) - 0.1 * a, {"2R": None, "struct": pd["H"]}, cool=1)
        # (5) HARMONICS on the zigzag when a high (C) is confirmed: Gartley, Bat (X-A-B-C-D) and AB=CD
        if new_sh and len(Z) >= 3 and Z[-1][0] == "H":
            if len(Z) >= 4:
                (tx, jx, X), (ta, ja, A), (tb, jb, B), (tc, jc, C) = Z[-4:]
                if tx == "L" and A > X and B > X and C < A and C > B:
                    XA = A - X; ab = (A - B) / XA; bc = (C - B) / (A - B)
                    for name, abr, dr, cdr in (("GARTLEY", (0.55, 0.68), 0.786, (1.1, 1.8)), ("BAT", (0.36, 0.53), 0.886, (1.5, 2.8))):
                        if abr[0] <= ab <= abr[1] and 0.382 <= bc <= 0.886:
                            Dp = A - dr * XA; cd = (C - Dp) / (C - B)
                            if cdr[0] <= cd <= cdr[1] and Dp < c[i]:
                                add(f"HARM_{name}", i, "lmt", Dp, X - 0.1 * a, {"2R": None, "struct": Dp + 0.618 * (C - Dp), "ext": C},
                                    expiry=72, cancel_above=C)
            (ta, ja, A), (tb, jb, B), (tc, jc, C) = Z[-3:]
            if ta == "H" and tb == "L" and A > B and C > B and C < A:
                bc = (C - B) / (A - B)
                if 0.618 <= bc <= 0.786:
                    for mult in (1.0, 1.272):
                        Dp = C - mult * (A - B)
                        if Dp < c[i]:
                            add(f"HARM_ABCD{mult}", i, "lmt", Dp, Dp - 0.3 * (A - B), {"2R": None, "struct": Dp + 0.618 * (C - Dp), "ext": C},
                                expiry=72, cancel_above=C)
        # =================== NEW SITUATIONS ===================
        # (6) CME WEEKEND GAP: Friday CME close -> Sunday CME open (DST aware); trade toward the fill at the Sunday open
        ts_next = t[i] + HR
        dt = datetime.datetime.utcfromtimestamp(ts_next / 1000)
        if dt.weekday() == 6 and dt.hour == (22 if us_dst(ts_next) else 23) and i + 1 < n:
            fri_close_ts = ts_next - 2 * DAY - HR                                  # Friday 21:00 UTC (DST) / 22:00 UTC (winter)
            k = i - 49                                                             # bar ending at Friday close = start fri_close_ts - 1h
            while k > 0 and t[k] > fri_close_ts - HR: k -= 1
            if t[k] == fri_close_ts - HR:
                cf = c[k]; ep = o[i + 1]; gap = (cf - ep) / abs(ep)                # >0: price below Friday close -> long toward the fill
                for g in (0.005, 0.01, 0.02):
                    if gap >= g:
                        for s in (0.5, 1.0, 2.0):
                            add(f"CMEGAP_fill_g{g}_s{s}", i, "mkt", None, ep - s * (cf - ep), {"struct": cf}, hold=120, cool=1)
        # (7) DALTON 80% RULE: day opened below yesterday's value area, then two consecutive hourly closes back inside -> toward VAH
        fb = first_bar.get(d)
        if fb is not None and d - 1 in dayVA and i - fb >= 1 and (t[i] // HR) % 24 <= 19 and done_day.get(("VA80", d)) is None:
            va = dayVA[d - 1]
            if o[fb] < va["val"] and va["val"] < c[i] < va["vah"] and va["val"] < c[i - 1] < va["vah"] and i - 1 >= fb:
                done_day[("VA80", d)] = 1
                add("VA80_REENTRY", i, "mkt", None, min(l[fb:i + 1]) - 0.1 * a, {"2R": None, "struct": va["vah"]}, cool=1)
        # (8) PREVIOUS DAY / WEEK HIGH-LOW: sweep + reclaim of the low, breakout of the high with volume
        if fb is not None and d - 1 in dayH:
            PDH, PDL = dayH[d - 1], dayL[d - 1]
            if done_day.get(("PDL", d)) is None and l[i] < PDL - 0.1 * a and c[i] > PDL:
                done_day[("PDL", d)] = 1; add("PDL_SWEEP_RECLAIM", i, "mkt", None, l[i] - 0.1 * a, {"2R": None, "struct": PDH}, cool=1)
            if done_day.get(("PDH", d)) is None and c[i] > PDH and c[i - 1] <= PDH and v[i] >= 1.5 * vavg(i, 20):
                done_day[("PDH", d)] = 1; add("PDH_BREAKOUT_VOL", i, "mkt", None, PDH - 1.0 * a, {"2R": None, "struct": PDH + (PDH - PDL)}, cool=1)
            w = wk(d)
            if w - 1 in wkH:
                PWH, PWL = wkH[w - 1], wkL[w - 1]
                if done_day.get(("PWL", w)) is None and l[i] < PWL - 0.1 * a and c[i] > PWL:
                    done_day[("PWL", w)] = 1; add("PWL_SWEEP_RECLAIM", i, "mkt", None, l[i] - 0.1 * a, {"2R": None, "struct": PWH}, cool=1)
                if done_day.get(("PWH", w)) is None and c[i] > PWH and c[i - 1] <= PWH:
                    done_day[("PWH", w)] = 1; add("PWH_BREAKOUT", i, "mkt", None, PWH - 1.5 * a, {"2R": None, "struct": PWH + (PWH - PWL)}, cool=1)
        # (9) WEEKEND RANGE on Monday: breakout continuation, and failed break (sweep of the weekend low, back inside)
        if weekday(d) == 0 and (d - 1) in dayH and (d - 2) in dayH:
            WH = max(dayH[d - 1], dayH[d - 2]); WL = min(dayL[d - 1], dayL[d - 2])
            if done_day.get(("WKB", d)) is None and c[i] > WH and c[i - 1] <= WH:
                done_day[("WKB", d)] = 1; add("WEEKEND_BREAKOUT", i, "mkt", None, (WH + WL) / 2, {"2R": None, "struct": WH + (WH - WL)}, cool=1)
            if done_day.get(("WKF", d)) is None and l[i] < WL - 0.1 * a and c[i] > WL:
                done_day[("WKF", d)] = 1; add("WEEKEND_FAILED_BREAK", i, "mkt", None, l[i] - 0.1 * a, {"2R": None, "struct": WH}, cool=1)
        # (10) ROUND-NUMBER sweep: low pierces a round level (step = 10^(digits-1)) and the hour closes back above it
        stp = round_step(c[i])
        if inv:   # inverted prices are negative: round levels are -k*step
            cand = [-(x * stp) for x in range(math.ceil(-c[i] / stp), math.floor(-l[i] / stp) + 1)]
        else:
            cand = [x * stp for x in range(math.ceil(l[i] / stp), math.floor(c[i] / stp) + 1)]
        for R_ in cand:
            if l[i] < R_ - 0.1 * a and c[i] > R_:
                add("ROUND_SWEEP_RECLAIM", i, "mkt", None, l[i] - 0.1 * a, {"2R": None, "struct": R_ + stp})
                break
    return sym, inv, ev

if __name__ == "__main__":
    import pickle, time, collections
    coins = sorted(f.split("_")[0] for f in os.listdir(scen.D) if f.endswith("_1H.json") and len(json.load(open(f"{scen.D}/{f}"))) > 15000)
    btcL = scen.btc_daily(False); btcS = scen.btc_daily(True)
    t0 = time.time()
    with Pool(4) as p: out = p.map(worker, [(s, inv, btcS if inv else btcL) for s in coins for inv in (False, True)])
    ev = [e for _, _, E in out for e in E]
    pickle.dump(dict(ev=ev, coins=coins), open("scen2_ev.pkl", "wb"))
    print("events", len(ev), "secs", int(time.time() - t0)); print(collections.Counter(e["fam"] for e in ev).most_common())
