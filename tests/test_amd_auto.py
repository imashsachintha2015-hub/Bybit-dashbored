"""Tests for backend_lib/amd_auto.py (the AMD-FVG auto-orders).  Run:  python tests/test_amd_auto.py

Uses the fake exchange of tests/test_sbgz_auto.py (orders, positions, closed PnL like Bybit would); the Forward Lab's ledger, the prices
and the instrument limits are stubbed. Nothing here talks to Bybit, so no order can be sent.
"""
import os
import sys
import tempfile
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

from backend_lib import sbgz_trade as T, sbgz_auto as SA, amd_auto as M  # noqa: E402
import test_sbgz_auto as TS  # noqa: E402

CLOCK = TS.CLOCK0
HOUR = 3_600_000


def row(sym="XYZUSDT", side="LONG", entry=10.0, stop=9.8, target=10.4, status="PENDING", sig=1):
    return dict(key=f"AMD_NO_GATE|{sym}|{side}|{sig}", symbol=sym, side=side, signal_t=CLOCK - 2 * HOUR, entry=entry, stop=stop, target=target, status=status)


def short_row(sym="SHORTUSDT"):
    return row(sym, "SHORT", entry=10.0, stop=10.2, target=9.6)


class Env:
    def __init__(self, ex=None, price=10.5):
        self.ex, self.price = ex or TS.FakeEx(), price

    def __enter__(self):
        self.dir = tempfile.mkdtemp()
        self.saved = (SA._store_file, T.instrument, os.environ.get("SBGZ_AUTO_BACKEND"))
        os.environ["SBGZ_AUTO_BACKEND"] = "file"
        SA._store_file = lambda key=SA.STORE_KEY: os.path.join(self.dir, key + ".json")
        T.instrument = lambda sym, timeout=6: TS.SPEC
        M.reset_for_tests()
        return self

    def __exit__(self, *a):
        SA._store_file, T.instrument = self.saved[:2]
        if self.saved[2] is None: os.environ.pop("SBGZ_AUTO_BACKEND", None)
        else: os.environ["SBGZ_AUTO_BACKEND"] = self.saved[2]
        M.reset_for_tests()

    def mode(self, mode, **kw):
        r = M.update_settings(dict(mode=mode, **kw), self.ex, confirm=True); assert r["ok"], r; return r

    def cycle(self, rows, now=CLOCK, price=None):
        p = self.price if price is None else price
        return M.run_cycle(self.ex, ledger={r["key"]: r for r in rows}, now=now, price_of=lambda s: p if not isinstance(p, dict) else p.get(s))

    def tracked(self, **where):
        return [t for t in M.status(self.ex, full_log=True)["tracked"] if all(t.get(k) == v for k, v in where.items())]

    def log(self, kinds=None, n=50):
        return [(e["kind"], e["symbol"], e["text"]) for e in M.status(self.ex, full_log=True)["log"][:n] if not kinds or e["kind"] in kinds]

    def creates(self):
        return [c for c in self.ex.calls if c[0] == "POST" and c[1] == "/v5/order/create"]


def test_settings_validation():
    base = dict(M.DEFAULTS)
    assert base["mode"] == "off" and base["equity_usd"] == 20.0 and base["risk_pct"] == 2.0 and base["config"] == "AMD_NO_GATE"
    assert M.clean_settings(dict(risk_pct=1, max_open=2, config="AMD_PRIMARY"), base)["config"] == "AMD_PRIMARY"
    for bad in (dict(mode="on"), dict(risk_pct=0), dict(risk_pct=6), dict(equity_usd=-1), dict(max_open=0), dict(config="X"), dict(leverage=99), dict(hold_hours="x")):
        try:
            M.clean_settings(bad, base); raise AssertionError(f"accepted {bad}")
        except ValueError:
            pass
    assert M.clean_settings(dict(unknown=1), base) == base
    print("  settings: validated, bounded, off by default, 20 USDT account size")


def test_off_and_real_money_do_nothing():
    with Env() as e:
        e.cycle([row()]); assert not e.ex.calls, "off: no call at all"
        r = M.update_settings(dict(mode="live"), e.ex)
        assert not r["ok"] and r.get("need_confirm"), r
    with Env(TS.FakeEx(base_url="https://api.bybit.com")) as e:
        r = M.update_settings(dict(mode="live"), e.ex, confirm=True); assert not r["ok"] and "REAL" in r["error"], r
    print("  off by default: no call at all; going live needs a confirmation; real-money accounts are refused")


def test_places_the_order_sized_on_20_usdt():
    with Env() as e:
        e.mode("live"); e.cycle([row()])
        c = e.creates(); assert len(c) == 1, c
        b = c[0][3]
        assert b["orderType"] == "Limit" and b["timeInForce"] == "PostOnly" and b["side"] == "Buy", b
        assert b["price"] == "10.000" and b["stopLoss"] == "9.800" and b["takeProfit"] == "10.400" and b["tpslMode"] == "Partial" and b["slOrderType"] == "Market", b
        assert b["orderLinkId"].startswith("amd-"), b
        assert abs(float(b["qty"]) - 2.0) < 1e-9, b                                    # 2% of 20 USDT = 0.40 between 10.0 and 9.8 -> 2 units
        t = e.tracked(status="resting"); assert len(t) == 1 and abs(t[0]["risk_usd"] - 0.4) < 1e-6, t
        n = len(e.creates()); e.cycle([row()]); e.cycle([row()]); assert len(e.creates()) == n, "the same setup is not placed twice"
        e.cycle([short_row()], price=9.5); s = [c[3] for c in e.creates() if c[3]["symbol"] == "SHORTUSDT"]
        assert len(s) == 1 and s[0]["side"] == "Sell" and s[0]["stopLoss"] == "10.200", "shorts are the mirror"
    print("  places one post-only limit with stop and target, sized on 20 USDT, once per setup; shorts mirrored")


def test_skips():
    with Env() as e:
        e.mode("live")
        e.ex.orders["foreign"] = dict(orderId="f1", orderLinkId="manual-1", symbol="XYZUSDT", side="Buy", price="9", qty="1", orderStatus="New", reduceOnly=False)
        e.cycle([row()]); assert not e.creates(), "a coin with any order on it is left alone"
    with Env() as e:
        e.mode("live"); e.ex.positions["XYZUSDT"] = dict(symbol="XYZUSDT", side="Buy", size="1", avgPrice="10", createdTime=str(CLOCK))
        e.cycle([row()]); assert not e.creates(), "a coin with a position is left alone"
    with Env() as e:
        e.mode("live"); e.cycle([row()], price=9.9)                                      # price already below a buy limit at 10.0
        assert not e.creates() and any("already through the entry" in x[2] for x in e.log(("skip",))), e.log()
        e.cycle([row()], price=9.9); assert len([x for x in e.log(("skip",))]) == 1, "logged once"
    with Env() as e:
        e.mode("live"); e.cycle([row(status="CANCELLED"), row(sym="OPENUSDT", status="OPEN"), row(sym="DONEUSDT", status="CLOSED")])
        assert not e.creates(), "only PENDING setups are placed"
    with Env() as e:
        e.mode("live", equity_usd=1.0); e.cycle([row()]); assert not e.creates() and any("too small" in x[2] for x in e.log(("skip",))), e.log()
    print("  skips: coins with an order / position, price through the entry, setups that are not PENDING, orders too small for the account size")


def test_caps():
    rows = [row(sym=f"C{i}USDT", sig=i) for i in range(5)]
    with Env() as e:
        e.mode("live", max_resting=2, max_open=3); e.cycle(rows); e.cycle(rows)
        assert len(e.tracked(status="resting")) == 2, e.tracked()
    with Env() as e:
        e.mode("live", max_resting=5, max_open=1); e.cycle(rows)
        l = e.tracked(status="resting")[0]["link"]; e.ex.fill(l); e.cycle(rows); n = len(e.creates()); e.cycle(rows)
        assert len(e.creates()) == n and len(e.tracked(status="open")) == 1, "no new order while the open-trade cap is reached"
    print("  caps: waiting orders and open trades")


def test_ledger_ending_cancels_the_order():
    with Env() as e:
        e.mode("live"); e.cycle([row()]); link = e.tracked(status="resting")[0]["link"]
        e.cycle([row(status="EXPIRED")])
        assert not e.ex.orders and e.tracked(link=link)[0]["status"] == "cancelled" and any("ledger: expired" in x[2] for x in e.log(("cancel",))), e.log()
    with Env() as e:
        e.mode("live"); e.cycle([row()]); e.cycle([row(status="CLOSED")]); assert not e.ex.orders, "a setup whose paper trade is already over is not left resting"
    with Env() as e:
        e.mode("live"); e.cycle([row()]); e.cycle([row(status="OPEN")]); assert len(e.ex.orders) == 1, "a paper fill leaves the real order alone"
    print("  the order is cancelled when the ledger says the setup ended; left alone while the paper trade is open")


def test_fill_target_stop_and_result():
    with Env() as e:
        e.mode("live"); e.cycle([row()]); link = e.tracked(status="resting")[0]["link"]
        e.ex.fill(link); e.cycle([row(status="OPEN")])
        assert e.tracked(status="open") and any(x[0] == "fill" for x in e.log()), e.log()
        e.ex.hit("XYZUSDT", 10.4); e.cycle([row(status="CLOSED")])
        t = e.tracked(status="done")[0]; assert 1.9 < t["R"] < 2.0, t                    # +2R minus the fees
        assert not [o for o in e.ex.orders.values() if o.get("reduceOnly")], "leftover stop / target orders are removed"
        assert any(x[0] == "closed" and "target hit" in x[2] for x in e.log()), e.log()
        e.cycle([row(status="CLOSED")]); assert len(e.creates()) == 1, "the coin is not traded again for the same setup"
    with Env() as e:
        e.mode("live"); e.cycle([row()]); e.ex.fill(e.tracked(status="resting")[0]["link"]); e.cycle([row(status="OPEN")])
        e.ex.hit("XYZUSDT", 9.8); e.cycle([row(status="CLOSED")])
        t = e.tracked(status="done")[0]; assert -1.1 < t["R"] < -1.0 and any("stop hit" in x[2] for x in e.log()), (t, e.log())
    print("  fill -> open -> target (+2R) or stop (-1R) with the result booked after fees; leftovers removed; not placed again")


def test_time_exit():
    with Env() as e:
        e.mode("live"); e.cycle([row()]); e.ex.fill(e.tracked(status="resting")[0]["link"]); e.cycle([row(status="OPEN")])
        e.ex.price["XYZUSDT"] = 10.1
        e.cycle([row(status="OPEN")], now=CLOCK + 90 * HOUR); assert e.tracked(status="open"), "not yet"
        e.cycle([row(status="OPEN")], now=CLOCK + 97 * HOUR)
        c = [x[3] for x in e.creates() if x[3].get("reduceOnly")]; assert len(c) == 1 and c[0]["orderType"] == "Market" and c[0]["side"] == "Sell", c
        e.cycle([row(status="OPEN")], now=CLOCK + 97 * HOUR + 60_000)
        assert e.tracked(status="done"), e.tracked()
    print("  time exit: a market close 96 hours after the fill, then the result is booked")


def test_preview_sends_nothing_and_leaving_live_cancels():
    with Env() as e:
        e.mode("preview"); e.cycle([row()])
        assert not e.creates() and any("WOULD place" in x[2] for x in e.log(("place",))), e.log()
        e.cycle([row()]); assert len(e.log(("place",))) == 1, "once per setup"
    with Env() as e:
        e.mode("live"); e.cycle([row()]); assert e.ex.orders
        r = M.update_settings(dict(mode="off"), e.ex); assert r["ok"] and not e.ex.orders, r
        n = len(e.ex.calls); e.cycle([row(sym="NEWUSDT", sig=7)]); assert len(e.ex.calls) == n, "off: nothing happens"
    with Env() as e:
        e.mode("live"); e.cycle([row()]); e.ex.fill(e.tracked(status="resting")[0]["link"]); e.cycle([row(status="OPEN")])
        r = M.kill(e.ex, close_positions=True); assert r["ok"] and not e.ex.positions and M.status(e.ex)["settings"]["mode"] == "off", r
    print("  preview sends nothing; switching off cancels the waiting orders; kill can close the open trades")


def test_failures_fail_closed():
    with Env() as e:
        e.mode("live"); e.ex.fail["wallet"] = True; e.cycle([row()]); assert not e.creates()
    with Env() as e:
        e.mode("live"); e.ex.fail["create"] = True; e.cycle([row()])
        assert not e.tracked() and any("refused" in x[2] for x in e.log(("error",))), e.log()
        n = len(e.creates()); e.ex.fail["create"] = False; e.cycle([row()]); assert len(e.creates()) == n, "a refused order is not retried for 10 minutes"
        e.cycle([row()], now=CLOCK + 11 * 60_000); assert len(e.creates()) == n + 1
    print("  failures fail closed: no wallet -> nothing; a refused order is logged and retried after 10 minutes")


def test_state_survives_a_restart():
    with Env() as e:
        e.mode("live"); e.cycle([row()])
        M.reset_for_tests()                                                              # a new server process reads the saved state
        assert M.status(e.ex)["settings"]["mode"] == "live" and len(e.tracked(status="resting")) == 1
        n = len(e.creates()); e.cycle([row()]); assert len(e.creates()) == n, "no duplicate order after a restart"
    print("  state survives a restart: settings and tracked orders; no duplicate order")


if __name__ == "__main__":
    fails = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
            except Exception:
                fails += 1
                print(f"FAIL {name}"); traceback.print_exc()
    print("all amd_auto tests passed" if not fails else f"{fails} amd_auto test(s) failed")
    sys.exit(1 if fails else 0)
