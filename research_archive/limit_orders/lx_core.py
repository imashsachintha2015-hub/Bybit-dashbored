"""Limit-order execution model (Bybit USDT perpetuals, non-VIP): maker 2.0 bps per side, taker 5.5 bps + 1.5 bps slippage = 7 bps per
side for market entries, stop-market exits and time-outs. Market in + market out = 14 bps, the cost used in every earlier test.
Conservative fill rules:
  - a limit ENTRY fills only when price trades through it by FILL_PEN x ATR; the fill is checked BEFORE any cancel on that bar
    (the look-ahead fixed in amd_fvg.py on 2026-10-08); the fill bar can only stop us out (its high may have come first)
  - a limit TAKE-PROFIT fills only when price trades through it by TP_PEN x ATR (a touch is not enough: queue position)
  - stop and time-out exits are always taker
Gross R is the price move in R; fees are returned separately as a fraction of notional (net R = gross - fee/stop% - funding)."""
TAKER, MAKER = 7e-4, 2e-4
FILL_PEN, TP_PEN, HOLD = 0.05, 0.02, 96
LADDER = ((1.0, 1 / 3), (1.5, 1 / 3), (2.25, 1 / 3))

def levels_for(x):
    """'L' ladder, 'S1' 1.5R, 'S2' 2.25R, or a number = one target at that many R"""
    if isinstance(x, (int, float)): return ((float(x), 1.0),)
    return LADDER if x == "L" else ((1.5, 1.0),) if x == "S1" else ((2.25, 1.0),)

def sim_exec(h, l, c, e, ep, stop, risk, x, limit_entry, maker_tp, atr, hold=HOLD):
    """long orientation. e = entry bar (fill bar for limits). x: 'L' ladder (TP1/TP2/TP3 thirds, breakeven after TP1), 'S1' 1.5R, 'S2' 2.25R.
    returns (gross R, fee fraction of notional, exit index)"""
    n = len(c); lv = levels_for(x); pen = TP_PEN * atr if maker_tp else 0.0
    fee = MAKER if limit_entry else TAKER; tpfee = MAKER if maker_tp else TAKER
    R = 0.0; rem = 1.0; st = stop; hit = 0
    if limit_entry and l[e] <= st: return -1.0, fee + TAKER, e
    for j in range(e + 1 if limit_entry else e, min(n, e + hold)):
        if l[j] <= st: return R + rem * (st - ep) / risk, fee + rem * TAKER, j
        while hit < len(lv) and h[j] >= ep + lv[hit][0] * risk + pen:
            R += lv[hit][1] * lv[hit][0]; rem -= lv[hit][1]; fee += lv[hit][1] * tpfee; hit += 1
        if hit == len(lv): return R, fee, j
        if x == "L" and hit >= 1: st = max(st, ep)
    j = min(n - 1, e + hold - 1); return R + rem * (c[j] - ep) / risk, fee + rem * TAKER, j

def limit_fill(l, c, i, price, stop, atr, bars):
    """first bar after i where the resting limit fills (traded through by FILL_PEN*atr), checked before the cancel rule
    (a bar that did not fill and closed below the stop cancels the order). None = no fill."""
    n = len(c)
    for q in range(i + 1, min(n - 1, i + 1 + bars)):
        if l[q] <= price - FILL_PEN * atr: return q
        if c[q] < stop: return None
    return None

if __name__ == "__main__":
    # parity: market mode must reproduce resonance_test/res.py exactly (gross R and exit bar); maker TP never earlier than a touch
    import os, sys, random, numpy as np
    sys.path.insert(1, os.environ.get("REPO", "/home/user/Bybit-dashbored") + "/research_archive/resonance_test")
    import res
    rnd = random.Random(5); bad = 0; tot = 0
    for sym in ("BTC", "ETH", "SOL", "DOGE"):
        for inv in (False, True):
            t, o, h, l, c, v = res.load(sym, inv); F = res.features(t, o, h, l, c, v)
            for i in rnd.sample(range(6000, len(t) - 200), 300):
                e = i + 1; ep = o[e]; stp = min(F["stop_base"][i], ep - res.MINSTOP * abs(ep)); risk = ep - stp
                for x, k in (("L", None), ("S1", 1.5), ("S2", 2.25)):
                    ref = res.sim_ladder(h, l, c, e, ep, stp, risk) if x == "L" else res.sim_fixed(h, l, c, e, ep, stp, risk, k)
                    g, fee, j = sim_exec(h, l, c, e, ep, stp, risk, x, False, False, F["atr"][i])
                    tot += 1; bad += (abs(g - ref[0]) > 1e-12 or j != ref[1] or abs(fee - 14e-4) > 1e-12)
                    gm, fm, jm = sim_exec(h, l, c, e, ep, stp, risk, x, False, True, F["atr"][i])
                    bad += (jm < j and not (gm < g))  # maker TP can only fill later (or never), not earlier
    print(f"parity market mode vs res.py: {tot} trades, {bad} mismatches")
    # hand checks (entry 100, risk 1, atr 1 -> maker TP needs +0.02)
    H = np.array([100.5, 101.51, 100.2]); L = np.array([99.5, 100.6, 99.9]); C = (H + L) / 2
    print("S1 market, TP touched at 101.51 >= 101.5:", sim_exec(H, L, C, 0, 100.0, 99.0, 1.0, "S1", False, False, 1.0)[:2])
    print("S1 maker, 101.51 < 101.52 -> not filled, time-out mark:", sim_exec(H, L, C, 0, 100.0, 99.0, 1.0, "S1", False, True, 1.0, hold=3)[:2])
    print("limit entry, fill bar hits stop -> -1R, fee maker+taker:", sim_exec(np.array([100.2]), np.array([98.9]), np.array([99.0]), 0, 100.0, 99.0, 1.0, "S1", True, True, 1.0))
    print("limit_fill: bar1 low 99.96 (needs <= 99.95) no fill, bar2 low 99.9 fill ->", limit_fill(np.array([0, 99.96, 99.9]), np.array([0, 100.2, 100.1, 100]), 0, 100.0, 99.0, 1.0, 4))
