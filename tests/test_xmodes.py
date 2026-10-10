"""Tests for backend_lib/xmodes.py (CME-X5 and Championship chart indicators).  Run:  python tests/test_xmodes.py

  * simulate(): hand-built paths for every exit (target, hard stop, break-even lock, +0.5R lock, timeout, gap through the
    stop, a candle touching both stop and target, still open, wrong-sided levels)
  * simulate() against research_archive/historical_replay/replay.py `sim` on random paths and signals: same exit kind,
    candle and R (skipped when that folder is absent)
  * the windows: the raw signals of both runs are exactly the engine's own functions called on the last 100 / 250 candles
  * higher-timeframe candles as the engine sees them (finished ones plus the one still forming)
  * the agent layer: a 0.0 score rejects, adjusted targets are applied, a failing agent counts as a neutral 0.7
  * get() end to end on fake candles: JSON-safe, every status valid, stats equal a recount, LIVE only on the newest candle
Nothing here talks to Bybit.
"""
import importlib.util
import json
import math
import os
import random
import sys
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from backend_lib import xmodes as XM  # noqa: E402

T0 = 1_700_000_000_000
STEP = 900_000


def bar(i, o, h, l, c, v=1000.0):
    return dict(start=T0 + i * STEP, open=o, high=h, low=l, close=c, volume=v)


def flat(n, p=100.0):
    return [bar(i, p, p + 0.05, p - 0.05, p) for i in range(n)]


def path(prices, base=100.0):
    """A signal candle at 0 (close = base) followed by candles that open at the previous close and travel to `prices`."""
    out = [bar(0, base, base + .05, base - .05, base)]; prev = base
    for k, (hi, lo, cl) in enumerate(prices, 1):
        out.append(bar(k, prev, hi, lo, cl)); prev = cl
    return out


def test_simulate_scenarios():
    # long: entry 100, stop 99 (risk 1%), target 103; fr = 0.0014 / 0.01 = 0.14R
    fr = 0.14
    r = XM.simulate(path([(100.5, 99.9, 100.4), (103.2, 100.3, 103.0)]), 0, "LONG", 99.0, 103.0)
    assert r["kind"] == "TP" and r["exit_i"] == 2 and abs(r["R"] - (3.0 - fr)) < 1e-9, r
    r = XM.simulate(path([(100.2, 98.9, 99.2)]), 0, "LONG", 99.0, 103.0)
    assert r["kind"] == "SL" and abs(r["R"] - (-1.0 - fr)) < 1e-9, r
    r = XM.simulate(path([(100.6, 99.9, 100.4)] + [(100.5, 99.9, 100.4)] * 3), 0, "LONG", 99.0, 103.0)
    assert r["kind"] == "OPEN" and abs(r["R_now"] - 0.4) < 1e-9, r
    # +0.8R reached (>= 0.70R) -> stop to 100.10; next candle dips to 100.05 -> stopped at break-even +0.10%
    r = XM.simulate(path([(100.8, 100.2, 100.6), (100.7, 100.05, 100.3)]), 0, "LONG", 99.0, 103.0)
    assert r["kind"] == "BE" and abs(r["exit"] - 100.10) < 1e-9 and abs(r["R"] - (0.10 - fr)) < 1e-9, r
    # +1.3R reached -> stop to 100.5 (+0.5R); next candle falls to 100.4 -> trail stop
    r = XM.simulate(path([(101.3, 100.6, 101.0), (101.0, 100.4, 100.45)]), 0, "LONG", 99.0, 103.0)
    assert r["kind"] == "TRAIL" and abs(r["exit"] - 100.5) < 1e-9 and abs(r["R"] - (0.5 - fr)) < 1e-9, r   # filled at the +0.5R stop
    # a candle that touches both the stop and the target counts as a stop
    r = XM.simulate(path([(103.5, 98.5, 101.0)]), 0, "LONG", 99.0, 103.0)
    assert r["kind"] == "SL", r
    # gap through the stop: the next candle opens below it -> filled at the open
    c = path([(100.2, 100.0, 100.0)]); c.append(bar(2, 98.0, 98.5, 97.5, 98.0))
    r = XM.simulate(c, 0, "LONG", 99.0, 103.0)
    assert r["kind"] == "SL" and r["exit"] == 98.0 and r["R"] < -1.0 - fr, r
    # 48 candles without an exit -> timeout at the close of the 48th
    c = path([(100.4, 99.8, 100.2)] * 48)
    r = XM.simulate(c, 0, "LONG", 99.0, 103.0)
    assert r["kind"] == "TIMEOUT" and r["exit_i"] == 48 and r["bars"] == 48 and abs(r["R"] - (0.2 - fr)) < 1e-9, r
    assert XM.simulate(path([(100.4, 99.8, 100.2)] * 47), 0, "LONG", 99.0, 103.0)["kind"] == "OPEN"
    # short mirror: entry 100, stop 101, target 97
    r = XM.simulate(path([(100.1, 96.9, 97.5)]), 0, "SHORT", 101.0, 97.0)
    assert r["kind"] == "TP" and abs(r["R"] - (3.0 - fr)) < 1e-9, r
    r = XM.simulate(path([(101.1, 99.9, 100.5)]), 0, "SHORT", 101.0, 97.0)
    assert r["kind"] == "SL" and abs(r["R"] - (-1.0 - fr)) < 1e-9, r
    # wrong-sided levels
    assert XM.simulate(path([(101, 99, 100)]), 0, "LONG", 101.0, 103.0)["kind"] == "INVALID"
    assert XM.simulate(path([(101, 99, 100)]), 0, "LONG", 99.0, 99.5)["kind"] == "INVALID"
    print("  simulate: target / stop / break-even / +0.5R lock / timeout / gap / tie / open / invalid all as the executor does")


def _load_replay():
    p = os.path.join(ROOT, "research_archive", "historical_replay", "replay.py")
    if not os.path.exists(p): return None
    cwd = os.getcwd()
    try:
        os.chdir(os.path.dirname(p))
        spec = importlib.util.spec_from_file_location("replay_harness", p)
        m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
        return m
    finally:
        os.chdir(cwd)


def walk(n, seed, vol=0.0035, start=100.0, spikes=True):
    rnd = random.Random(seed); c = start; out = []
    for i in range(n):
        o = c; c = max(1e-6, c * math.exp(rnd.gauss(0, vol)))
        h = max(o, c) * (1 + abs(rnd.gauss(0, vol / 2))); l = min(o, c) * (1 - abs(rnd.gauss(0, vol / 2)))
        v = 1000 * math.exp(rnd.gauss(0, 0.45)) * (3.0 if spikes and rnd.random() < 0.06 else 1.0)
        out.append(dict(start=T0 + i * STEP, open=o, high=h, low=l, close=c, volume=v))
    return out


def test_simulate_matches_replay_harness():
    H = _load_replay()
    if H is None:
        print("  replay-harness parity: skipped (research_archive/historical_replay not present)"); return
    rnd = random.Random(7); n_cmp = 0; kinds = {}
    for seed in range(40):
        cs = walk(400, 1000 + seed, vol=rnd.choice((0.002, 0.004, 0.008)))
        for _ in range(25):
            i = rnd.randrange(100, 340); d = rnd.choice(("LONG", "SHORT")); ep = cs[i]["close"]
            risk = ep * rnd.choice((0.003, 0.006, 0.012, 0.02)); k = rnd.choice((1.0, 1.5, 2.0, 3.0))
            stop = ep - risk if d == "LONG" else ep + risk; tp = ep + k * risk if d == "LONG" else ep - k * risk
            mine = XM.simulate(cs, i, d, stop, tp)
            theirs = H.sim(cs, i, dict(direction=d, stop_p=stop, target_p=tp, risk_pct=risk / ep))
            assert mine["kind"] != "INVALID" and theirs is not None
            kind = {"STOP": "SL"}.get(theirs["kind"], theirs["kind"])
            assert mine["kind"] == kind and mine["exit_i"] == theirs["exit_i"], (seed, i, d, mine, theirs)
            assert abs(mine["R"] - theirs["R"]) < 1e-9, (mine, theirs)
            n_cmp += 1; kinds[kind] = kinds.get(kind, 0) + 1
    assert len(kinds) >= 4, kinds
    print(f"  simulate == replay.sim on {n_cmp} random trades (exits {kinds})")


def test_higher_timeframe_candles():
    cs = walk(900, 3)
    for ms, n in ((XM.H1_MS, 60), (XM.H4_MS, 50)):
        bk, ix = XM._buckets(cs, ms)
        for i in (300, 457, 458, 459, 460, 899):
            got = XM._htf(cs, bk, ix, i, n)
            assert len(got) == min(n, ix[i] + 1)
            assert [g["start"] for g in got] == sorted(g["start"] for g in got)
            last = got[-1]; seg = [c for c in cs[:i + 1] if c["start"] // ms * ms == last["start"]]   # the forming candle, through i only
            assert last["open"] == seg[0]["open"] and last["close"] == cs[i]["close"]
            assert last["high"] == max(c["high"] for c in seg) and last["low"] == min(c["low"] for c in seg)
            assert abs(last["volume"] - sum(c["volume"] for c in seg)) < 1e-6
            if len(got) > 1:                                    # a finished one holds every 15m candle of its bucket
                prev = got[-2]; seg = [c for c in cs if c["start"] // ms * ms == prev["start"]]
                assert prev["high"] == max(c["high"] for c in seg) and prev["close"] == seg[-1]["close"]
    print("  1h / 4h candles: finished ones complete, the forming one aggregated only through the signal candle")


class _Fake:
    def __init__(self, r): self.r = r

    def analyze(self, *a, **k):
        if isinstance(self.r, Exception): raise self.r
        return self.r


def test_agent_layer():
    E = XM._engine(); real = (E["sr"], E["poc"], E["fvg"])
    sig = dict(direction="LONG", entry_p=100.0, stop_p=99.0, target_p=103.0)
    cs = walk(200, 5)
    try:
        ok = {"score": 0.9, "adjustment": None, "reason": "ok"}
        E["sr"], E["poc"], E["fvg"] = _Fake(ok), _Fake(ok), _Fake(ok)
        v, ag = XM._verdict(E, "BTCUSDT", sig, cs[-100:], [], [])
        assert v["decision"] == "PASS" and [a["name"] for a in ag] == ["S/R", "POC", "FVG"]
        E["sr"] = _Fake({"score": 0.0, "adjustment": None, "reason": "fatal"})              # one fatal agent vetoes the lot
        assert XM._verdict(E, "BTCUSDT", sig, cs[-100:], [], [])[0]["decision"] == "REJECT"
        E["sr"] = _Fake({"score": 0.6, "adjustment": {"target_p": 101.5}, "reason": "tp"})
        v, _ = XM._verdict(E, "BTCUSDT", sig, cs[-100:], [], [])
        assert v["decision"] == "PASS" and v["adjustments"] == {"target_p": 101.5}
        E["sr"] = _Fake(RuntimeError("boom"))                                               # failing agent = neutral 0.7
        v, ag = XM._verdict(E, "BTCUSDT", sig, cs[-100:], [], [])
        assert v["decision"] == "PASS" and ag[0]["score"] == 0.7 and "error" in ag[0]["reason"]
    finally:
        E["sr"], E["poc"], E["fvg"] = real
    print("  agent layer: veto on a 0.0 score, adjusted target carried, an agent error counts as 0.7")


def test_windows_are_the_engines_own():
    """With every trade ending on its own candle (so none blocks a later signal) and every setup passed by the agents, the
    signals of a run are exactly what the engine functions return on the last 100 / 250 candles."""
    E = XM._engine(); X = E["X"]; eng = E["champ"]
    real = (XM._verdict, XM.simulate)
    XM._verdict = lambda *a, **k: (dict(decision="PASS", final_score=0.9, adjustments={}), [])
    XM.simulate = lambda bars, i, side, stop, tp, fric=XM.FRIC: dict(kind="TP", exit_i=i, exit=tp, R=1.0, bars=1)
    try:
        n5 = n_ch = 0
        for seed in range(6):
            cs = walk(700, 40 + seed, vol=0.004)
            n_closed = len(cs) - 1                      # the last candle plays the forming one
            sigs, skipped = XM._run_cmex5(E, "SOLUSDT", cs, 150, n_closed)
            want = []
            for i in range(150, len(cs)):
                w = cs[i - 99:i + 1]
                s = X.detect_s2("SOLUSDT", w, None) or X.detect_s7("SOLUSDT", w, None)
                if s: want.append((cs[i]["start"] // 1000, s["direction"], s["entry_p"], s["stop_p"], s["target_p"]))
            got = [(g["t"], g["side"], g["entry"], g["stop"], g["target"]) for g in sigs]
            assert got == want and skipped == 0, (seed, len(got), len(want))
            n5 += len(got)
            btc = walk(700, 90 + seed, vol=0.004)
            sig2, skipped2 = XM._run_champ(E, "SOLUSDT", cs, btc, 260, n_closed)
            want2 = []
            for i in range(260, len(cs)):
                c = eng.scan_candidate("SOLUSDT", cs[i - 249:i + 1], btc[i - 249:i + 1])
                if c: want2.append((cs[i]["start"] // 1000, c["direction"], c["entry_price"], c["stop_loss"], c["tp2"]))
            got2 = [(g["t"], g["side"], g["entry"], g["stop"], g["target"]) for g in sig2]
            assert got2 == want2 and skipped2 == 0, (seed, got2, want2)
            n_ch += len(got2)
        assert n5 > 10, n5
        print(f"  windows: {n5} CME-X5 and {n_ch} Championship signals are exactly the engine functions' output on the last 100 / 250 candles")
    finally:
        XM._verdict, XM.simulate = real


def test_windows_on_real_history():
    """The same exact-match check on real candles (Binance 15m in scratch/, when present), where Championship fires often."""
    d = os.path.join(ROOT, "scratch", "binance_15m")
    if not os.path.exists(os.path.join(d, "BTCUSDT.npz")):
        print("  windows on real history: skipped (no scratch/binance_15m)"); return
    import numpy as np

    def load(sym, a, b):
        z = np.load(os.path.join(d, sym + ".npz")); t, o, h, l, c, v = (z[k][a:b] for k in ("t", "o", "h", "l", "c", "v"))
        return [dict(start=int(t[i]), open=float(o[i]), high=float(h[i]), low=float(l[i]), close=float(c[i]), volume=float(v[i])) for i in range(len(t))]

    E = XM._engine(); X = E["X"]; eng = E["champ"]
    real = (XM._verdict, XM.simulate)
    XM._verdict = lambda *a, **k: (dict(decision="PASS", final_score=0.9, adjustments={}), [])
    XM.simulate = lambda bars, i, side, stop, tp, fric=XM.FRIC: dict(kind="TP", exit_i=i, exit=tp, R=1.0, bars=1)
    try:
        n5 = n_ch = 0; sides = set(); kinds = set()
        for sym in ("ETHUSDT", "SOLUSDT", "AAVEUSDT"):
            a, b = 60000, 63500                                   # ~36 days in 2024
            cs, btc = load(sym, a, b), load("BTCUSDT", a, b)
            assert cs[0]["start"] == btc[0]["start"] and len(cs) == len(btc)
            n_closed = len(cs) - 1
            sigs, _ = XM._run_cmex5(E, sym, cs, 150, n_closed)
            want = []
            for i in range(150, len(cs)):
                w = cs[i - 99:i + 1]
                s = X.detect_s2(sym, w, None) or X.detect_s7(sym, w, None)
                if s: want.append((cs[i]["start"] // 1000, s["direction"], s["entry_p"], s["stop_p"], s["target_p"]))
            assert [(g["t"], g["side"], g["entry"], g["stop"], g["target"]) for g in sigs] == want, sym
            n5 += len(want)
            sig2, _ = XM._run_champ(E, sym, cs, btc, 260, n_closed)
            want2 = []
            for i in range(260, len(cs)):
                c = eng.scan_candidate(sym, cs[i - 249:i + 1], btc[i - 249:i + 1])
                if c: want2.append((cs[i]["start"] // 1000, c["direction"], c["entry_price"], c["stop_loss"], c["tp2"]))
            assert [(g["t"], g["side"], g["entry"], g["stop"], g["target"]) for g in sig2] == want2, sym
            n_ch += len(want2); sides |= {g["side"] for g in sig2}; kinds |= {g["kind"] for g in sig2}
        assert n5 > 100 and n_ch >= 8, (n5, n_ch)
        print(f"  windows on real candles: {n5} CME-X5 and {n_ch} Championship signals ({sorted(kinds)}) match the engine functions exactly")
    finally:
        XM._verdict, XM.simulate = real


def test_get_end_to_end_on_fake_candles():
    real = XM._candles
    cache = {}
    def fake(symbol, n, ttl=30):
        key = (symbol, n)
        if key not in cache:
            cs = walk(n, sum(map(ord, symbol)), vol=0.0045)
            # make the newest candle "forming" relative to the real clock
            now = int(__import__("time").time() * 1000) // STEP * STEP
            for k, c in enumerate(cs): c["start"] = now - (len(cs) - 1 - k) * STEP
            cache[key] = cs
        return cache[key]
    def check(mode, label):
        n_sig = n_trade = 0
        for sym in ("SOLUSDT", "ETHUSDT", "BTCUSDT", "DOGEUSDT", "LINKUSDT", "XRPUSDT"):
            d = XM.get(mode, sym, 1000, ttl=0)
            assert d["ok"], d
            json.dumps(d, allow_nan=False)
            assert len(d["candles"]) == 1001 and d["interval"] == "15"
            sg = d["signals"]; n_sig += len(sg)
            assert [g["t"] for g in sg] == sorted(g["t"] for g in sg)
            t_first, t_last = d["candles"][0]["start"] // 1000, d["candles"][-1]["start"] // 1000
            assert all(t_first <= g["t"] <= t_last for g in sg), "signals must lie inside the shown candles"
            assert all(g["status"] in ("REJECTED", "LIVE", "OPEN", "TP", "TRAIL", "BE", "SL", "TIMEOUT") for g in sg)
            assert all(g["live"] == (g["t"] == t_last) for g in sg), "only the newest (forming) candle can be live"
            traded = [g for g in sg if g["status"] in ("TP", "TRAIL", "BE", "SL", "TIMEOUT")]; n_trade += len(traded)
            st = d["panel"]["stats"]
            assert st["n"] == len(traded) and st["wins"] == sum(1 for g in traded if g["R"] > 0)
            assert abs(st["total_r"] - sum(g["R"] for g in traded)) < 1e-9
            for a, b in zip(traded, traded[1:]):
                assert b["t"] > a["t_exit"], "one position per coin: a trade starts after the previous one ended"
            for g in traded:
                L = g["side"] == "LONG"
                assert (g["stop"] < g["entry"] < g["target"]) if L else (g["target"] < g["entry"] < g["stop"])
                assert g["t_exit"] > g["t"] and 1 <= g["bars"] <= XM.TIMEOUT_BARS
            for g in sg:
                if g["status"] == "REJECTED": assert g["verdict"] == "REJECT" and mode == "cmex5"
            assert all(r["l"] and r["v"] and len(r["l"]) <= 15 for r in d["panel"]["rows"]) and d["panel"]["reference"]
            assert d["panel"]["headline"] and d["panel"]["headline_c"] in ("up", "dn", "warn", "mut")
        return label, n_sig, n_trade

    XM._candles = fake
    real_v = XM._verdict
    try:
        out = [check("cmex5", "CME-X5 (real agents)"), check("champ", "Championship")]
        XM._cache.clear()
        XM._verdict = lambda *a, **k: (dict(decision="PASS", final_score=0.9, adjustments={}), [])
        out.append(check("cmex5", "CME-X5 (agents stubbed to PASS)"))
        assert out[2][2] > 5, "stubbed agents should let S2/S7 setups trade"
        assert XM.get("nope", "BTCUSDT")["ok"] is False
        print("  get(): well-formed on fake candles, stats equal a recount: " + "; ".join(f"{l}: {s} signals, {t} closed trades" for l, s, t in out))
    finally:
        XM._candles, XM._verdict = real, real_v
        XM._cache.clear()


if __name__ == "__main__":
    fails = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
            except Exception:
                fails += 1
                print(f"FAIL {name}"); traceback.print_exc()
    print("all xmodes tests passed" if not fails else f"{fails} xmodes test(s) failed")
    sys.exit(1 if fails else 0)
