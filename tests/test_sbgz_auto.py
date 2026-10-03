"""Tests for backend_lib/sbgz_auto.py (the SBGZ auto-orders).  Run:  python tests/test_sbgz_auto.py

A fake exchange keeps orders, positions and closed PnL like Bybit would; the radar scan, the setups and the instrument limits are
stubbed. Nothing here talks to Bybit, so no order can be sent.
"""
import json
import os
import sys
import tempfile
import time
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from backend_lib import sbgz, sbgz_trade as T, sbgz_auto as A  # noqa: E402

SPEC = dict(qty_step=0.01, min_qty=0.01, max_qty=1e6, min_notional=5.0, tick=0.001, max_lev=25.0)
STEP = {"15": 900, "60": 3600}
T0 = 1_800_000_000                      # first candle (seconds)
N = 300                                 # candles per chart
ARM = 100                               # the bar index every test setup armed on
CLOCK0 = T0 * 1000 + 290 * 900_000      # the exchange's clock at the start (ms)


def zone(entry=10.0, stop=9.8, target=10.9, side="LONG", vol_ok=True, since_bar=ARM, strong=True):
    return dict(side=side, entry=entry, stop=stop, target=target, strong=strong, vol_ok=vol_ok, bvol=3.0 if vol_ok else 1.2, break_vu=15.0,
                zone_top=entry, zone_bottom=entry - 0.1, since_bar=since_bar)


class World:
    """The radar's view: per (coin, interval) the candles' closes, the price now, the waiting setups and the recently ended ones."""
    def __init__(self):
        self.charts = {}
        self.add("BTCUSDT", "15", 1000.0); self.add("BTCUSDT", "60", 1000.0)

    def add(self, sym, iv, price, closes=None, setups=(), missed=()):
        times = [T0 + i * STEP[iv] for i in range(N)]
        self.charts[(sym, iv)] = dict(times=times, closes=list(closes or [price] * N), price=price, setups=list(setups), missed=list(missed))

    def scan(self, drop=()):
        res = []
        for (s, iv), c in self.charts.items():
            res.append(((s, iv), None if (s, iv) in drop else dict(price=c["price"], times=c["times"], closes=c["closes"], panel={}, setups=c["setups"],
                                                                   open_trades=[], trades=[], missed=c["missed"], warmup=150)))
        return (1, res)

    def get(self, sym, interval="15", bars=1000, ttl=30):
        c = self.charts.get((sym, interval))
        if not c: return dict(ok=False, error="no candles")
        return dict(ok=True, symbol=sym, interval=interval, setups=c["setups"], times=c["times"],
                    candles=[dict(start=0, open=c["price"], high=c["price"], low=c["price"], close=c["price"], volume=1.0)])


class FakeEx:
    """Just enough of Bybit: wallet, positions, resting orders, order create / cancel / history, closed PnL. Records every call."""
    def __init__(self, equity=10_000.0, base_url="https://api-demo.bybit.com"):
        self.base_url, self.equity = base_url, equity
        self.time_offset = CLOCK0 - int(time.time() * 1000)
        self.orders, self.positions, self.closed, self.history, self.calls = {}, {}, [], {}, []
        self.price, self.fail, self.n = {}, {}, 0

    @property
    def clock(self):
        return CLOCK0

    def get_wallet_balance(self):
        if self.fail.get("wallet"): return {"retCode": 10003, "retMsg": "API key is invalid"}
        return {"retCode": 0, "result": {"list": [{"totalEquity": str(self.equity)}]}}

    def get_positions(self, symbol=None):
        return {"retCode": 0, "result": {"list": [p for p in self.positions.values() if symbol is None or p["symbol"] == symbol]}}

    def get_open_orders(self, symbol=None):
        return {"retCode": 0, "result": {"list": [o for o in self.orders.values() if symbol is None or o["symbol"] == symbol]}}

    def set_leverage(self, symbol, leverage):
        self.calls.append(("leverage", symbol, leverage))
        return {"retCode": 110043, "retMsg": "leverage not modified"}

    def signed_request(self, method, path, params=None, body=None):
        self.calls.append((method, path, params, body))
        if path == "/v5/order/create":
            if self.fail.get("create"): return {"retCode": 110017, "retMsg": "refused"}
            self.n += 1
            if body.get("reduceOnly") and body.get("orderType") == "Market":                 # a market close: fills at the price now
                self._close(body["symbol"], float(self.price.get(body["symbol"], self.positions[body["symbol"]]["avgPrice"])))
                return {"retCode": 0, "result": {"orderId": f"c{self.n}"}}
            o = dict(orderId=f"o{self.n}", orderLinkId=body["orderLinkId"], symbol=body["symbol"], side=body["side"], price=body["price"], qty=body["qty"],
                     orderStatus="New", stopLoss=body.get("stopLoss"), takeProfit=body.get("takeProfit"), createdTime=str(CLOCK0), reduceOnly=False)
            self.orders[o["orderLinkId"]] = o; self.history[o["orderLinkId"]] = "New"
            return {"retCode": 0, "result": {"orderId": o["orderId"]}}
        if path == "/v5/order/cancel":
            key = body.get("orderLinkId") or next((k for k, o in self.orders.items() if o["orderId"] == body.get("orderId")), None)
            if key not in self.orders: return {"retCode": 110001, "retMsg": "order not exists"}
            self.history[self.orders[key].get("orderLinkId") or key] = "Cancelled"; del self.orders[key]
            return {"retCode": 0, "result": {}}
        if path == "/v5/order/realtime": return {"retCode": 0, "result": {"list": list(self.orders.values())}}
        if path == "/v5/order/history":
            st = self.history.get(params.get("orderLinkId"))
            return {"retCode": 0, "result": {"list": [{"orderStatus": st}] if st else []}}
        if path == "/v5/position/closed-pnl":
            return {"retCode": 0, "result": {"list": [c for c in self.closed if c["symbol"] == params["symbol"]]}}
        if path == "/v5/position/list":
            return {"retCode": 0, "result": {"list": [p for p in self.positions.values() if p["symbol"] == params["symbol"]]}}
        return {"retCode": -1, "retMsg": "unexpected call " + path}

    # test controls
    def fill(self, link, price=None, children=True):
        o = self.orders.pop(link); self.history[link] = "Filled"
        self.positions[o["symbol"]] = dict(symbol=o["symbol"], side=o["side"], size=o["qty"], avgPrice=str(price or o["price"]), createdTime=str(self.clock),
                                           stopLoss=o["stopLoss"], takeProfit=o["takeProfit"])
        if children:                                                                         # the attached take-profit / stop-loss orders
            for kind, px in (("PartialTakeProfit", o["takeProfit"]), ("PartialStopLoss", o["stopLoss"])):
                self.n += 1; self.orders[f"child{self.n}"] = dict(orderId=f"ch{self.n}", orderLinkId="", symbol=o["symbol"], side="Sell" if o["side"] == "Buy" else "Buy",
                                                                   price=px, qty=o["qty"], orderStatus="Untriggered", reduceOnly=True, stopOrderType=kind, createdTime=str(self.clock))

    def _close(self, sym, px):
        p = self.positions.pop(sym); sd = 1 if p["side"] == "Buy" else -1; size, e = float(p["size"]), float(p["avgPrice"])
        self.closed.append(dict(symbol=sym, closedPnl=str(sd * (px - e) * size - 0.00055 * (e + px) * size), avgExitPrice=str(px), avgEntryPrice=str(e),
                                updatedTime=str(self.clock), createdTime=str(self.clock)))

    def hit(self, sym, px, drop_children=True):
        self._close(sym, px)
        if drop_children:
            for k in [k for k, o in self.orders.items() if o["symbol"] == sym and o.get("reduceOnly")]: del self.orders[k]

    def posts(self, path="/v5/order/create"):
        return [c[3] for c in self.calls if c[0] == "POST" and c[1] == path]


class Env:
    """Fresh state in a temp file, stubs in place; restores everything on exit."""
    def __init__(self, world, ex=None, cap=0.0):
        self.world, self.ex, self.cap = world, ex or FakeEx(), cap          # cap: the account size setting (0 = the fake account's own equity)

    def __enter__(self):
        self.dir = tempfile.mkdtemp()
        self.saved = (A._store_file, sbgz.get, T.instrument, os.environ.get("SBGZ_AUTO_BACKEND"))
        os.environ["SBGZ_AUTO_BACKEND"] = "file"
        A._store_file = lambda key=A.STORE_KEY: os.path.join(self.dir, key + ".json")
        sbgz.get = self.world.get
        T.instrument = lambda sym, timeout=6: SPEC
        A.reset_for_tests()
        A._ensure()["settings"].update(equity_usd=self.cap, risk_pct=0.25); A._save()          # the older tests' numbers were written for 0.25 % of the equity
        return self

    def __exit__(self, *a):
        A._store_file, sbgz.get, T.instrument = self.saved[:3]
        if self.saved[3] is None: os.environ.pop("SBGZ_AUTO_BACKEND", None)
        else: os.environ["SBGZ_AUTO_BACKEND"] = self.saved[3]
        A.reset_for_tests()

    def mode(self, mode, **kw):
        r = A.update_settings(dict(mode=mode, **kw), self.ex, confirm=True); assert r["ok"], r; return r

    def cycle(self, drop=()):
        r = A.run_cycle(self.ex, scan=self.world.scan(drop), now=self.ex.clock); return r

    def log(self, n=50, kinds=None):
        return [(e["kind"], e["symbol"], e["text"]) for e in A.status(self.ex)["log"][:n] if not kinds or e["kind"] in kinds]

    def tracked(self, **where):
        return [t for t in A.status(self.ex)["tracked"] if all(t.get(k) == v for k, v in where.items())]


def one_setup(price=10.5, sym="XYZUSDT", iv="60", **kw):
    w = World(); w.add(sym, iv, price, setups=[zone(**kw)]); return w


# ── tests ────────────────────────────────────────────────────────────────────────────────────────────────────────────
def test_settings_validation():
    base = dict(A.DEFAULTS)
    assert A.clean_settings(dict(risk_pct=0.5, intervals=[60, "15"], vol_only=False), base)["intervals"] == ["15", "60"]
    assert A.clean_settings(dict(risk_pct=5, equity_usd=0), base)["equity_usd"] == 0.0 and A.clean_settings(dict(equity_usd=20), base)["equity_usd"] == 20.0
    assert A.DEFAULTS["equity_usd"] == 20.0 and A.DEFAULTS["risk_pct"] == 1.0 and A.DEFAULTS["mode"] == "off"
    for bad in (dict(mode="on"), dict(risk_pct=0), dict(risk_pct=5.5), dict(equity_usd=-1), dict(equity_usd="x"), dict(vol_only="yes"), dict(intervals=[]),
                dict(intervals=["5"]), dict(max_resting=0), dict(leverage=99), dict(daily_stop_r="x")):
        try:
            A.clean_settings(bad, base); raise AssertionError(f"accepted {bad}")
        except ValueError:
            pass
    assert A.clean_settings(dict(unknown=1), base) == base
    print("  settings: validated, bounded, unknown keys ignored")


def test_off_does_nothing_and_default_is_off():
    with Env(one_setup()) as e:
        assert A.status(e.ex)["settings"]["mode"] == "off"
        r = e.cycle(); assert r["msg"] == "off" and not e.ex.calls, e.ex.calls
    print("  off by default: no call at all")


def test_going_live_needs_confirmation_and_demo_account():
    with Env(one_setup()) as e:
        r = A.update_settings(dict(mode="live"), e.ex); assert not r["ok"] and r.get("need_confirm"), r
        assert A.status(e.ex)["settings"]["mode"] == "off"
        assert A.update_settings(dict(mode="preview"), e.ex)["ok"]                      # preview needs no confirmation
        assert A.update_settings(dict(mode="live"), e.ex, confirm=True)["ok"]
    with Env(one_setup(), FakeEx(base_url="https://api.bybit.com")) as e:               # a real-money account is never accepted
        for m in ("preview", "live"):
            r = A.update_settings(dict(mode=m), e.ex, confirm=True); assert not r["ok"] and "REAL" in r["error"], r
        A._ensure()["settings"]["mode"] = "live"; A._save()                              # even if the store says live
        r = e.cycle(); assert not r["ok"] and A.status(e.ex)["settings"]["mode"] == "off" and not e.ex.posts(), r
    print("  live needs a confirmation; real-money accounts are refused everywhere")


def test_preview_decides_logs_and_sends_nothing():
    w = World(); w.add("AAAUSDT", "60", 10.5, setups=[zone()]); w.add("BBBUSDT", "15", 10.5, setups=[zone(vol_ok=False)])
    with Env(w) as e:
        e.mode("preview"); e.cycle()
        place = [x for x in e.log(kinds=("place",))]
        assert len(place) == 1 and "WOULD place" in place[0][2] and place[0][1] == "AAAUSDT", place          # the unconfirmed one is filtered out
        assert "125.00 @ 10.000" in place[0][2] and "stop 9.800" in place[0][2] and "target 10.900" in place[0][2], place
        e.cycle(); e.cycle(); assert len(e.log(kinds=("place",))) == 1, "the same decision is not logged again"
        w.charts[("AAAUSDT", "60")]["setups"] = [zone(entry=10.2, stop=9.95, target=11.0)]                    # the impulse extended
        e.cycle(); assert any("WOULD move" in x[2] for x in e.log(kinds=("move",))), e.log()
        w.charts[("AAAUSDT", "60")]["setups"] = []; w.charts[("AAAUSDT", "60")]["missed"] = [dict(side="LONG", arm_bar=ARM, end_bar=150, why="swing")]
        e.cycle(); c = e.log(kinds=("cancel",)); assert c and "WOULD cancel" in c[0][2] and "new swing formed" in c[0][2], c
        assert not [x for x in e.ex.calls if x[0] != "GET" and x[0] != "leverage"], [x for x in e.ex.calls if x[0] != "GET"]
        assert not e.ex.posts() and not e.ex.orders
    print("  preview: would-place / move / cancel logged once each, no order call of any kind")


def test_live_places_the_right_order_once():
    with Env(one_setup()) as e:
        e.mode("live"); r = e.cycle(); assert r["ok"] and r["counts"]["placed"] == 1, r
        (b,) = e.ex.posts()
        assert (b["symbol"], b["side"], b["orderType"], b["timeInForce"]) == ("XYZUSDT", "Buy", "Limit", "PostOnly"), b
        assert (b["price"], b["stopLoss"], b["takeProfit"], b["qty"]) == ("10.000", "9.800", "10.900", "125.00"), b
        assert (b["tpslMode"], b["tpOrderType"], b["tpLimitPrice"], b["slOrderType"]) == ("Partial", "Limit", "10.900", "Market"), b
        assert b["orderLinkId"].startswith("sbgz-XYZUSDT-60-L-z") and len(b["orderLinkId"]) <= 36, b["orderLinkId"]
        t = e.tracked(symbol="XYZUSDT")[0]; assert t["status"] == "resting" and abs(t["risk_usd"] - 25.0) < 0.01 and not t["virtual"], t
        e.cycle(); e.cycle(); assert len(e.ex.posts()) == 1, "one order per setup"
        st = A.status(e.ex); assert st["day"]["orders"] == 1 and st["status"]["ok"] and st["running"] in (True, False)
        assert T.open_orders(e.ex)["orders"][0]["auto"] is True
        json.dumps(st, allow_nan=False)
    print("  live: one post-only limit with take-profit / stop (tagged 'z'), not repeated")


def test_filters_and_priority():
    w = World()
    for i, (iv, vol) in enumerate((("15", True), ("60", True), ("60", False), ("15", False))):
        w.add(f"C{i}USDT", iv, 10.5, setups=[zone(vol_ok=vol, since_bar=ARM + i)])
    with Env(w) as e:
        e.mode("live", max_new_per_cycle=1); e.cycle()
        assert [t["symbol"] for t in e.tracked()] == ["C1USDT"], "volume-confirmed, 1h first"
        e.cycle(); e.cycle(); assert sorted(t["symbol"] for t in e.tracked()) == ["C0USDT", "C1USDT"], "the unconfirmed ones stay out"
        A.update_settings(dict(vol_only=False, max_new_per_cycle=5), e.ex); e.cycle()
        assert len(e.tracked()) == 4
    with Env(w) as e:
        e.mode("live", intervals=["60"]); e.cycle(); assert [t["symbol"] for t in e.tracked()] == ["C1USDT"]
    print("  filters: volume-confirmed only, intervals, priority order, per-pass limit")


def test_caps():
    w = World()
    for i in range(8): w.add(f"C{i}USDT", "60", 10.5, setups=[zone(since_bar=ARM + i)])
    with Env(w) as e:
        e.mode("live", max_resting=3, max_new_per_cycle=5); e.cycle(); e.cycle()
        assert len(e.tracked()) == 3, "waiting orders are capped"
    with Env(w) as e:
        e.mode("live", max_open_risk_pct=0.6, max_new_per_cycle=5)                    # 0.25% each: two fit, the third would be 0.75%
        e.cycle(); assert len(e.tracked()) == 2 and any("total risk" in x[2] for x in e.log(kinds=("skip",))), e.log()
    with Env(w) as e:
        e.mode("live", max_orders_per_day=2, max_new_per_cycle=5, max_resting=20); e.cycle()
        assert len(e.tracked()) == 2 and any(x[0] == "halt" for x in e.log()), e.log()
    with Env(w) as e:
        e.mode("live", max_open=1, max_resting=20, max_new_per_cycle=1); e.cycle(); e.ex.fill(e.tracked()[0]["link"]); e.cycle()
        n = len(e.ex.posts()); e.cycle(); assert len(e.ex.posts()) == n, "no new order while the open-trade cap is reached"
    print("  caps: waiting orders, total risk, orders per day, open trades")


def test_busy_coins_and_manual_orders_are_left_alone():
    w = World(); w.add("AAAUSDT", "60", 10.5, setups=[zone()]); w.add("BBBUSDT", "60", 10.5, setups=[zone()]); w.add("CCCUSDT", "60", 10.5, setups=[zone()])
    ex = FakeEx()
    ex.positions["AAAUSDT"] = dict(symbol="AAAUSDT", side="Buy", size="5", avgPrice="9", createdTime="1")                  # a position of the user's
    ex.orders["sbgz-BBBUSDT-60-L-5f5e1000"] = dict(orderId="m1", orderLinkId="sbgz-BBBUSDT-60-L-5f5e1000", symbol="BBBUSDT", side="Buy", price="10", qty="1",
                                                   orderStatus="New", reduceOnly=False, createdTime="1")               # a manual order of the button
    with Env(w, ex) as e:
        e.mode("live", max_new_per_cycle=5); e.cycle()
        assert [t["symbol"] for t in e.tracked()] == ["CCCUSDT"], e.tracked()
        w.charts[("BBBUSDT", "60")]["setups"] = []; e.cycle()                                   # its setup is gone: the manual order stays
        assert "sbgz-BBBUSDT-60-L-5f5e1000" in ex.orders and not ex.posts("/v5/order/cancel")
        A.kill(ex); assert "sbgz-BBBUSDT-60-L-5f5e1000" in ex.orders and "AAAUSDT" in ex.positions
    print("  coins with a position or a manual order are skipped; manual orders and positions are never touched")


def test_moved_order_is_replaced_and_ended_order_cancelled():
    w = one_setup()
    with Env(w) as e:
        e.mode("live"); e.cycle(); first = e.tracked()[0]["link"]
        w.charts[("XYZUSDT", "60")]["setups"] = [zone(entry=10.2, stop=9.95, target=11.0)]            # the impulse extended: new levels
        r = e.cycle(); assert r["counts"]["moved"] == 1
        live = [o for o in e.ex.orders.values() if o["orderLinkId"]]
        assert len(live) == 1 and live[0]["price"] == "10.200" and live[0]["orderLinkId"] != first and live[0]["stopLoss"] == "9.950", live
        assert e.ex.history[first] == "Cancelled" and any(x[0] == "move" and "moved the order" in x[2] for x in e.log()), e.log()
        assert [t["status"] for t in e.tracked(symbol="XYZUSDT")].count("resting") == 1
        e.cycle(); assert len(e.ex.posts()) == 2, "stable setup: nothing more to do"
        w.charts[("XYZUSDT", "60")]["setups"] = []                                                    # a new swing formed before the fill
        w.charts[("XYZUSDT", "60")]["missed"] = [dict(side="LONG", arm_bar=ARM, end_bar=170, why="swing")]
        e.cycle(); assert not [o for o in e.ex.orders.values() if o["orderLinkId"]]
        assert any("new swing formed" in x[2] for x in e.log(kinds=("cancel",))), e.log()
        w.charts[("XYZUSDT", "60")]["setups"] = [zone(entry=10.2, stop=9.95, target=11.0)]            # it comes back (a one-candle flicker): placed again
        e.cycle(); assert len([o for o in e.ex.orders.values() if o["orderLinkId"]]) == 1
    print("  waiting orders follow their setup: moved -> re-placed, ended -> cancelled with the reason, a flicker is placed again")


def test_moved_but_price_already_through_cancels_for_good():
    w = one_setup()
    with Env(w) as e:
        e.mode("live"); e.cycle()
        w.charts[("XYZUSDT", "60")]["setups"] = [zone(entry=10.2, stop=9.95, target=11.0)]
        w.charts[("XYZUSDT", "60")]["price"] = 10.1                                                   # already below the new entry: a post-only order would be refused
        e.cycle(); assert not [o for o in e.ex.orders.values() if o["orderLinkId"]]
        assert any("cannot be re-placed" in x[2] for x in e.log()), e.log()
        w.charts[("XYZUSDT", "60")]["price"] = 10.6; e.cycle(); e.cycle()
        assert len(e.ex.posts()) == 1, "the same setup is not placed again"
    print("  a moved setup whose new entry is already behind the price: old order cancelled, setup not placed again")


def btc_world(drop_to, price=10.5):
    """XYZ 1h setup armed at ARM; BTC flat until the end, then at `drop_to` on the last candles."""
    w = World(); w.add("XYZUSDT", "60", price, setups=[zone()])
    c = [1000.0] * N
    for i in range(N - 3, N): c[i] = drop_to
    w.add("BTCUSDT", "60", c[-1], closes=c); return w


def test_btc_cancel_option():
    for opt, drop, expect in ((True, 989.0, "cancelled"), (True, 997.0, "resting"), (False, 989.0, "resting")):   # risk 2% -> 0.5R = BTC -1%
        with Env(btc_world(drop)) as e:
            e.mode("live", btc_cancel=opt); e.cycle(); e.cycle()
            assert [t["status"] for t in e.tracked(symbol="XYZUSDT")] == [expect], (opt, drop, e.tracked(), e.log())
            if expect == "cancelled":
                assert any("BTC has moved" in x[2] for x in e.log(kinds=("cancel",)))
                e.cycle(); assert len(e.ex.posts()) == 1, "not placed again"
    print("  BTC cancel option: cancels at -0.5R since the signal and stays cancelled; off by default")


def filled_trade(e, w, price_after=10.5):
    e.cycle(); link = e.tracked(symbol="XYZUSDT")[0]["link"]; e.ex.fill(link); e.cycle(); return link


def test_fill_is_noticed_and_btc_rule_closes_at_market():
    w = World(); w.add("XYZUSDT", "60", 10.5, setups=[zone()])
    c = [1000.0] * N; w.add("BTCUSDT", "60", 1000.0, closes=c)
    fill_idx = N - 4                                                                                  # the fill candle: 3 closed candles ago
    with Env(w) as e:
        e.mode("live"); e.cycle(); link = e.tracked(symbol="XYZUSDT")[0]["link"]
        e.ex.fill(link); e.ex.positions["XYZUSDT"]["createdTime"] = str((T0 + fill_idx * 3600) * 1000 + 5000)
        e.cycle(); t = e.tracked(symbol="XYZUSDT")[0]; assert t["status"] == "open" and any(x[0] == "fill" for x in e.log()), (t, e.log())
        assert not e.ex.posts()[1:], "BTC is calm: no close"
        c2 = [1000.0] * N
        for i in range(N - 2, N): c2[i] = 988.0                                                         # BTC -1.2% after the fill = -0.6R
        w.add("BTCUSDT", "60", 988.0, closes=c2)
        e.ex.price["XYZUSDT"] = 10.3; e.cycle()
        close = e.ex.posts()[-1]
        assert close["reduceOnly"] is True and close["orderType"] == "Market" and close["side"] == "Sell" and close["qty"] == "125.00", close
        assert any(x[0] == "close" and "BTC moved" in x[2] for x in e.log()), e.log()
        assert not e.ex.positions, "the fake closed it"
        e.cycle()                                                                                         # next pass: outcome + leftover take-profit / stop orders removed
        done = e.tracked(symbol="XYZUSDT")[0]
        assert done["status"] == "done" and 1.3 < done["R"] < 1.6, done                                  # closed at 10.3: +0.3 on 0.2 of risk, after fees
        assert any(x[0] == "closed" and "closed by the BTC rule" in x[2] for x in e.log()), e.log()
        assert not [o for o in e.ex.orders.values() if o.get("reduceOnly")], "orphaned take-profit / stop orders removed"
        assert A.status(e.ex)["day"]["closed"] == 1
    with Env(w) as e:                                                                                   # the option off: no close
        w.add("BTCUSDT", "60", 988.0, closes=c2); e.mode("live", btc_close=False); e.cycle()
        link = e.tracked(symbol="XYZUSDT")[0]["link"]; e.ex.fill(link); e.ex.positions["XYZUSDT"]["createdTime"] = str((T0 + fill_idx * 3600) * 1000 + 5000)
        e.cycle(); e.cycle(); assert e.ex.positions and len(e.ex.posts()) == 1
    print("  a fill is noticed; BTC -0.5R closes the trade at the market (reduce-only), leftovers removed, R logged; option off = no close")


def test_exchange_exits_are_booked_and_the_daily_stop_halts():
    w = World()
    for i in range(4): w.add(f"C{i}USDT", "60", 10.5, setups=[zone(since_bar=ARM + i)])
    with Env(w) as e:
        e.mode("live", max_new_per_cycle=1, max_resting=10, max_open=10, daily_stop_r=2.0, btc_close=False); e.cycle()
        l1 = e.tracked()[0]; e.ex.fill(l1["link"]); e.cycle()
        e.ex.hit(l1["symbol"], 10.9, drop_children=True); e.cycle()                                     # target: about +4.3R
        d = [t for t in e.tracked() if t["symbol"] == l1["symbol"]][0]
        assert d["status"] == "done" and 4.0 < d["R"] < 4.6, d
        assert any(x[0] == "closed" and "target hit" in x[2] for x in e.log()), e.log()
        assert abs(A.status(e.ex)["day"]["r"] - d["R"]) < 1e-9
        for _ in range(3):                                                                              # three stop-outs: -1.1R each
            e.cycle(); cur = [t for t in e.tracked() if t["status"] == "resting"]
            if not cur: break
            e.ex.fill(cur[0]["link"]); e.cycle(); e.ex.hit(cur[0]["symbol"], 9.8); e.cycle()
        st = A.status(e.ex); assert st["day"]["closed"] >= 3 and not st["halted"], st["day"]               # +4.3R then stop-outs: still above -2R
        before = len(e.ex.posts()); A._ensure()["day"]["r"] = -2.5; assert A.status(e.ex)["halted"]; e.cycle()
        assert len(e.ex.posts()) == before and any(x[0] == "halt" and "daily loss stop" in x[2] for x in e.log()), e.log()
    print("  target / stop exits are booked with their R; the daily loss stop blocks new orders")


def test_order_cancelled_from_outside_is_not_placed_again():
    with Env(one_setup()) as e:
        e.mode("live"); e.cycle(); link = e.tracked()[0]["link"]
        del e.ex.orders[link]; e.ex.history[link] = "Cancelled"                                          # the user cancelled it in the Bybit app
        e.cycle(); assert e.tracked()[0]["status"] == "gone" and any("no longer on Bybit" in x[2] for x in e.log()), e.log()
        e.cycle(); e.cycle(); assert len(e.ex.posts()) == 1
    print("  an order cancelled from outside is noticed and not placed again")


def test_failures_fail_closed():
    with Env(one_setup()) as e:
        e.mode("live"); e.ex.fail["wallet"] = True
        r = e.cycle(); assert not r["ok"] and not e.ex.posts() and any(x[0] == "error" for x in e.log()), (r, e.log())
        e.cycle(); assert len([x for x in e.log() if x[0] == "error"]) == 1, "an error repeats once, not every pass"
        e.ex.fail["wallet"] = False; e.ex.fail["create"] = True
        e.cycle(); assert not e.tracked() and any("refused" in x[2] for x in e.log()), e.log()
        e.ex.fail["create"] = False; e.cycle(); assert not e.tracked(), "a refused order waits 10 minutes before the next try"
        e.ex.calls.clear(); A.run_cycle(e.ex, scan=e.world.scan(), now=e.ex.clock + A.RETRY_AFTER_MS + 1000); assert len(e.ex.posts()) == 1
    w = one_setup()
    with Env(w) as e:                                                                                    # partial radar data: no action
        e.mode("live"); e.cycle(); n = len(e.ex.posts())
        w.charts[("XYZUSDT", "60")]["setups"] = []                                                       # looks ended, but the chart did not download
        r = e.cycle(drop=[("XYZUSDT", "60")]); assert [t["status"] for t in e.tracked()] == ["resting"] and len(e.ex.posts()) == n, r
        r = e.cycle(drop=[("XYZUSDT", "60"), ("BTCUSDT", "15"), ("BTCUSDT", "60")]); assert "charts downloaded" in r["msg"] or e.tracked()
    print("  failures fail closed: no wallet / refusal / missing chart leads to a cancel or a blind order; errors are logged once")


def test_unplannable_setups_are_not_replanned_every_pass():
    w = one_setup(price=9.9)                                                                                          # price already through the entry
    calls = {"n": 0}; real = T.plan
    def counting(*a, **k): calls["n"] += 1; return real(*a, **k)
    with Env(w) as e:
        T.plan = counting
        try:
            e.mode("live"); e.cycle(); e.cycle(); e.cycle()
            assert calls["n"] == 1 and any("already through the entry" in x[2] for x in e.log(kinds=("skip",))), (calls, e.log())
            A.run_cycle(e.ex, scan=w.scan(), now=e.ex.clock + A.SKIP_RETRY_MS + 1000); assert calls["n"] == 2, "looked at again after 5 minutes"
            w.charts[("XYZUSDT", "60")]["price"] = 10.5
            A.run_cycle(e.ex, scan=w.scan(), now=e.ex.clock + 2 * A.SKIP_RETRY_MS + 2000); assert len(e.ex.posts()) == 1, "placed once the price is back above the entry"
        finally:
            T.plan = real
    print("  a setup that cannot be planned is looked at again after 5 minutes, not every pass")


def test_lagging_order_list_does_not_drop_an_order():
    with Env(one_setup()) as e:
        e.mode("live"); e.cycle(); link = e.tracked()[0]["link"]
        o = e.ex.orders.pop(link)                                                                                       # the realtime list lags: the order is not listed yet ...
        e.ex.history[link] = "New"                                                                                      # ... but the history still says New
        e.cycle(); assert e.tracked()[0]["status"] == "resting", e.tracked()
        e.ex.orders[link] = o; e.cycle(); assert e.tracked()[0]["status"] == "resting" and len(e.ex.posts()) == 1
    print("  an order the list does not show for a moment (status New) is not treated as gone")


def test_adopt_unknown_auto_order():
    with Env(one_setup()) as e:
        link = "sbgz-XYZUSDT-60-L-z6ac00001"
        e.ex.orders[link] = dict(orderId="x1", orderLinkId=link, symbol="XYZUSDT", side="Buy", price="10.000", qty="125.00", orderStatus="New", stopLoss="9.800",
                                 takeProfit="10.900", reduceOnly=False, createdTime=str(CLOCK0))
        e.mode("live"); e.cycle()
        t = e.tracked(symbol="XYZUSDT"); assert len(t) == 1 and t[0]["link"] == link and not e.ex.posts(), (t, e.ex.posts())
        assert t[0]["status"] == "resting" and A._ensure()["tracked"][link]["since"] == T0 + ARM * 3600 and link in e.ex.orders, t   # kept, not cancelled as 'ended' 
        e.cycle(); assert link in e.ex.orders and not e.ex.posts()
    w = World(); w.add("XYZUSDT", "60", 10.5)                                                                          # no setup on the coin any more: it goes
    with Env(w) as e:
        link = "sbgz-XYZUSDT-60-L-z6ac00001"
        e.ex.orders[link] = dict(orderId="x1", orderLinkId=link, symbol="XYZUSDT", side="Buy", price="10.000", qty="125.00", orderStatus="New", stopLoss="9.800",
                                 takeProfit="10.900", reduceOnly=False, createdTime=str(CLOCK0))
        e.mode("live"); e.cycle(); assert link not in e.ex.orders, "an adopted order whose setup is gone is cancelled"
    print("  an auto-tagged order missing from the list (lost reply) is taken over, kept while its setup lives, not duplicated")


def test_leaving_live_and_kill():
    w = World(); w.add("AAAUSDT", "60", 10.5, setups=[zone()]); w.add("BBBUSDT", "60", 10.5, setups=[zone(since_bar=ARM + 1)])
    with Env(w) as e:
        e.mode("live", max_new_per_cycle=5, btc_close=False); e.cycle()
        la = [t for t in e.tracked() if t["symbol"] == "AAAUSDT"][0]["link"]; lb = [t for t in e.tracked() if t["symbol"] == "BBBUSDT"][0]["link"]
        e.ex.fill(la); e.cycle()
        r = A.update_settings(dict(mode="preview"), e.ex); assert r["ok"] and r["note"]["cancelled"] == 1, r        # leaving live cancels the waiting order
        assert lb not in e.ex.orders and "AAAUSDT" in e.ex.positions and any("left live mode" in x[2] for x in e.log())
        e.mode("live"); e.cycle()
        k = A.kill(e.ex); assert k["ok"] and A.status(e.ex)["settings"]["mode"] == "off" and "AAAUSDT" in e.ex.positions, k
        n = len(e.ex.posts()); e.cycle(); assert len(e.ex.posts()) == n
        k = A.kill(e.ex, close_positions=True); assert k["closed"] == 1 and "AAAUSDT" not in e.ex.positions and e.ex.posts()[-1]["reduceOnly"], k
    print("  leaving live / kill cancel the waiting auto orders (open trades keep their stop and target; close_positions closes them)")


def test_store_persistence_and_unreadable_store():
    w = one_setup()
    with Env(w) as e:
        e.mode("live", risk_pct=0.5, btc_cancel=True); e.cycle(); link = e.tracked()[0]["link"]
        A.reset_for_tests()                                                                              # a restarted server reads the same state
        st = A.status(e.ex); assert st["settings"]["risk_pct"] == 0.5 and st["settings"]["btc_cancel"] and st["tracked"][0]["link"] == link, st
        e.cycle(); assert len(e.ex.posts()) == 1, "after a restart the same setup is not placed again"
        with open(A._store_file(), "w") as f: f.write("{not json")
        A.reset_for_tests(); n = len(e.ex.calls)
        assert not A.status(e.ex)["ok"] and not A.run_cycle(e.ex, scan=w.scan())["ok"] and len(e.ex.calls) == n, "unreadable store: nothing is traded"
        assert not A.update_settings(dict(mode="live"), e.ex, confirm=True)["ok"]
    print("  state survives a restart; an unreadable store means nothing is traded")


def test_only_one_server_trades_at_a_time():
    w = one_setup(); real = A._INSTANCE
    with Env(w) as e:
        e.mode("live"); A._INSTANCE = "server-A"
        try:
            r = e.cycle(); assert len(e.ex.posts()) == 1 and r["counts"]["placed"] == 1
            A._INSTANCE = "server-B"                                                              # a second server (a redeploy overlap, a local copy)
            w.add("OTHERUSDT", "60", 10.5, setups=[zone(since_bar=ARM + 5)])
            r = e.cycle(); assert "waits" in r["msg"] and len(e.ex.posts()) == 1, r                  # the lease is A's: B does nothing
            r = A.run_cycle(e.ex, scan=w.scan(), now=e.ex.clock + A.LEASE_MS + 1000)                  # A went silent: B takes over, with A's saved state
            assert r["ok"] and len(e.ex.posts()) == 2, (r, e.ex.posts())
            assert [t["symbol"] for t in e.tracked(status="resting")].count("XYZUSDT") == 1, "B knows A's order (state reloaded): no duplicate"
            A._INSTANCE = "server-A"
            r = A.run_cycle(e.ex, scan=w.scan(), now=e.ex.clock + A.LEASE_MS + 5000); assert "waits" in r["msg"] and len(e.ex.posts()) == 2, r
        finally:
            A._INSTANCE = real
    with Env(w) as e:                                                                                   # an unreadable lease: nothing is done
        e.mode("live")
        with open(A._store_file(A.LEASE_KEY), "w") as f: f.write("{broken")
        r = e.cycle(); assert not e.ex.posts() and "waits" in r["msg"], r
    print("  one server at a time: a second server waits, takes over (with the saved state) only after the first goes silent")


def test_account_size_20_usdt():
    w = World()
    for i in range(7): w.add(f"C{i}USDT", "60", 10.5, setups=[zone(since_bar=ARM + i)])
    with Env(w, cap=20.0) as e:                                                           # the account holds 10,000 but trades are sized on 20
        assert A.equity_cap() == 20.0 and T.equity_cap() == 20.0
        e.mode("live", risk_pct=1.0, max_new_per_cycle=5, max_resting=20, max_open_risk_pct=1.0)       # 1 % of 20 = 0.20 USDT; total risk 1 % of 20 = 0.20
        e.cycle(); b = e.ex.posts()
        assert len(b) == 1 and b[0]["qty"] == "1.00" and b[0]["price"] == "10.000", b                  # 0.20 USDT over a 0.2 stop distance
        assert any("account size (20 USDT)" in x[2] for x in e.log(kinds=("skip",))), e.log()           # a second order would pass the 1 % total-risk cap of 20
        st = A.status(e.ex); assert st["equity"] == dict(used=20.0, real=10_000.0) and st["settings"]["equity_usd"] == 20.0, st
        A.update_settings(dict(equity_usd=0, max_open_risk_pct=5.0), e.ex)                              # 0 = the account's real equity; takes effect at once
        assert A.equity_cap() == 0.0
        e.cycle(); new = e.ex.posts()[1:]
        assert new and all(x["qty"] == "500.00" for x in new), new                                      # 1 % of 10,000 = 100 USDT over 0.2
    print("  account size 20: orders and the total-risk cap are calculated on 20 USDT, not on the account's 10,000; 0 = the real equity")


def test_settings_without_a_mode_change_do_not_wait_for_a_pass():
    with Env(one_setup()) as e:
        e.mode("preview")
        assert A._cycle_lock.acquire(blocking=False)                                                    # pretend a pass is running
        try:
            t0 = time.time(); r = A.update_settings(dict(risk_pct=0.5, equity_usd=50), e.ex)
            assert r["ok"] and time.time() - t0 < 5 and A.status(e.ex)["settings"]["equity_usd"] == 50.0, r
        finally:
            A._cycle_lock.release()
    print("  filters / limits / account size are saved at once; only a mode change waits for a running pass")


def test_unreadable_store_still_sizes_on_20():
    with Env(one_setup()) as e:
        with open(A._store_file(), "w") as f: f.write("{broken")
        A.reset_for_tests()
        assert A.equity_cap() == 20.0 and T.equity_cap() == 20.0
    print("  an unreadable store means the account size is 20 (the smaller size), never the full account")


def test_trade_module_unchanged_behaviour():
    with Env(one_setup()) as e:                                                                          # the manual button still works the same
        p = T.plan(e.ex, "XYZUSDT", "60", "LONG", 0.5)
        assert p["ok"] and p["since"] == T0 + ARM * 3600 and p["price_str"]["entry"] == "10.000", p
        r = T.place(e.ex, p); assert r["placed"] and not r["order_link_id"].split("-")[4].startswith("z"), r
    print("  the manual order button is unchanged (its orders are not tagged auto)")


if __name__ == "__main__":
    fails = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
            except Exception:
                fails += 1
                print(f"FAIL {name}"); traceback.print_exc()
    print("all sbgz_auto tests passed" if not fails else f"{fails} sbgz_auto test(s) failed")
    sys.exit(1 if fails else 0)
