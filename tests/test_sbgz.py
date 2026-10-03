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


def test_missed_setups_bookkeeping():
    n_missed = 0
    for seed in range(12):
        r = sbgz.compute(walk(4000, 300 + seed), "15")
        traded = {(t["side"], t["since_bar"]) for t in r["trades"] + r["open_trades"]}
        for m in r["missed"]:
            n_missed += 1
            L = m["side"] == "LONG"
            assert m["arm_bar"] <= m["seen_bar"] <= m["end_bar"] and m["why"] in sbgz.MISSED_WHY, m
            assert (m["stop"] < m["entry"] < m["target"]) if L else (m["target"] < m["entry"] < m["stop"]), m
            assert m["break_vu"] >= sbgz.P["min_break"] - 1e-9 and m["why"] != "vol", m   # the all-breaks run never needs volume
            assert (m["side"], m["arm_bar"]) not in traded, "a setup ends either as a trade or unfilled"
    assert n_missed > 0
    print(f"  {n_missed} strong setups that never filled, each ended once with a reason")


def test_history_store_and_updates():
    coins = [f"C{k}USDT" for k in range(12)]
    now = {"n": 1601}

    def fake_get(sym, interval="15", bars=1000, ttl=30):     # a sliding 1000-bar window over one long random walk
        c = walk(2601, sum(map(ord, sym + interval)))[:now["n"]][-(bars + 1):]
        r = sbgz.compute(c[:-1], interval)
        r.update(ok=True, symbol=sym, interval=interval, candles=c, times=[x["start"] // 1000 for x in c[:-1]])
        return sbgz._clean(r)

    store = {"rows": [], "saves": 0}

    def save(rows):
        store["rows"] = json.loads(json.dumps(rows)); store["saves"] += 1
        return True

    real = (sbgz.get, sbgz._hist_load, sbgz._hist_save, sbgz._hist_mem)
    try:
        sbgz.get, sbgz._hist_save = fake_get, save
        sbgz._hist_load = lambda: ([], False)                  # store unreadable: refuse, never overwrite it
        sbgz._hist_mem = None
        assert not sbgz.history(("15", "60"), coins, ttl=0)["ok"] and store["saves"] == 0
        sbgz._hist_load = lambda: (json.loads(json.dumps(store["rows"])), True)
        seen, updated, agreed = {}, 0, 0
        for n in (1601, 1801, 2001, 2201, 2401, 2601):          # 200 bars later each time, read back as a fresh process
            now["n"] = n; sbgz._hist_mem = None
            h = sbgz.history(("15", "60"), coins, ttl=0, limit=100_000)
            rows = {x["k"]: x for x in store["rows"]}
            assert h["ok"] and h["total"] == len(rows) and len(h["rows"]) == len(rows)
            assert set(seen) <= set(rows), "rows are never dropped"
            for k, old in seen.items():
                if old["status"] != "OPEN": assert rows[k] == old, "a closed / NO FILL row never changes"
                elif rows[k]["status"] != "OPEN": updated += 1
            for sym in coins:                                  # the same setup computed in this later window agrees
                for iv in ("15", "60"):
                    for x in sbgz._hist_rows(sym, iv, sbgz._brief(fake_get(sym, iv))):
                        old = rows.get(x["k"])
                        if old and old["status"] != "OPEN" and x["status"] != "OPEN":
                            assert (old["status"], old["entry"], old["R"]) == (x["status"], x["entry"], x["R"]), (old, x)
                            agreed += 1
            seen = rows
        st = sbgz._hist_stats(list(seen.values()))["all"]["total"]
        assert st["wins"] + st["losses"] + st["open"] + st["no_fill"] == st["signals"] == len(seen)
        assert all(x["status"] in ("WIN", "LOSS", "OPEN", "NO FILL") for x in seen.values())
        assert all((x["R"] > 0) == (x["status"] == "WIN") for x in seen.values() if x["status"] in ("WIN", "LOSS"))
        vo = sbgz.history(("15", "60"), coins, ttl=0, vonly=True, limit=100_000)
        assert vo["total"] == sum(1 for x in seen.values() if x["vol_ok"]) and all(x["vol_ok"] for x in vo["rows"])
        assert updated > 0 and agreed > 0
        json.dumps(vo, allow_nan=False)
    finally:
        sbgz.get, sbgz._hist_load, sbgz._hist_save, sbgz._hist_mem = real
    print(f"  radar history: {len(seen)} rows over 6 sliding windows ({st['wins']} WIN, {st['losses']} LOSS, {st['open']} OPEN, "
          f"{st['no_fill']} NO FILL); {updated} OPEN rows closed later; {agreed} rows recomputed identically")


def test_btc_exit_rule():
    """Close at the market when BTC's close has moved 0.5 risk-units against the trade (hand-built numbers, 15m candles)."""
    step = 900
    times = [i * step for i in range(10)]
    closes = [100, 100, 100.5, 101, 100, 99.8, 100.2, 100.4, 100.1, 100.3]         # the coin
    btc = {t: 1000.0 for t in times}
    for k, v in ((4, 1000.0), (5, 998.0), (6, 996.0), (7, 994.9), (8, 990.0), (9, 990.0)): btc[times[k]] = v
    row = dict(symbol="TESTUSDT", side="LONG", entry=100.0, stop=99.0, t_fill=times[2], t_end=None)     # risk 1% -> 0.5R = BTC -0.5%
    bx = sbgz.btc_exit(row, times, closes, btc, step)
    fee = sbgz.P["fee_limit"] + sbgz.P["fee_stop"]
    assert bx["fired"] and bx["t"] == times[7] + step and bx["px"] == 100.4, bx          # first close with BTC <= 995 vs the candle before the fill
    assert abs(bx["R"] - ((100.4 - 100.0) / 1.0 - fee * 100.0 / 1.0)) < 1e-3, bx
    # the candle that ends the trade itself, or any later one, is not eligible: the stop / target came first
    assert not sbgz.btc_exit(dict(row, t_end=times[7]), times, closes, btc, step)["fired"]
    assert sbgz.btc_exit(dict(row, t_end=times[8]), times, closes, btc, step)["fired"]
    assert not sbgz.btc_exit(dict(row, t_end=times[2]), times, closes, btc, step)["fired"], "stopped on its own fill candle"
    # BTC moving with the trade never fires; BTC itself and missing candles are handled
    up = {t: 1000.0 + 5 * i for i, t in enumerate(times)}
    assert not sbgz.btc_exit(row, times, closes, up, step)["fired"]
    assert sbgz.btc_exit(dict(row, symbol="BTCUSDT"), times, closes, btc, step) == dict(fired=False)
    assert sbgz.btc_exit(row, times, closes, {t: v for t, v in btc.items() if t != times[1]}, step) is None, "no BTC candle before the fill"
    assert sbgz.btc_exit(dict(row, t_fill=times[2] + 1), times, closes, btc, step) is None, "fill candle not in the list"
    # a SHORT mirrors it: BTC rising 0.5R fires, R is measured on the short side
    sh = dict(symbol="TESTUSDT", side="SHORT", entry=100.0, stop=101.0, t_fill=times[2], t_end=None)
    ub = {t: 1000.0 for t in times}
    for k in (5, 6, 7, 8, 9): ub[times[k]] = 1005.1
    b2 = sbgz.btc_exit(sh, times, closes, ub, step)
    assert b2["fired"] and b2["t"] == times[5] + step and b2["px"] == 99.8 and abs(b2["R"] - ((100.0 - 99.8) - fee * 100.0)) < 1e-3, b2
    assert not sbgz.btc_exit(sh, times, closes, btc, step)["fired"], "BTC falling is with a short"
    print("  BTC rule: first close 0.5R against, exit candle excluded, short mirrored, edge cases handled")


def test_history_btc_rule_and_backfill():
    step = 900
    coins = ["BTCUSDT"] + [f"C{k}USDT" for k in range(11)]
    now = {"n": 1601}

    def series(sym):
        return walk(2601, sum(map(ord, sym + "15")))

    def fake_get(sym, interval="15", bars=1000, ttl=30):
        c = series(sym)[:now["n"]][-(bars + 1):]
        r = sbgz.compute(c[:-1], interval)
        r.update(ok=True, symbol=sym, interval=interval, candles=c, times=[x["start"] // 1000 for x in c[:-1]])
        return sbgz._clean(r)

    def fake_fetch(sym, interval="15", bars=1000, timeout=6):
        return series(sym)[:now["n"]][-bars:]

    def expect(x):                                           # the rule again, with plain loops over the same candles
        if x["symbol"] == "BTCUSDT": return dict(fired=False)
        cl = {c["start"] // 1000: c["close"] for c in series(x["symbol"])[:now["n"] - 1]}
        bc = {c["start"] // 1000: c["close"] for c in series("BTCUSDT")[:now["n"] - 1]}
        sd = 1 if x["side"] == "LONG" else -1; E, S = x["entry"], x["stop"]; risk = abs(E - S)
        b0 = bc[x["t_fill"] - step]; t = x["t_fill"]
        while t in cl and (x["t_end"] is None or t < x["t_end"]):
            if sd * (bc[t] / b0 - 1) / (risk / abs(E)) <= -0.5:
                return dict(fired=True, t=t + step, R=sd * (cl[t] - E) / risk - 0.001 * abs(E) / risk)
            t += step
        return dict(fired=False)

    store = {"rows": [], "saves": 0}

    def save(rows):
        store["rows"] = json.loads(json.dumps(rows)); store["saves"] += 1
        return True

    real = (sbgz.get, sbgz._hist_load, sbgz._hist_save, sbgz._hist_mem, sbgz.fetch_candles)
    try:
        sbgz.get, sbgz._hist_save, sbgz.fetch_candles = fake_get, save, fake_fetch
        sbgz._hist_load = lambda: (json.loads(json.dumps(store["rows"])), True)
        seen = {}; later = 0
        for n in (1601, 1801, 2001, 2201, 2401, 2601):
            now["n"] = n; sbgz._hist_mem = None
            h = sbgz.history(("15",), coins, ttl=0, limit=100_000)
            rows = {x["k"]: x for x in store["rows"]}
            filled = {k: x for k, x in rows.items() if x["status"] != "NO FILL"}
            assert h["ok"] and all("bx" in x for x in filled.values()), "every filled row carries the BTC-rule result"
            assert all("bx" not in x for x in rows.values() if x["status"] == "NO FILL")
            for k, old in seen.items():                      # closed rows never change; an OPEN row may gain a bx, or close
                if old["status"] != "OPEN": assert rows[k] == old, "a closed row (with its bx) never changes"
                elif rows[k]["status"] == "OPEN" and rows[k]["bx"] != old.get("bx"): later += 1
            seen = rows
        fired = [x for x in seen.values() if x.get("bx", {}).get("fired")]
        calm = [x for x in seen.values() if x["status"] != "NO FILL" and not x["bx"]["fired"]]
        assert fired and calm, "random walks should show both outcomes"
        for x in seen.values():                              # against the plain-loop version on the final window
            if x["status"] == "NO FILL": continue
            e = expect(x); assert e["fired"] == x["bx"]["fired"], (x, e)
            if e["fired"]: assert x["bx"]["t"] == e["t"] and abs(x["bx"]["R"] - e["R"]) < 2e-3, (x, e)
        st = sbgz._hist_stats(list(seen.values()))["all"]["total"]; sx = sbgz._hist_stats_bx(list(seen.values()))["all"]["total"]
        assert sx["wins"] + sx["losses"] + sx["exits"] + sx["open"] + sx["no_fill"] == sx["signals"] == st["signals"] and sx["pending"] == 0
        assert sx["exits"] == len(fired) and sx["filled"] == st["filled"]
        want = sum((x["bx"]["R"] if x.get("bx", {}).get("fired") else x["R"]) for x in seen.values()
                   if x["status"] in ("WIN", "LOSS") or x.get("bx", {}).get("fired"))
        assert abs(sx["total_r"] - want) < 1e-6
        # stored rows without a result (from before the rule) are worked out in a longer download and come out the same
        store["rows"] = [{k: v for k, v in x.items() if k != "bx"} for x in store["rows"]]; sbgz._hist_mem = None
        h2 = sbgz.history(("15",), coins, ttl=0, limit=100_000)
        again = {x["k"]: x for x in store["rows"]}
        assert all(again[k].get("bx") == x.get("bx") for k, x in seen.items() if x["status"] != "NO FILL"), "backfill disagrees with the scan"
        assert h2["stats_bx"]["all"]["total"]["pending"] == 0
        # a download that gets no time leaves the rows for the next call
        assert sbgz._bx_backfill([x for x in seen.values() if x["status"] != "NO FILL"], budget=-1) == {}
        json.dumps(h2, allow_nan=False)
    finally:
        sbgz.get, sbgz._hist_load, sbgz._hist_save, sbgz._hist_mem, sbgz.fetch_candles = real
    print(f"  history + BTC rule: {len(seen)} rows, {len(fired)} closed by the rule, {later} OPEN rows changed their BTC result later; backfill identical")


def test_open_row_gains_btc_exit_then_closes():
    """An OPEN stored row: BTC calm -> bx not fired; BTC drops -> the row stays OPEN but now carries the exit; the trade later hits its stop ->
    status LOSS, the BTC exit (earlier) is kept. A trade that had already ended before BTC dropped never gets one."""
    step = 900; t0 = 1_700_000_000
    times = [t0 + step * i for i in range(130)]
    def brief(n, closes, **kw):
        return dict(price=closes[n - 1], times=times[:n], closes=closes[:n], panel={}, setups=[], missed=[], warmup=0, trades=kw.get("trades", []),
                    open_trades=kw.get("open", []))
    trade = dict(side="LONG", fill_bar=110, entry=100.0, stop=99.0, target=104.8, rr=4.8, break_vu=14.0, bvol=3.0, vol_ok=True, since_bar=105)
    coin = [100.2] * 130
    btc_calm = [1000.0] * 130
    btc_drop = [1000.0] * 119 + [994.0] * 11                  # 0.6% lower from candle 119; the trade's risk is 1% -> 0.5R = 0.5%
    store = {"rows": []}
    def save(rows): store["rows"] = json.loads(json.dumps(rows)); return True
    state = {}
    def fake_scan(intervals, coins, ttl):
        return 1, [(("BTCUSDT", "15"), brief(state["n"], state["btc"])), (("C0USDT", "15"), state["coin_brief"])]
    real = (sbgz._scan, sbgz._hist_load, sbgz._hist_save, sbgz._hist_mem)
    try:
        sbgz._scan, sbgz._hist_save = fake_scan, save
        sbgz._hist_load = lambda: (json.loads(json.dumps(store["rows"])), True)
        sbgz._hist_mem = None
        state.update(n=118, btc=btc_calm, coin_brief=brief(118, coin, open=[dict(trade)]))
        h = sbgz.history(("15",), ["BTCUSDT", "C0USDT"], ttl=0)
        row = store["rows"][0]
        assert len(store["rows"]) == 1 and row["status"] == "OPEN" and row["bx"] == dict(fired=False), row
        assert h["stats_bx"]["all"]["total"]["open"] == 1 and h["stats_bx"]["all"]["total"]["exits"] == 0
        state.update(n=126, btc=btc_drop, coin_brief=brief(126, coin, open=[dict(trade)]))     # BTC fell at candle 119; still no stop / target
        sbgz._hist_mem = None; h = sbgz.history(("15",), ["BTCUSDT", "C0USDT"], ttl=0)
        row = store["rows"][0]
        assert row["status"] == "OPEN" and row["bx"]["fired"] and row["bx"]["t"] == times[119] + step and row["bx"]["px"] == 100.2, row
        assert abs(row["bx"]["R"] - (0.2 - 0.001 * 100.0)) < 1e-3, row
        sx = h["stats_bx"]["all"]["total"]; sp = h["stats"]["all"]["total"]
        assert sx["open"] == 0 and sx["exits"] == 1 and sx["exits_up"] == 1 and abs(sx["total_r"] - row["bx"]["R"]) < 1e-9 and sp["open"] == 1 and sp["total_r"] == 0
        closed = dict(trade, exit_bar=127, result="SL", exit=99.0, R=-1.1)                    # the stop is hit at candle 127: later than the BTC exit
        state.update(n=130, btc=btc_drop, coin_brief=brief(130, coin, trades=[closed]))
        sbgz._hist_mem = None; sbgz.history(("15",), ["BTCUSDT", "C0USDT"], ttl=0)
        row = store["rows"][0]
        assert row["status"] == "LOSS" and row["bx"]["fired"] and row["bx"]["t"] == times[119] + step, row
        # a trade that ended at candle 118, before BTC dropped, is not touched by the rule
        store["rows"] = []; early = dict(trade, exit_bar=118, result="SL", exit=99.0, R=-1.1)
        state.update(n=130, btc=btc_drop, coin_brief=brief(130, coin, trades=[early]))
        sbgz._hist_mem = None; sbgz.history(("15",), ["BTCUSDT", "C0USDT"], ttl=0)
        assert store["rows"][0]["status"] == "LOSS" and store["rows"][0]["bx"] == dict(fired=False), store["rows"][0]
    finally:
        sbgz._scan, sbgz._hist_load, sbgz._hist_save, sbgz._hist_mem = real
    print("  open row: gains the BTC exit while still open, keeps it when the stop is hit later; an earlier exit is not overridden")


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
