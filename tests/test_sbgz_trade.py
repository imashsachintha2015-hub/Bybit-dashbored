"""Tests for backend_lib/sbgz_trade.py (the dashboard's SBGZ order button).  Run:  python tests/test_sbgz_trade.py

A fake Bybit client records every call; the setup and the instrument limits are stubbed. Nothing here talks to Bybit,
so no order can be sent.
"""
import os
import sys
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from backend_lib import sbgz, sbgz_trade as T  # noqa: E402

SPEC = dict(qty_step=0.01, min_qty=0.01, max_qty=1000.0, min_notional=5.0, tick=0.001, max_lev=25.0)
LONG = dict(side="LONG", strong=True, entry=10.12345, stop=9.87654, target=11.04321, vol_ok=True, bvol=2.5, break_vu=15.0,
            zone_top=10.12345, zone_bottom=9.9, since_bar=0)
SHORT = dict(side="SHORT", strong=True, entry=10.12345, stop=10.3701, target=9.2004, vol_ok=False, bvol=1.2, break_vu=14.0,
             zone_top=10.3, zone_bottom=10.12345, since_bar=0)


class FakeClient:
    def __init__(self, equity=1000.0, positions=(), orders=(), base_url="https://api-demo.bybit.com", create_ret=0, pos_ret=0):
        self.base_url, self.equity, self.create_ret, self.pos_ret = base_url, equity, create_ret, pos_ret
        self.positions, self.orders, self.calls = list(positions), list(orders), []

    def get_wallet_balance(self):
        return {"retCode": 0, "result": {"list": [{"totalEquity": str(self.equity)}]}}

    def get_positions(self, symbol=None):
        if self.pos_ret: return {"retCode": self.pos_ret, "retMsg": "api key expired"}
        return {"retCode": 0, "result": {"list": [p for p in self.positions if p["symbol"] == symbol]}}

    def get_open_orders(self, symbol=None):
        return {"retCode": 0, "result": {"list": [o for o in self.orders if symbol is None or o["symbol"] == symbol]}}

    def set_leverage(self, symbol, leverage):
        self.calls.append(("leverage", symbol, leverage))
        return {"retCode": 110043, "retMsg": "leverage not modified"}

    def signed_request(self, method, path, params=None, body=None):
        self.calls.append((method, path, params, body))
        if path == "/v5/order/create":
            return {"retCode": self.create_ret, "retMsg": "OK" if not self.create_ret else "PostOnly will take liquidity", "result": {"orderId": "oid-1"}}
        if path == "/v5/order/cancel": return {"retCode": 0, "retMsg": "OK", "result": {}}
        if path == "/v5/order/realtime": return {"retCode": 0, "result": {"list": self.orders}}
        return {"retCode": -1, "retMsg": "unexpected call"}

    def created(self):
        return [c[3] for c in self.calls if c[0] == "POST" and c[1] == "/v5/order/create"]


class Stub:
    """Swap in a fixed setup list, price and instrument spec for the duration of a with-block."""
    def __init__(self, setups, price, spec=SPEC):
        self.setups, self.price, self.spec = setups, price, spec

    def __enter__(self):
        self.get, self.inst = sbgz.get, T.instrument
        sbgz.get = lambda sym, interval="15", bars=1000, ttl=30: dict(
            ok=True, symbol=sym, interval=interval, setups=self.setups,
            candles=[dict(start=0, open=self.price, high=self.price, low=self.price, close=self.price, volume=1.0)])
        T.instrument = lambda sym, timeout=6: self.spec
        return self

    def __exit__(self, *a):
        sbgz.get, T.instrument = self.get, self.inst


def confirm_of(p):
    return dict(qty=p["qty_str"], **p["price_str"])


def test_rounding():
    assert T.floor_step(0.12399999999, 0.001) == 0.123 and T.floor_step(0.123, 0.001) == 0.123
    assert T.ceil_step(0.1231, 0.001) == 0.124 and T.ceil_step(0.123, 0.001) == 0.123
    assert T.round_step(10.12345, 0.001) == 10.123 and T.round_step(10.1235001, 0.001) == 10.124
    assert T.floor_step(127, 10) == 120 and T.fmt(120.0, 10) == "120" and T.fmt(20.24, 0.01) == "20.24"
    assert T.fmt(0.0000123, 0.0000001) == "0.0000123" and T._decimals(1e-11) == 11 and T._decimals(1) == 0
    print("  tick / qty-step rounding ok")


def test_sizing():
    q = T.size_qty(1000, 0.005, 10.123, 9.876, SPEC, 10)
    assert q == 20.24 and q * (10.123 - 9.876) <= 5.0, q                    # never more than the planned risk
    assert T.size_qty(10, 0.005, 10.123, 9.876, SPEC, 10) == 0.0             # 2 USDT notional < Bybit minimum 5 USDT
    q = T.size_qty(1000, 0.02, 10.123, 10.113, SPEC, 10)                     # tight stop: capped by equity x leverage
    assert q == 987.84 and q * 10.123 <= 10000, q                           # 1000 x 10 / 10.123 = 987.849 -> floored
    print("  risk sizing ok (risk cap, exchange minimum, leverage cap)")


def test_plan_long_and_short():
    with Stub([LONG, SHORT], 10.5):
        p = T.plan(FakeClient(), "BTCUSDT", "15", "LONG", 0.5)
    assert p["ok"], p
    assert (p["entry"], p["stop"], p["target"]) == (10.123, 9.876, 11.043), p   # stop rounded away, target towards
    assert p["price_str"] == dict(entry="10.123", stop="9.876", target="11.043") and p["qty_str"] == "20.24"
    assert p["risk_usd"] <= 5.0 and p["bybit_side"] == "Buy" and p["account"] == "DEMO" and p["exp_r"] == 0.36
    with Stub([LONG, SHORT], 10.0):
        s = T.plan(FakeClient(), "BTCUSDT", "60", "SHORT", 1.0)
    assert s["ok"], s
    assert (s["entry"], s["stop"], s["target"]) == (10.123, 10.371, 9.201), s
    assert s["bybit_side"] == "Sell" and not s["vol_ok"] and s["exp_r"] == 0.17
    assert s["qty"] * (s["stop"] - s["entry"]) <= 10.0
    print(f"  plan: LONG {p['qty_str']} @ {p['price_str']['entry']} SL {p['price_str']['stop']} TP {p['price_str']['target']}, "
          f"SHORT {s['qty_str']} @ {s['price_str']['entry']}")


def test_plan_refusals():
    with Stub([LONG], 10.5):
        assert "no armed" in T.plan(FakeClient(), "BTCUSDT", "15", "SHORT")["error"]
        assert "tested" in T.plan(FakeClient(), "BTCUSDT", "5", "LONG")["error"]
        assert "at most 2" in T.plan(FakeClient(), "BTCUSDT", "15", "LONG", 2.5)["error"]
        assert "REAL" in T.plan(FakeClient(base_url="https://api.bybit.com"), "BTCUSDT", "15", "LONG")["error"]
        os.environ["DASHBOARD_ALLOW_REAL_MONEY"] = "1"
        try:
            assert T.plan(FakeClient(base_url="https://api.bybit.com"), "BTCUSDT", "15", "LONG")["account"] == "REAL"
        finally:
            os.environ.pop("DASHBOARD_ALLOW_REAL_MONEY")
        assert "too small" in T.plan(FakeClient(equity=10), "BTCUSDT", "15", "LONG")["error"]
        assert "could not check" in T.plan(FakeClient(pos_ret=33004), "BTCUSDT", "15", "LONG")["error"]   # fail closed
        pos = dict(symbol="BTCUSDT", size="0.5", side="Buy")
        assert "already holding" in T.plan(FakeClient(positions=[pos]), "BTCUSDT", "15", "LONG")["error"]
        other = dict(symbol="BTCUSDT", side="Buy", price="9.5", qty="1", orderLinkId="")
        r = T.plan(FakeClient(orders=[other]), "BTCUSDT", "15", "LONG")
        assert not r["ok"] and "did not place" in r["error"] and not r.get("can_replace")
    with Stub([LONG], 10.1):                                                     # price already below a LONG entry
        assert "already through the entry" in T.plan(FakeClient(), "BTCUSDT", "15", "LONG")["error"]
    print("  plan refuses: no setup, untested TF, risk > 2%, real money, too small, unknown positions, "
          "open position, foreign order, missed entry")


def test_order_preview_confirm_place():
    c = FakeClient()
    with Stub([LONG], 10.5):
        pv = T.order(c, dict(symbol="BTCUSDT", interval="15", side="LONG", risk_pct=0.5))
        assert pv["ok"] and not pv["placed"] and not c.created(), "a request without preview=false must not place"
        assert not T.order(c, dict(symbol="BTCUSDT", interval="15", side="LONG", preview="false"))["placed"]
        ch = T.order(c, dict(symbol="BTCUSDT", interval="15", side="LONG", preview=False, confirm=dict(confirm_of(pv), entry="10.100")))
        assert ch["changed"] and not ch["placed"] and not c.created(), "numbers changed: confirm again"
        assert not T.order(c, dict(symbol="BTCUSDT", interval="15", side="LONG", preview=False))["placed"]
        done = T.order(c, dict(symbol="BTCUSDT", interval="15", side="LONG", risk_pct=0.5, preview=False, confirm=confirm_of(pv)))
    assert done["placed"] and done["order_id"] == "oid-1", done
    (b,) = c.created()
    assert b["orderType"] == "Limit" and b["timeInForce"] == "PostOnly" and b["side"] == "Buy"
    assert (b["qty"], b["price"], b["stopLoss"], b["takeProfit"]) == ("20.24", "10.123", "9.876", "11.043")
    assert b["tpslMode"] == "Partial" and b["tpOrderType"] == "Limit" and b["tpLimitPrice"] == b["takeProfit"]
    assert b["slOrderType"] == "Market" and b["tpTriggerBy"] == b["slTriggerBy"] == "LastPrice"
    assert b["orderLinkId"].startswith("sbgz-BTCUSDT-15-L-") and len(b["orderLinkId"]) <= 36
    assert ("leverage", "BTCUSDT", 10) in c.calls
    with Stub([LONG], 10.5):
        res = T.order(FakeClient(create_ret=170218), dict(symbol="BTCUSDT", interval="15", side="LONG", risk_pct=0.5,
                                                          preview=False, confirm=confirm_of(pv)))
    assert not res["placed"] and "Bybit refused" in res["error"], res
    assert "symbol" in T.order(FakeClient(), dict(symbol="BTC/USDT", side="LONG"))["error"]
    print("  order: preview by default, confirm must match, post-only limit entry + limit TP + stop-market SL, refusals passed on")


def test_replace_own_order_and_cancel():
    mine = dict(symbol="BTCUSDT", side="Buy", price="10.050", qty="20", orderLinkId="sbgz-BTCUSDT-15-L-66f00000")
    c = FakeClient(orders=[mine])
    with Stub([LONG], 10.5):
        r = T.order(c, dict(symbol="BTCUSDT", interval="15", side="LONG"))
        assert not r["ok"] and r["can_replace"] and r["resting"][0]["link"] == mine["orderLinkId"], r
        pv = T.order(c, dict(symbol="BTCUSDT", interval="15", side="LONG", replace=True))
        assert pv["ok"] and pv["replace"] == [mine["orderLinkId"]] and not c.created()
        done = T.order(c, dict(symbol="BTCUSDT", interval="15", side="LONG", replace=True, preview=False, confirm=confirm_of(pv)))
    assert done["placed"]
    paths = [x[1] for x in c.calls if x[0] == "POST"]
    assert paths.index("/v5/order/cancel") < paths.index("/v5/order/create"), "the old order is cancelled first"
    cb = next(x[3] for x in c.calls if x[1] == "/v5/order/cancel")
    assert cb == dict(category="linear", symbol="BTCUSDT", orderLinkId=mine["orderLinkId"])
    n = len(c.calls)
    assert not T.cancel(c, "BTCUSDT", "runner-123")["ok"] and len(c.calls) == n, "foreign orders are never cancelled"
    print("  own resting order: offered as a move, cancelled before the new one; foreign orders untouchable")


def test_open_orders_status():
    orders = [dict(symbol="BTCUSDT", side="Buy", price="10.123", qty="20", orderLinkId="sbgz-BTCUSDT-15-L-1"),
              dict(symbol="BTCUSDT", side="Buy", price="10.050", qty="20", orderLinkId="sbgz-BTCUSDT-60-L-2"),
              dict(symbol="BTCUSDT", side="Sell", price="10.5", qty="20", orderLinkId="sbgz-BTCUSDT-15-S-3"),
              dict(symbol="BTCUSDT", side="Buy", price="9", qty="1", orderLinkId="")]
    with Stub([LONG], 10.5):
        r = T.open_orders(FakeClient(orders=orders))
    assert r["ok"] and [o["setup"] for o in r["orders"]] == ["same", "moved", "gone"], r     # 1h order rests at 10.050
    with Stub([dict(LONG, entry=10.2)], 10.5):
        r = T.open_orders(FakeClient(orders=orders[:1]))
    assert r["orders"][0]["setup"] == "moved" and r["orders"][0]["setup_entry"] == 10.2
    print("  my orders: only sbgz- orders, each marked same / moved / gone")


if __name__ == "__main__":
    fails = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
            except Exception:
                fails += 1
                print(f"FAIL {name}"); traceback.print_exc()
    print("all sbgz_trade tests passed" if not fails else f"{fails} sbgz_trade test(s) failed")
    sys.exit(1 if fails else 0)
