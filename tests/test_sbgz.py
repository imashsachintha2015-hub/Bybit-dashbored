"""Tests for backend_lib/sbgz.py (the dashboard's strong-break golden zone).  Run:  python tests/test_sbgz.py

  * invariants on random-walk candles: alternating trend pivots, trade prices in the right order, one trade
    per side at a time, R consistent with the fill / exit prices and the fees, JSON-safe output
  * a hand-built strong break that must give a LONG fill at the 50% retracement and a TP exit; it only counts
    as volume-confirmed when the break candle has >= 2x the normal volume
  * parity with the research twin (scratch/sbgz_reference.py) on downloaded Binance history
    (skipped when scratch/ data is absent)
Nothing here talks to Bybit.
"""
import json
import math
import os
import random
import sys
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from backend_lib import sbgz  # noqa: E402


def walk(n, seed, vol=0.004, start=100.0):
    rnd = random.Random(seed); c = start; out = []
    for i in range(n):
        o = c; c = max(1e-6, c * math.exp(rnd.gauss(0, vol)))
        h = max(o, c) * (1 + abs(rnd.gauss(0, vol / 2))); l = min(o, c) * (1 - abs(rnd.gauss(0, vol / 2)))
        out.append(dict(start=1_700_000_000_000 + i * 900_000, open=o, high=h, low=l, close=c, volume=1000 + rnd.random() * 500))
    return out


def test_invariants():
    p = sbgz.P; n_tr = 0
    for seed in range(12):
        r = sbgz.compute(walk(4000, seed), "15")
        kinds = [x["kind"] for x in r["trend"]]
        assert all(a != b for a, b in zip(kinds, kinds[1:])), "trend pivots must alternate"
        kinds = [x["kind"] for x in r["swings"]]
        assert all(a != b for a, b in zip(kinds, kinds[1:])), "swing pivots must alternate"
        for side in ("LONG", "SHORT"):
            trs = sorted((t for t in r["trades"] + r["open_trades"] if t["side"] == side), key=lambda t: t["fill_bar"])
            for a, b in zip(trs, trs[1:]):
                assert b["fill_bar"] > a["exit_bar"], "one trade per side at a time"
        for t in r["trades"]:
            n_tr += 1
            L = t["side"] == "LONG"
            assert (t["stop"] < t["entry"] < t["target"]) if L else (t["target"] < t["entry"] < t["stop"])
            assert t["break_vu"] >= p["min_break"] - 1e-9
            assert t["rr"] > 0
            risk = abs(t["entry"] - t["stop"]); win = t["result"] == "TP"
            gross = (t["exit"] - t["entry"]) / risk * (1 if L else -1)
            fee = (p["fee_limit"] + (p["fee_limit"] if win else p["fee_stop"])) * t["entry"] / risk
            assert abs(t["R"] - (gross - fee)) < 1e-6, (t, gross, fee)
            assert (t["R"] > 0) == win or abs(t["R"]) < 1e-9
        for t in r["trades"] + r["open_trades"]:
            assert t["vol_ok"] == (t["bvol"] == t["bvol"] and t["bvol"] >= p["vol_mult"]), t
        assert all(t["vol_ok"] for t in r["trades_v"] + r["open_trades_v"]), "confirmed-only run took an unconfirmed break"
        json.dumps(sbgz._clean(r), allow_nan=False)
    assert n_tr > 0, "random walks should produce at least a few strong-break trades"
    print(f"  invariants ok on 12 random walks ({n_tr} closed trades)")


def _hand_built(break_vol):
    # flat warm-up, a 3-VU dip and bounce (swing low), a big rally that breaks the prior swing high,
    # a 50% pullback (fill), then a rally through the target
    c = []; t0 = 1_700_000_000_000; price = 100.0

    def bar(o, h, l, cl, vol=1000.0):
        c.append(dict(start=t0 + len(c) * 900_000, open=o, high=h, low=l, close=cl, volume=vol))

    for i in range(300):                                   # quiet market: ~0.2 range per bar
        x = price + (0.1 if i % 2 else -0.1); bar(price, max(price, x) + 0.05, min(price, x) - 0.05, x); price = x
    for x in (100.6, 101.2, 101.6):                        # swing high ~101.7
        bar(price, x + 0.05, price - 0.05, x); price = x
    for x in (101.0, 100.4, 99.8, 99.4):                   # pullback of >3 VU -> swing high confirmed
        bar(price, price + 0.05, x - 0.05, x); price = x
    for x in (100.2, 101.0, 102.0, 103.5, 105.0, 106.5, 108.0):   # impulse that breaks 101.7 by closes, >13 VU
        bar(price, x + 0.05, price - 0.05, x, break_vol if x == 102.0 else 1000.0); price = x   # 102.0 = break candle
    for x in (107.0, 105.8, 104.6, 103.7, 103.6):          # retrace to ~50% of (99.35 .. 108.05)
        bar(price, price + 0.05, x - 0.05, x); price = x
    for x in (104.5, 106.0, 108.0, 110.0, 111.0, 111.5):   # rally through the -0.272 target (~110.4)
        bar(price, x + 0.05, price - 0.05, x); price = x
    return sbgz.compute(c, "15")


def test_hand_built_long():
    r = _hand_built(1000.0)
    longs = [t for t in r["trades"] if t["side"] == "LONG"]
    assert longs, f"expected a LONG trade, got trades={r['trades']} setups={r['setups']}"
    t = longs[-1]
    assert t["result"] == "TP", t
    imp_lo, imp_hi = 99.35, 108.05
    assert abs(t["entry"] - (imp_hi - 0.5 * (imp_hi - imp_lo))) < 0.3, t
    assert not t["vol_ok"] and not [x for x in r["trades_v"] if x["side"] == "LONG"], "flat volume must not confirm"
    rv = _hand_built(5000.0)                               # same path, break candle at 5x volume
    tv = [x for x in rv["trades_v"] if x["side"] == "LONG"]
    assert tv and tv[-1]["vol_ok"] and abs(tv[-1]["bvol"] - 5.0) < 0.2 and tv[-1]["result"] == "TP", tv
    print(f"  hand-built strong break: LONG @ {t['entry']:.2f}, TP {t['target']:.2f}, R {t['R']:+.2f}; "
          f"volume-confirmed only with a {tv[-1]['bvol']:.1f}x break candle")


def test_pullback_map_ladder():
    seen = 0
    for seed in range(12):
        c = walk(3000, 100 + seed)
        for cut in range(1200, 3000, 150):                     # the last bar lands in many different states
            pn = sbgz.compute(c[:cut], "15")["panel"]; pm = pn.get("pmap")
            if not pm or not pm.get("ladder"): continue
            seen += 1
            d = 1 if pn["trend"]["dir"] == "UP" else -1
            prices = [lv["price"] for lv in pm["ladder"]]; odds = [lv["cont_pct"] for lv in pm["ladder"]]
            assert all(d * (a - b) > 0 for a, b in zip(prices, prices[1:])), "ladder must step away from the top"
            assert all(d * (pm["top"] - x) > 0 for x in prices), "ladder lies on the pullback side of the top"
            assert all(d * (x - pm["trend_over"]) > 0 for x in prices), "no level beyond the line where the trend flips"
            assert pm["push_start"] is None or d * (pm["push_start"] - pm["trend_over"]) > 0
            assert all(a >= b for a, b in zip(odds, odds[1:])) and all(0 < x < 100 for x in odds), odds
            pb = pn["pullback"]
            assert 0 < pb["continue_pct"] < 100 and pb["depth_vu"] >= 0
    assert seen > 5, "random walks should often end inside a pullback"
    print(f"  pullback map ladder consistent in {seen} pullback states")


def test_radar_rows_without_network():
    real_get = sbgz.get
    def fake_get(sym, interval="15", bars=1000, ttl=30):
        c = walk(1600, sum(map(ord, sym + interval)))
        r = sbgz.compute(c[:-1], interval)
        r.update(ok=True, symbol=sym, interval=interval, candles=c, times=[x["start"] // 1000 for x in c[:-1]])
        return sbgz._clean(r)
    try:
        sbgz.get = fake_get
        res = sbgz.radar(("15", "60"), [f"C{k}USDT" for k in range(24)], ttl=0)
    finally:
        sbgz.get = real_get
    assert res["ok"] and res["scanned"] == 48 and not res["errors"]
    for r in res["rows"]:
        assert r["kind"] in ("setup", "open") and r["side"] in ("LONG", "SHORT") and r["exp_r"] is not None
        if r["kind"] == "setup":                              # the forming candle may already be through the entry
            assert r["dist_pct"] is not None and r["dist_vu"] is not None and r["vol_ok"] in (True, False)
    json.dumps(res, allow_nan=False)
    print(f"  radar: {len(res['rows'])} rows from 48 fake charts, all well-formed")


def test_parity_with_research_twin():
    scratch = os.path.join(ROOT, "scratch")
    if not os.path.exists(os.path.join(scratch, "binance_15m", "BTCUSDT.npz")):
        print("  parity: skipped (no scratch/ data)")
        return
    sys.path.insert(0, scratch)
    import sbgz_reference as REF  # type: ignore  # lives in scratch/ (added to sys.path above)
    for sym in ("BTCUSDT", "SOLUSDT", "AAVEUSDT"):
        d, _, ref = REF.run(sym)
        cand = [dict(start=int(d["t"][i]), open=d["o"][i], high=d["h"][i], low=d["l"][i], close=d["c"][i], volume=d["v"][i])
                for i in range(d["n"])]
        mine = {(1 if t["side"] == "LONG" else -1, int(d["t"][t["fill_bar"]])): t["R"] for t in sbgz.compute(cand, "15")["trades"]}
        theirs = {(sd, t): R for sd, t, R, _ in ref}
        assert mine.keys() == theirs.keys(), f"{sym}: different trades"
        assert all(abs(mine[k] - theirs[k]) < 1e-9 for k in mine), f"{sym}: different R"
        print(f"  parity with the research twin on {sym}: {len(mine)} identical trades")
    pkl = os.path.join(scratch, "anatomy_results.pkl")
    if os.path.exists(pkl):                                # break-candle volume vs the research rows
        import pickle
        B = pickle.load(open(pkl, "rb"))["bf"]
        if "bvol" in B:
            B = B[(B.tf == "15m") & (B.f == .5) & (B.s == .66) & (B.x == .272) & (B.sym == "SOLUSDT")].set_index(["side", "t"]).bvol.to_dict()
            d, _, _ = REF.run("SOLUSDT")
            cand = [dict(start=int(d["t"][i]), open=d["o"][i], high=d["h"][i], low=d["l"][i], close=d["c"][i], volume=d["v"][i]) for i in range(d["n"])]
            got = [((1 if t["side"] == "LONG" else -1, int(d["t"][t["fill_bar"]])), t["bvol"]) for t in sbgz.compute(cand, "15")["trades"]]
            both = [(k, v) for k, v in got if k in B]
            assert both and all(abs(v - B[k]) < 1e-9 for k, v in both), "break volume differs from the research"
            print(f"  break-candle volume identical to the research on {len(both)} SOLUSDT trades")


if __name__ == "__main__":
    fails = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
            except Exception:
                fails += 1
                print(f"FAIL {name}"); traceback.print_exc()
    print("all sbgz tests passed" if not fails else f"{fails} sbgz test(s) failed")
    sys.exit(1 if fails else 0)
