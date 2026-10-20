"""Tests for backend_lib/setup_orders.py (the pasted setups as demo orders). Run:  python tests/test_setup_orders.py
A fake Bybit client records every request; nothing here talks to Bybit."""
import os
import sys
import tempfile
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from backend_lib import setup_orders as O, setup_tracker as S, sbgz_auto as A, sbgz_trade as T  # noqa: E402

O._prices = lambda: {}                                                         # no network in tests (each test sets prices when it needs them)
SPEC = dict(tick=0.01, qty_step=0.01, min_qty=0.01, max_qty=1000.0, min_notional=5.0, max_lev=10.0)


class Fake:
    def __init__(self, base="https://api-demo.bybit.com"):
        self.base_url = base; self.posts = []; self.positions = []; self.orders = []; self.reject = set(); self.rate = {}; self.links = set()

    def get_positions(self):
        return dict(retCode=0, result=dict(list=self.positions))

    def set_leverage(self, sym, lev):
        return dict(retCode=110043)

    def signed_request(self, method, path, params=None, body=None):
        if path == "/v5/order/realtime":
            return dict(retCode=0, result=dict(list=[o for o in self.orders if o.get("flt") == (params or {}).get("orderFilter")], nextPageCursor=""))
        if path == "/v5/order/create":
            if body["symbol"] in self.reject: return dict(retCode=110093, retMsg="trigger price is invalid")
            if self.rate.get(body["symbol"], 0) > 0: self.rate[body["symbol"]] -= 1; return dict(retCode=10006, retMsg="Too many visits. Exceeded the API Rate Limit.")
            if body["orderLinkId"] in self.links: return dict(retCode=110072, retMsg="OrderLinkedID is duplicate")
            self.links.add(body["orderLinkId"]); self.posts.append(("create", body)); return dict(retCode=0)
        if path == "/v5/order/cancel":
            self.posts.append(("cancel", body)); return dict(retCode=0)
        return dict(retCode=0, result=dict(list=[]))


def row(coin="AAA", side="LONG", entry=100.0, stop=99.5, tp1=100.75, tp2=101.0):
    if side == "SHORT": entry, stop, tp1, tp2 = entry, 100.5, 99.25, 99.0
    return dict(coin=coin, side=side, ref=100.0, entry=entry, stop=stop, tp1=tp1, tp2=tp2, rr1=1.5, rr2=2.0)


def creates(f): return [b for k, b in f.posts if k == "create"]
def cancels(f): return [b for k, b in f.posts if k == "cancel"]


def test_placing():
    os.environ["SBGZ_AUTO_BACKEND"] = "file"; old = (A.ROOT, T.instrument)
    with tempfile.TemporaryDirectory() as d:
        A.ROOT = d
        T.instrument = lambda sym, timeout=6: None if sym == "NOPEUSDT" else dict(SPEC, min_notional=500.0) if sym == "TINYUSDT" else SPEC
        try:
            f = Fake(); f.positions = [dict(symbol="HELDUSDT", side="Buy", size="1")]; f.orders = [dict(symbol="MINEUSDT", orderLinkId="sbgz-x", flt="Order")]
            rows = [row("AAA", "LONG"), row("AAA", "SHORT"), row("HELD"), row("MINE"), row("NOPE"), row("TINY"), row("REJ")]
            f.reject = {"REJUSDT"}
            r = O.place_all(f, 1_800_000_000_000, rows, 2.0)
            assert r["ok"] and r["placed_setups"] == 2 and r["placed_orders"] == 4, r
            c = creates(f); assert len(c) == 4
            lng = [b for b in c if b["side"] == "Buy"]; sht = [b for b in c if b["side"] == "Sell"]
            assert [b["qty"] for b in lng] == ["2.00", "2.00"], lng                                  # $2 risk / 0.5 distance = 4, half each
            assert {b["takeProfit"] for b in lng} == {"100.75", "101.00"} and lng[0]["stopLoss"] == "99.50" and lng[0]["triggerPrice"] == "100.00", lng
            assert lng[0]["orderType"] == "Market" and lng[0]["triggerDirection"] == 1 and lng[0]["tpslMode"] == "Partial", lng[0]
            assert sht[0]["triggerDirection"] == 2 and sht[0]["stopLoss"] == "100.50" and {b["takeProfit"] for b in sht} == {"99.25", "99.00"}, sht
            assert len({b["orderLinkId"] for b in c}) == 4 and all(b["orderLinkId"].startswith("tg-") and len(b["orderLinkId"]) <= 36 for b in c)
            why = {x[0]: x[2] for x in r["skipped"]}
            assert "hold a position" in why["HELD"] and "order of your own" in why["MINE"] and "not tradable" in why["NOPE"] and "too small" in why["TINY"] and "refused" in why["REJ"], why
            s = O.summary(); assert s["orders"] == 4 and s["waiting"] == 4 and len(s["skipped"]) == 5, s
            # a real-money account is refused
            f2 = Fake("https://api.bybit.com"); r = O.place_all(f2, 1, [row()], 2.0); assert not r["ok"] and "REAL" in r["error"] and not f2.posts, r
            # a setup too small to split goes out as one order exiting at TP1
            T.instrument = lambda sym, timeout=6: dict(SPEC, qty_step=1.0, min_notional=5.0)
            f3 = Fake(); r = O.place_all(f3, 2, [row("ONE", entry=100.0, stop=99.0, tp1=101.5, tp2=102.0)], 3.0)           # total 3 units: halves 1 and 2 -> split
            assert len(creates(f3)) == 2, creates(f3)
            f4 = Fake(); r = O.place_all(f4, 3, [row("ONE", entry=100.0, stop=99.0, tp1=101.5, tp2=102.0)], 1.0)           # total 1 unit: one order at TP1
            assert len(creates(f4)) == 1 and creates(f4)[0]["takeProfit"] == "101.50", creates(f4)
        finally:
            A.ROOT, T.instrument = old; os.environ.pop("SBGZ_AUTO_BACKEND", None)
    print("  two half-size orders per setup (TP1 / TP2), conditional market entry, stop attached; busy / small / refused / real-money cases skipped")


def test_rate_limits_are_waited_out_and_missing_orders_resumed():
    os.environ["SBGZ_AUTO_BACKEND"] = "file"; old = (A.ROOT, T.instrument, O.time.sleep, O.PACE)
    O.time.sleep = lambda x: None; O.PACE = 0
    with tempfile.TemporaryDirectory() as d:
        A.ROOT = d; T.instrument = lambda sym, timeout=6: SPEC
        try:
            f = Fake(); f.rate = {"BBBUSDT": 2, "CCCUSDT": 99}                      # BBB: two refusals, then fine (retried); CCC: refuses every time in this pass
            rows = [row("AAA"), row("BBB"), row("CCC")]
            r = O.place_all(f, 5, rows, 2.0)
            assert r["ok"] and r["placed_setups"] == 2 and r["placed_orders"] == 4 and [x[0] for x in r["skipped"]] == ["CCC"], r
            assert "code 10006" in r["skipped"][0][2] and O.retryable(r), r
            f.rate = {}                                                              # Bybit is calm again: resume places only what is missing, never twice
            n = len(creates(f)); r2 = O.place_all(f, 5, rows, 2.0, resume=True)
            assert r2["placed_orders"] == 6 and r2["placed_setups"] == 3 and not r2["skipped"] and len(creates(f)) == n + 2 and not O.retryable(r2), (r2, n, len(creates(f)))
            assert len({b["orderLinkId"] for b in creates(f)}) == 6
            r3 = O.place_all(f, 5, rows, 2.0, resume=True); assert len(creates(f)) == n + 2 and r3["placed_orders"] == 6, "nothing left to place"
            r4 = O.place_all(f, 6, rows, 2.0); assert len(cancels(f)) >= 6, "a different list starts fresh: the old waiting orders are cancelled"
        finally:
            A.ROOT, T.instrument, O.time.sleep, O.PACE = old; os.environ.pop("SBGZ_AUTO_BACKEND", None)
    print("  'too many visits' is waited out and retried; the setups still missing are placed on a resume, never twice")


def test_voided_setups_are_not_placed_and_invalidation_cancels():
    os.environ["SBGZ_AUTO_BACKEND"] = "file"; old = (A.ROOT, T.instrument, O._prices, O.PACE)
    O.PACE = 0
    with tempfile.TemporaryDirectory() as d:
        A.ROOT = d; T.instrument = lambda sym, timeout=6: SPEC
        try:
            f = Fake()
            sh = lambda c: dict(row(c, "SHORT"), inval=101.0)                                        # short: entry 100, stop 100.5, invalidation 101
            O._prices = lambda: {"AAAUSDT": 101.2, "BBBUSDT": 99.8, "CCCUSDT": 100.3}
            r = O.place_all(f, 9, [sh("AAA"), sh("BBB"), sh("CCC")], 2.0)
            why = {x[0]: x[2] for x in r["skipped"]}
            assert "invalidation" in why["AAA"] and "already through the entry" in why["BBB"] and r["placed_setups"] == 1, r          # only CCC is placed
            assert all(b["symbol"] == "CCCUSDT" for b in creates(f))
            f.posts.clear(); O._prices = lambda: {"CCCUSDT": 101.05}                                # price runs up past the invalidation before the entry
            ev = O.manage(f, 9 + 60_000, force=True)
            assert len(cancels(f)) == 2 and O.summary()["waiting"] == 0 and len(ev) == 1 and "invalidated" in ev[0], (f.posts, ev)
            # a position you already held on the coin (opened before the order) is not taken for a fill
            f2 = Fake(); O._prices = lambda: {}
            O.place_all(f2, int(O.time.time() * 1000), [row("DDD")], 2.0); placed = min(o["placed"] for o in O._load()[0]["orders"].values())
            f2.positions = [dict(symbol="DDDUSDT", side="Buy", size="1", avgPrice="99", createdTime=str(placed - 3_600_000))]
            O.manage(f2, placed + 60_000, force=True); assert O.summary()["filled"] == 0, O.summary()
            f2.positions[0]["createdTime"] = str(placed + 30_000); O.manage(f2, placed + 90_000, force=True); assert O.summary()["filled"] == 2, O.summary()
        finally:
            A.ROOT, T.instrument, O._prices, O.PACE = old; os.environ.pop("SBGZ_AUTO_BACKEND", None)
    print("  setups already void or already through the entry are not placed; price past the invalidation cancels the waiting orders; an old position is not a fill")


def test_manage():
    os.environ["SBGZ_AUTO_BACKEND"] = "file"; old = (A.ROOT, T.instrument, S.expiry_ms)
    with tempfile.TemporaryDirectory() as d:
        A.ROOT = d; T.instrument = lambda sym, timeout=6: SPEC; S.expiry_ms = lambda: 24 * 3_600_000
        try:
            f = Fake(); created = 1_800_000_000_000
            O.place_all(f, created, [row("AAA", "LONG"), row("AAA", "SHORT"), row("BBB", "LONG")], 2.0); f.posts.clear()
            now = created + 3_600_000
            assert O.manage(f, now, force=True) == [] and not f.posts, "nothing triggered: nothing to do"
            f.positions = [dict(symbol="AAAUSDT", side="Buy", size="4", avgPrice="100.0")]
            ev = O.manage(f, now + 60_000, force=True)
            assert len(ev) == 1 and "AAA LONG triggered" in ev[0], ev
            cc = cancels(f); assert len(cc) == 2 and all(b["symbol"] == "AAAUSDT" and b["orderFilter"] == "StopOrder" for b in cc), cc          # the short's two orders
            assert O.summary()["filled"] == 2 and O.summary()["cancelled"] == 2 and O.summary()["waiting"] == 2, O.summary()
            f.positions = []
            O.manage(f, now + 120_000, force=True); assert O.summary()["done"] == 2, O.summary()
            f.posts.clear(); O.manage(f, created + 25 * 3_600_000, force=True)                                                                 # the list expired
            assert len(cancels(f)) == 2 and O.summary()["waiting"] == 0, (f.posts, O.summary())
            f.posts.clear(); O.place_all(f, created + 10, [row("CCC")], 2.0); f.posts.clear()
            assert O.cancel_all(f, "cleared") == 2 and len(cancels(f)) == 2 and O.summary()["cancelled"] >= 2
        finally:
            A.ROOT, T.instrument, S.expiry_ms = old; os.environ.pop("SBGZ_AUTO_BACKEND", None)
    print("  a triggered order cancels its either-or partner, finished trades are noted, expired lists are cancelled, cancel_all works")


def test_fees_and_calendar():
    os.environ["SBGZ_AUTO_BACKEND"] = "file"; old = (A.ROOT, S.status)
    with tempfile.TemporaryDirectory() as d:
        A.ROOT = d
        try:
            r = dict(entry=100.0, stop=99.5)
            assert abs(S.fee_r(r, True) - (0.00055 + 0.0002) * 100 / 0.5) < 1e-9 and abs(S.fee_r(r, False) - 0.00110 * 100 / 0.5) < 1e-9
            day = 1_800_000_000_000 // 86_400_000 * 86_400_000
            rows = [dict(coin="A", side="LONG", entry=100.0, stop=99.5, tp1=100.75, tp2=101.0, entered=day + 1, tp1_t=day + 10_000, tp2_t=day + 20_000, stop_t=None, R1=1.5, R2=2.0, status="TP2 HIT"),
                    dict(coin="B", side="SHORT", entry=100.0, stop=100.5, tp1=99.25, tp2=99.0, entered=day + 1, tp1_t=None, tp2_t=None, stop_t=day + 30_000, R1=-1.0, R2=-1.0, status="STOPPED"),
                    dict(coin="C", side="LONG", entry=100.0, stop=99.5, tp1=100.75, tp2=101.0, entered=day + 1, tp1_t=day + 40_000, tp2_t=None, stop_t=day + 90_000_000, R1=1.5, R2=-1.0, status="STOPPED AFTER TP1")]
            for x in rows: x["fee1"] = S.fee_r(x, x["R1"] > 0); x["fee2"] = S.fee_r(x, x["R2"] > 0)
            S._archive(777, rows)
            S.status = lambda now=None: dict(ok=True)
            c2 = S.calendar(plan=2, risk=2.0, fees=False, tz=0.0)
            assert c2["ok"] and [d_["date"] for d_ in c2["days"]][-1] and c2["total"]["n"] == 3 and c2["total"]["wins"] == 1 and c2["total"]["losses"] == 2 and c2["total"]["r"] == 0.0, c2     # +2 -1 -1
            c1 = S.calendar(plan=1, risk=2.0, fees=False)
            assert c1["total"]["r"] == 2.0 and c1["total"]["wins"] == 2 and c1["total"]["usd"] == 4.0, c1                                                      # +1.5 -1 +1.5
            c1f = S.calendar(plan=1, risk=2.0, fees=True)
            assert c1f["total"]["r"] < c1["total"]["r"] and c1f["fees"], (c1f["total"], c1["total"])
            cz = S.calendar(plan=2, risk=0, fees=False, tz=14.0); assert cz["total"]["n"] == 3 and cz["total"]["usd"] is None
        finally:
            A.ROOT, S.status = old; os.environ.pop("SBGZ_AUTO_BACKEND", None)
    print("  fees (taker entry, maker TP / taker stop) in R; the archive feeds a by-day profit calendar per plan, with or without fees")


if __name__ == "__main__":
    fails = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
            except Exception:
                fails += 1
                print(f"FAIL {name}"); traceback.print_exc()
    print("all setup_orders tests passed" if not fails else f"{fails} setup_orders test(s) failed")
    sys.exit(1 if fails else 0)
