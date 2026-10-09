"""Parity of the live module backend_lib/kalman_trend.py with the research code, on the research data
(OKX 4H 2020-2026, Binance funding). Checks z, ATR, BTC daily trend, the funding rule, the full trade list, and the
effect of computing everything from a limited warm-up window (what the live engine fetches)."""
import os, sys, json, math, bisect, collections
import numpy as np
REPO = "/home/user/Bybit-dashbored"
sys.path.insert(0, REPO)
import sit_core as C, tl_core as T, funding as FU
from backend_lib import kalman_trend as K

SYMS = sorted({x[1] for x in C.FINAL})
def fund_rows(sym):
    p = f"funding/{sym}.json"; rows = json.load(open(p)) if os.path.exists(p) else []
    return [int(r[0]) for r in rows], [float(r[1]) for r in rows]
F = {s: fund_rows(s) for s in SYMS}

# 1. indicators
zmax = amax = 0.0; cross_diff = 0; n_bars = 0
for s in SYMS:
    S = C.series(s, False); c = S["c"].tolist(); h = S["h"].tolist(); l = S["l"].tolist()
    z2, lvl = K.kalman(c); a2 = K.atr14(h, l, c)
    z1 = S["z"]; a1 = S["a"]
    ok = ~np.isnan(z1)
    zmax = max(zmax, float(np.nanmax(np.abs(np.array(z2)[ok] - z1[ok])))); amax = max(amax, float(np.max(np.abs(np.array(a2) - a1) / a1)))
    for i in range(1, len(c)):
        if K.crossing(z1[i - 1], z1[i]) != K.crossing(z2[i - 1], z2[i]): cross_diff += 1
    n_bars += len(c)
print(f"1. Kalman z: largest |difference| {zmax:.2e} over {n_bars} bars, crossing signals that differ: {cross_diff}; ATR largest relative difference {amax:.2e}")

B = C.series("BTC", False)
tr1 = T.daily_trend(B["t"], B["c"]); tr2 = K.daily_trend(B["t"].tolist(), B["c"].tolist())
print(f"2. BTC daily trend: {sum(1 for x, y in zip(tr1, tr2) if x != y)} of {len(tr1)} bars differ")
BTR = dict(zip(B["t"].tolist(), tr2))

# 3. funding rule: research = mean of the last 9 payments <= bar close; live = 72-hour window / 9
diff_val = diff_dec = n_f = 0
for te, sym, R, tx, t0, sd in C.FINAL:
    ft, fr = F[sym]
    if not ft: continue
    b = bisect.bisect_right(ft, t0 + K.H4)
    if b < 9: continue
    research = sum(fr[b - 9:b]) / 9; live = K.funding_avg(ft, fr, t0 + K.H4); n_f += 1
    if live is None or abs(live - research) > 1e-12: diff_val += 1
    d1 = (research > K.FUND_CAP_LONG) if sd == "L" else (research > 0)
    d2 = ((live or 0) > K.FUND_CAP_LONG) if sd == "L" else (live is not None and live > 0)
    diff_dec += d1 != d2
print(f"3. funding average at {n_f} system signals: value differs at {diff_val}, filter decision differs at {diff_dec}")

# 4. full trade list (research signal range i < n-2; research closes open trades at the end of the data)
mine = {}
for s in SYMS:
    S = C.series(s, False); t = S["t"].tolist(); o = S["o"].tolist(); h = S["h"].tolist(); l = S["l"].tolist(); c = S["c"].tolist()
    z, _ = K.kalman(c); a = K.atr14(h, l, c); ft, fr = F[s]
    def fav(tc, ft=ft, fr=fr):
        b = bisect.bisect_right(ft, tc)
        return (sum(fr[b - 9:b]) / 9) if b >= 9 else None               # research definition, to isolate the trade logic
    for tr in K.coin_trades(t, o, h, l, c, z, a, lambda ti: BTR.get(ti, 0), fav, last_signal_i=len(c) - 2):
        mine[(s, t[tr["signal_i"]], tr["side"])] = tr
ref = {(x[1], x[4], x[5]): x for x in C.FINAL}
both = set(mine) & set(ref); only_ref = set(ref) - set(mine); only_mine = set(mine) - set(ref)
same_exit = 0; rdiff = []; open_end = 0
for k in both:
    tr = mine[k]; te, sym, R, tx, t0, sd = ref[k]
    if tr["exit_t"] is None: open_end += 1; continue
    ft, fr = F[sym]
    if tr["entry_t"] == te and tr["exit_t"] == tx: same_exit += 1
    r_live = K.net_r(tr, ft, fr) if ft else None
    if r_live is not None and ft and te >= ft[0]: rdiff.append(abs(r_live - R))
print(f"4. trades: research {len(ref)}, live module {len(mine)}; in both {len(both)}, only research {len(only_ref)}, only live {len(only_mine)}")
print(f"   same entry and exit time: {same_exit} of {len(both) - open_end} closed ({open_end} still open at the end of the data, which the research closed at the last price)")
print(f"   net R with real funding: largest |difference| {max(rdiff):.2e} over {len(rdiff)} trades")
for k in sorted(only_ref)[:5]: print("   only research:", k)
for k in sorted(only_mine)[:5]: print("   only live:", k)

# 5. warm-up: z and BTC trend computed only from the last W bars, as the live engine does
for W in (1000, 1500):
    worst = 0.0; flips = 0; checks = 0
    for s in SYMS:
        S = C.series(s, False); c = S["c"].tolist(); z_full = S["z"]
        for end in range(max(W, 3000), len(c), 25):
            zw, _ = K.kalman(c[end - W:end + 1])
            worst = max(worst, abs(zw[-1] - z_full[end])); checks += 1
            flips += K.crossing(zw[-2], zw[-1]) != K.crossing(z_full[end - 1], z_full[end])
    print(f"5. warm-up {W} bars: at {checks} sampled bars, largest |z difference| at the last bar {worst:.4f}; crossing signal differs at {flips}")
bt = B["t"].tolist(); bc = B["c"].tolist(); dif = chk = 0
for end in range(3000, len(bc), 25):
    w = K.daily_trend(bt[end - 2000:end + 1], bc[end - 2000:end + 1]); chk += 1; dif += w[-1] != tr2[end]
print(f"   BTC daily trend from the last 2000 bars: differs at {dif} of {chk} sampled bars")
