"""Tests for backend_lib/setup_tracker.py (the pasted breakout setups). Run:  python tests/test_setup_tracker.py
Candles are stubbed; nothing here talks to Bybit."""
import os
import sys
import tempfile
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from backend_lib import setup_tracker as S, sbgz  # noqa: E402

CSV = """Coin,Side,Reference USD,Entry,Stop,TP1,TP2,RR TP1,RR TP2,Status
BTC,long,81385.18,81629.34,81222.41,82239.73,82443.19,1.5,2,Unconfirmed
BTC,short,81385.18,81141.03,81547.95,80530.64,80327.17,1.5,2,Unconfirmed
SOL,long,104.265,104.578,104.057,105.360,105.620,1.5,2,Unconfirmed
BAD,long,1,2,3,4,5,1.5,2,Unconfirmed
"""
T0 = 1_800_000_000_000


def cs(rows, t0=T0):
    return [dict(start=t0 + i * 60_000, high=h, low=l, close=(h + l) / 2) for i, (h, l) in enumerate(rows)]


LONG = dict(coin="X", side="LONG", entry=100.0, stop=98.0, tp1=103.0, tp2=104.0)
SHORT = dict(coin="X", side="SHORT", entry=100.0, stop=102.0, tp1=97.0, tp2=96.0)


def test_parse():
    rows, errs = S.parse(CSV)
    assert [(r["coin"], r["side"]) for r in rows] == [("BTC", "LONG"), ("BTC", "SHORT"), ("SOL", "LONG")], rows
    assert len(errs) == 1 and "BAD" in errs[0] and "wrong side" in errs[0], errs
    assert rows[0]["entry"] == 81629.34 and rows[0]["tp2"] == 82443.19
    rows, _ = S.parse(CSV.replace(",", "\t")); assert len(rows) == 3, "tab separated works too"
    assert S.parse("")[0] == []
    print("  the pasted list is read (header skipped, bad rows reported)")


def test_judge():
    j = lambda r, rows, now=T0 + 3_600_000: S.judge(r, cs(rows), T0, now)
    assert j(LONG, [(99, 98.5), (99.5, 99)])["status"] == "WAITING"
    assert j(LONG, [(99, 98.5), (100.5, 99.5)])["status"] == "ENTERED"
    x = j(LONG, [(100.5, 99.5), (103.2, 100)]); assert x["status"] == "TP1 HIT" and x["R1"] == 1.5 and x["R2"] is None, x
    x = j(LONG, [(100.5, 99.5), (103.2, 100), (104.5, 102)]); assert x["status"] == "TP2 HIT" and x["R1"] == 1.5 and x["R2"] == 2.0, x
    x = j(LONG, [(100.5, 99.5), (101, 97.9)]); assert x["status"] == "STOPPED" and x["R1"] == -1.0 and x["R2"] == -1.0, x
    x = j(LONG, [(100.5, 99.5), (103.2, 100), (102, 97)]); assert x["status"] == "STOPPED AFTER TP1" and x["R1"] == 1.5 and x["R2"] == -1.0, x
    x = j(LONG, [(100.5, 99.5), (103.5, 97.5)]); assert x["status"] == "STOPPED", "a candle touching the stop and a target counts as the stop"
    x = j(SHORT, [(99.5, 99), (100.2, 98), (98, 96.5)]); assert x["status"] == "TP1 HIT" and x["best_r"] >= 1.7, x
    x = j(SHORT, [(100.2, 99.5), (102.1, 100)]); assert x["status"] == "STOPPED", x
    assert j(LONG, [(99, 98.5)], now=T0 + 25 * 3_600_000)["status"] == "EXPIRED"
    x = j(LONG, [(100.5, 99.5)], now=T0 + 25 * 3_600_000); assert x["status"] == "ENTERED", "an entered trade does not expire"
    print("  statuses: waiting / entered / TP1 / TP2 / stopped / stopped after TP1 / expired; the worse case in a double touch")


def test_status_end_to_end():
    os.environ["SBGZ_AUTO_BACKEND"] = "file"
    import backend_lib.sbgz_auto as A
    old_root, old_fc = A.ROOT, sbgz.fetch_candles
    with tempfile.TemporaryDirectory() as d:
        A.ROOT = d
        try:
            assert S.status()["empty"]
            r = S.save(CSV); assert r["ok"] and r["saved"] == 3 and len(r["skipped"]) == 1, r
            doc, _ = A._read_key(S.KEY); now = doc["created"] + 2 * 3_600_000; t0 = doc["created"] // 60_000 * 60_000
            def fake(sym, iv, n, timeout=8):
                assert iv == "1", iv                                                  # 2 hours old: 1m candles
                if sym == "BTCUSDT": return cs([(81500, 81300), (81700, 81400), (81500, 81380)], t0 + 60_000)    # the long enters, nothing else
                if sym == "SOLUSDT": return cs([(104, 103.8), (105.5, 104.1)], t0)                                                                 # the long enters and reaches TP1 (105.36)
                return []
            sbgz.fetch_candles = fake; S._cache.clear()
            d = S.status(now); rows = {(x["coin"], x["side"]): x for x in d["rows"]}
            assert rows[("BTC", "LONG")]["status"] == "ENTERED" and rows[("BTC", "SHORT")]["status"] == "WAITING", rows
            assert rows[("SOL", "LONG")]["status"] == "TP1 HIT", rows[("SOL", "LONG")]
            s = d["summary"]; assert s["n"] == 3 and s["waiting"] == 1 and s["open"] == 2 and s["tp1"] == 1, s
            assert abs(s["r_tp1_plan"] - 1.5) < 1e-9, s
            assert S.clear()["ok"] and S.status()["empty"]
        finally:
            A.ROOT = old_root; sbgz.fetch_candles = old_fc; os.environ.pop("SBGZ_AUTO_BACKEND", None)
    print("  save -> status -> clear on the file store, candles only when asked")


def test_a_failed_download_does_not_change_the_result():
    os.environ["SBGZ_AUTO_BACKEND"] = "file"
    import backend_lib.sbgz_auto as A
    old_root, old_fc, old_sleep = A.ROOT, sbgz.fetch_candles, S.time.sleep
    S.time.sleep = lambda x: None
    with tempfile.TemporaryDirectory() as d:
        A.ROOT = d
        try:
            r = S.save(CSV); doc, _ = A._read_key(S.KEY); created = doc["created"]; t0 = created // 60_000 * 60_000; now = created + 2 * 3_600_000
            good = {"BTCUSDT": cs([(81700, 81400), (82300, 81500), (82500, 81600)], t0), "SOLUSDT": cs([(104, 103.9)], t0)}
            calls = {"n": 0}
            def flaky(sym, iv, n, timeout=8):                                      # BTC: the first two downloads fail, the third works
                if sym == "BTCUSDT":
                    calls["n"] += 1
                    return [] if calls["n"] < 3 else good[sym]
                return good.get(sym, [])
            sbgz.fetch_candles = flaky; S._cache.clear()
            d1 = S.status(now); rows = {(x["coin"], x["side"]): x for x in d1["rows"]}
            assert calls["n"] == 3 and rows[("BTC", "LONG")]["status"] == "TP2 HIT" and d1["summary"]["no_data"] == 0, (calls, rows[("BTC", "LONG")]["status"], d1["summary"])
            S._cache.clear(); sbgz.fetch_candles = lambda sym, iv, n, timeout=8: [] if sym == "BTCUSDT" else good.get(sym, [])          # BTC is down for a whole pass
            d2 = S.status(now); rows2 = {(x["coin"], x["side"]): x for x in d2["rows"]}
            assert rows2[("BTC", "LONG")]["status"] == "TP2 HIT" and rows2[("BTC", "LONG")]["R2"] == 2.0 and rows2[("BTC", "LONG")].get("stale"), rows2[("BTC", "LONG")]   # the archived result stands
            assert rows2[("BTC", "SHORT")]["status"] == "NO DATA" and d2["summary"]["no_data"] == 1, (rows2[("BTC", "SHORT")], d2["summary"])                     # nothing archived: honestly no data
            assert d2["summary"]["r_tp2_plan"] == d1["summary"]["r_tp2_plan"], "the net did not move"
        finally:
            A.ROOT = old_root; sbgz.fetch_candles = old_fc; S.time.sleep = old_sleep; os.environ.pop("SBGZ_AUTO_BACKEND", None)
    print("  a download that fails is retried; a finished result already archived stands; a setup with nothing is shown as NO DATA, not as waiting")


if __name__ == "__main__":
    fails = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
            except Exception:
                fails += 1
                print(f"FAIL {name}"); traceback.print_exc()
    print("all setup_tracker tests passed" if not fails else f"{fails} setup_tracker test(s) failed")
    sys.exit(1 if fails else 0)
