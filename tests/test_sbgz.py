"""Tests for backend_lib/sbgz.py (the dashboard's strong-break golden zone).  Run:  python tests/test_sbgz.py

  * invariants on random-walk candles: alternating trend pivots, trade prices in the right order, one trade
    per side at a time, R consistent with the fill / exit prices and the fees, JSON-safe output
  * a hand-built strong break that must give a LONG fill at the 50% retracement and a TP exit
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
        json.dumps(sbgz._clean(r), allow_nan=False)
    assert n_tr > 0, "random walks should produce at least a few strong-break trades"
    print(f"  invariants ok on 12 random walks ({n_tr} closed trades)")


def test_hand_built_long():
    # flat warm-up, a 3-VU dip and bounce (swing low), a big rally that breaks the prior swing high,
    # a 50% pullback (fill), then a rally through the target
    c = []; t0 = 1_700_000_000_000; price = 100.0

    def bar(o, h, l, cl):
        c.append(dict(start=t0 + len(c) * 900_000, open=o, high=h, low=l, close=cl, volume=1000.0))

    for i in range(300):                                   # quiet market: ~0.2 range per bar
        x = price + (0.1 if i % 2 else -0.1); bar(price, max(price, x) + 0.05, min(price, x) - 0.05, x); price = x
    for x in (100.6, 101.2, 101.6):                        # swing high ~101.7
        bar(price, x + 0.05, price - 0.05, x); price = x
    for x in (101.0, 100.4, 99.8, 99.4):                   # pullback of >3 VU -> swing high confirmed
        bar(price, price + 0.05, x - 0.05, x); price = x
    for x in (100.2, 101.0, 102.0, 103.5, 105.0, 106.5, 108.0):   # impulse that breaks 101.7 by closes, >13 VU
        bar(price, x + 0.05, price - 0.05, x); price = x
    for x in (107.0, 105.8, 104.6, 103.7, 103.6):          # retrace to ~50% of (99.35 .. 108.05)
        bar(price, price + 0.05, x - 0.05, x); price = x
    for x in (104.5, 106.0, 108.0, 110.0, 111.0, 111.5):   # rally through the -0.272 target (~110.4)
        bar(price, x + 0.05, price - 0.05, x); price = x
    r = sbgz.compute(c, "15")
    longs = [t for t in r["trades"] if t["side"] == "LONG"]
    assert longs, f"expected a LONG trade, got trades={r['trades']} setups={r['setups']}"
    t = longs[-1]
    assert t["result"] == "TP", t
    imp_lo, imp_hi = 99.35, 108.05
    assert abs(t["entry"] - (imp_hi - 0.5 * (imp_hi - imp_lo))) < 0.3, t
    print(f"  hand-built strong break: LONG @ {t['entry']:.2f}, TP {t['target']:.2f}, R {t['R']:+.2f}")


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
