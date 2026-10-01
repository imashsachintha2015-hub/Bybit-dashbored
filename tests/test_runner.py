"""Tests for the MASIS strategy runner.  Run:  python tests/test_runner.py   (or pytest tests/test_runner.py)

  * pure functions: sizing, rounding, statistics, signal rules, the research trade simulation
  * lifecycle with a fake exchange: entry, safety limits, fail-safes, reconcile, trailing, time exit,
    restart idempotency, auto-pause
  * parity with the research code on the downloaded history (skipped when scratch/ data is absent)

Nothing here talks to Bybit or Supabase: market data and the state store are replaced in memory.
"""
import os
import random
import sys
import tempfile
import time
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from daemons.runner import core, scans, snapback, trading, trend4h  # noqa: E402

H4, M15, H1 = core.H4, core.M15, core.H1


class _NoSleep:                      # the entry code polls for the fill with sleeps; tests do not need to wait
    sleep = staticmethod(lambda s: None)


trading.time = _NoSleep
STORE = {}
core.kv_get_json = lambda k, d=None: STORE.get(k, d)
core.kv_set_json = lambda k, v: STORE.__setitem__(k, v)


def set_time(ms):
    core._clock["off"] = ms - int(time.time() * 1000)
    core._clock["at"] = time.time() + 10 ** 9      # never resync from the network


def new_log():
    return core.Log(path=os.path.join(tempfile.mkdtemp(), "log.jsonl"), quiet=True)


def events(log, type_=None):
    import json
    rows = [json.loads(l) for l in open(log.path)] if os.path.exists(log.path) else []
    return [r for r in rows if type_ in (None, r["type"])]


class FakeEx:
    """Stands in for core.Exchange (live behaviour) with switches for every failure we guard against."""
    paper = False
    endpoint = "fake"

    def __init__(self, equity=2000.0):
        self.eq = equity
        self.pos, self.orders, self.tick, self.calls, self.links = {}, set(), {}, [], set()
        self.spec_ = {"qty_step": 0.001, "min_qty": 0.001, "max_qty": 1000.0, "min_notional": 5.0, "tick": 0.1, "max_lev": 50}
        self.reject, self.attach_stop, self.stop_set_ok, self.closed_rows, self.positions_none = None, True, True, {}, False

    def spec(self, sym): return dict(self.spec_)
    def ticker(self, sym): b, a = self.tick[sym]; return {"bid": b, "ask": a, "last": (a + b) / 2}
    def equity(self): return self.eq
    def positions(self): return None if self.positions_none else {k: dict(v) for k, v in self.pos.items()}
    def order_symbols(self): return set(self.orders)
    def ensure_leverage(self, sym): self.calls.append(("lev", sym))
    def paper_step(self): pass

    def market_order(self, sym, side, qty, sl, tp, link):
        self.calls.append(("order", sym, side, qty, sl, tp, link))
        if self.reject:
            return False, 10001, self.reject
        if link in self.links:
            return True, 110072, "duplicate"
        self.links.add(link)
        b, a = self.tick[sym]
        px = a if side == 1 else b
        self.pos[sym] = dict(side=side, size=qty, avg=px, stop=sl if self.attach_stop else None, tp=tp if self.attach_stop else None, value=qty * px)
        return True, 0, ""

    def set_stop(self, sym, sl=None, tp=None):
        self.calls.append(("stop", sym, sl, tp))
        if not self.stop_set_ok:
            return False
        if sym in self.pos:
            if sl is not None:
                self.pos[sym]["stop"] = sl
            if tp is not None:
                self.pos[sym]["tp"] = tp
        return True

    def close(self, sym, side, qty):
        self.calls.append(("close", sym, qty))
        self.pos.pop(sym, None)
        return True

    def closed_pnl(self, sym, since): return list(self.closed_rows.get(sym, []))

    def orders_of(self, kind): return [c for c in self.calls if c[0] == kind]


def make_ctx(equity=2000.0, price=(99.95, 100.05), syms=("BTCUSDT",)):
    set_time(1_790_000_000_000)
    state, log = core.State(store=False), new_log()
    ex = FakeEx(equity)
    for s in syms:
        ex.tick[s] = price
    ctx = core.Ctx(ex, state, log)
    ctx.gov.refresh()
    return ctx


# ─── pure functions ──────────────────────────────────────────────────────────
def test_sizing_and_rounding():
    sp = {"qty_step": 0.001, "min_qty": 0.001, "max_qty": 100, "min_notional": 5}
    q = core.size_qty(2000, 0.0025, 100.0, 98.0, sp, 3.0)          # risk $5 over a $2 stop -> 2.5
    assert abs(q - 2.5) < 1e-9, q
    assert core.size_qty(2000, 0.0025, 100.0, 100.0, sp, 3.0) == 0          # zero stop distance
    assert core.size_qty(2000, 0.0025, 100.0, 99.99, sp, 3.0) * 100 <= 2000 * 3.0 + 1e-6   # notional cap binds
    assert core.size_qty(10, 0.0025, 100.0, 98.0, sp, 3.0) == 0              # below min notional
    assert core.stop_price(98.07, 1, 0.1) == 98.0 and core.stop_price(102.03, -1, 0.1) == 102.1      # never tighter than raw
    assert core.target_price(104.07, 1, 0.1) == 104.0 and core.target_price(95.93, -1, 0.1) == 96.0  # never further than raw
    assert core.fmt_step(2.5, 0.001) == "2.500" and core.fmt_step(7, 1.0) == "7" and core.fmt_step(0.12345, 0.0001) == "0.1235"
    assert core.floor_step(0.3, 0.1) == 0.3                                   # float noise must not drop a step


def test_statistics():
    lo, hi = scans.wilson(60, 100)
    assert 0.50 < lo < 0.60 < hi < 0.70
    assert scans.wilson(0, 0) == (0.0, 1.0)
    s = scans.summarize([1.0, -1.0, 1.0, 1.0])
    assert s["n"] == 4 and s["win"] == 0.75 and abs(s["mean_R"] - 0.5) < 1e-9


def test_snapback_rules_and_cooldown():
    assert snapback.rules_pass(True, False, "A|B") and not snapback.rules_pass(False, False, "A|B")
    assert snapback.rules_pass(True, True, "A&B") and not snapback.rules_pass(True, False, "A&B")
    assert snapback.rules_pass(False, True, "B") and not snapback.rules_pass(True, False, "B")
    ctx = make_ctx()
    sb = snapback.Snapback(ctx, universe=["BTCUSDT"])
    t0 = 1_000_000_000_000
    assert sb._kept("BTCUSDT", 1, t0)
    assert not sb._kept("BTCUSDT", 1, t0 + 7 * M15)          # inside the 2h cooldown
    assert sb._kept("BTCUSDT", -1, t0 + M15)                  # the other side is independent
    assert sb._kept("BTCUSDT", 1, t0 + 8 * M15)               # the cooldown ran from the kept trigger, not the dropped one


def test_fresh_side():
    down, up = [False, True, True, False], [False, False, False, True]
    assert snapback.fresh_side(down, up, 1) == 1 and snapback.fresh_side(down, up, 2) == 0 and snapback.fresh_side(down, up, 3) == -1


def test_oi_change():
    look = lambda stamp: {10: 100.0, 6: 110.0}.get(int(stamp // H1))
    assert abs(snapback.oi_change_4h(look, 11 * H1) - (100 / 110 - 1)) < 1e-12
    assert snapback.oi_change_4h(lambda s: None, 11 * H1) is None


def _bars(prices, start=0):
    return [{"t": start + i * M15, "o": o, "h": h, "l": l, "c": c} for i, (o, h, l, c) in enumerate(prices)]


def test_simulate_trade_rules():
    flat = (100, 100.2, 99.8, 100)
    # next open 100, atr 1: target 101, stop 99
    b = _bars([flat, flat, (100, 101.5, 99.9, 101), flat])
    r, k = snapback.simulate(b, 0, 1, 1.0)
    assert k == 2 and abs(r - (1 - 0.0012 * 100)) < 1e-9, (r, k)      # target hit, 12 bps of fees = 0.12 R at this stop size
    b = _bars([flat, flat, (100, 101.5, 98.5, 100), flat])
    r, k = snapback.simulate(b, 0, 1, 1.0)
    assert r < -1.0 and k == 2                                         # both touched in one bar: the stop wins
    b = _bars([flat, flat, (100, 100.5, 98.0, 98.5), flat])
    r, _ = snapback.simulate(b, 0, -1, 1.0)                            # a short: the same bar hits target (below) first? low 98 <= 99 target
    assert r > 0.8
    b = _bars([flat] * 60)
    r, k = snapback.simulate(b, 0, 1, 1.0)
    assert k == 48 and abs(r + 0.12) < 1e-9                            # nothing touched: closes at the 48th bar, only fees


def test_trend_signal_and_trail():
    rnd = random.Random(7)
    bars, p = [], 100.0
    for i in range(700):
        p *= 1 + rnd.gauss(0.0008, 0.01)
        bars.append({"t": i * H4, "o": p, "h": p * 1.004, "l": p * 0.996, "c": p})
    x = trend4h.indicators(bars)
    fired = [i for i in range(300, 700) if trend4h.signal(x, i)]
    assert fired, "synthetic uptrend produced no pullback signal"
    i = fired[0]
    s = trend4h.signal(x, i)
    assert s == (1 if x["e50"][i] > x["e200"][i] else -1)
    # trailing stop only ever tightens
    ctx = make_ctx()
    t = trend4h.Trend4h(ctx, universe=["BTCUSDT"])
    p_ = {"sym": "BTCUSDT", "side": 1, "entry": 100.0, "stop": 90.0, "best": 100.0, "risk": 10.0}
    ctx.ex.pos["BTCUSDT"] = dict(side=1, size=1, avg=100.0, stop=90.0, tp=None, value=100)
    xx = {"c": [0, 110.0], "atr": [0, 2.0]}
    t._trail(p_, xx, 1, 1)
    assert p_["stop"] == 102.0 and p_["best"] == 110.0                  # 110 - 4*2
    xx = {"c": [0, 104.0], "atr": [0, 2.0]}
    t._trail(p_, xx, 1, 2)
    assert p_["stop"] == 102.0                                          # price fell back: the stop does not loosen
    ctx.ex.stop_set_ok = False
    xx = {"c": [0, 120.0], "atr": [0, 2.0]}
    t._trail(p_, xx, 1, 3)
    assert p_["stop"] == 102.0                                          # broker refused: our record keeps the old stop


# ─── lifecycle with the fake exchange ────────────────────────────────────────
def test_entry_places_stop_and_records():
    ctx = make_ctx()
    rec = trading.open_position(ctx, "trend4h", "BTCUSDT", 1, 2.5, 0.0, 1_790_000_000_000 - H4, meta={"group": "core20", "rules": ["pullback"]})
    assert rec and "BTCUSDT" in ctx.pos
    o = ctx.ex.orders_of("order")[0]
    assert o[2] == 1 and o[4] is not None and o[5] is None                # long, stop attached, no target (trend has none)
    assert len(o[6]) <= 36 and o[6].startswith("t4-BTC-")
    assert abs(rec["risk_usd"] - 2000 * core.CFG["risk_trend"]) < 0.01 * 2000 * core.CFG["risk_trend"] + 0.2, rec["risk_usd"]
    assert rec["stop"] == 97.5 and rec["group"] == "core20"                  # 100.05 - 2.5 = 97.55, rounded down to the 0.1 tick
    # snap-back style: stop and target both attached
    ctx2 = make_ctx()
    r2 = trading.open_position(ctx2, "snapback", "BTCUSDT", -1, 1.0, 1.0, 1_790_000_000_000 - M15)
    o2 = ctx2.ex.orders_of("order")[0]
    assert o2[4] == r2["stop"] and o2[5] == r2["target"]
    assert r2["stop"] > r2["entry"] > r2["target"]                          # short: stop above, target below


def test_safety_limits():
    ctx = make_ctx(syms=("BTCUSDT", "ETHUSDT"))
    ctx.ex.tick["ETHUSDT"] = (99.95, 100.05)
    bar = 1_790_000_000_000 - H4
    # halt switch
    core.STORE_BACKUP = dict(STORE)
    STORE[core.CONTROL_KEY] = {"halt": True, "note": "test"}
    ctx.gov.refresh()
    assert trading.open_position(ctx, "trend4h", "BTCUSDT", 1, 2.5, 0, bar) is None and not ctx.ex.orders_of("order")
    STORE[core.CONTROL_KEY] = {}
    ctx.gov.refresh()
    # a strategy pause
    ctx.state["paused"]["snapback"] = {"reason": "test", "since": 0, "auto": False}
    assert trading.open_position(ctx, "snapback", "BTCUSDT", 1, 1.0, 1.0, bar) is None
    ctx.state["paused"].clear()
    # a coin that already has a position or a resting order in the account
    ctx.ex.pos["ETHUSDT"] = dict(side=1, size=1, avg=100.0, stop=None, tp=None, value=100.0)
    ctx.gov.refresh()
    assert trading.open_position(ctx, "trend4h", "ETHUSDT", 1, 2.5, 0, bar) is None
    ctx.ex.pos.clear(); ctx.ex.orders.add("BTCUSDT"); ctx.gov.refresh()
    assert trading.open_position(ctx, "trend4h", "BTCUSDT", 1, 2.5, 0, bar) is None
    ctx.ex.orders.clear(); ctx.gov.refresh()
    # the account changed after the last refresh: the entry must notice (fresh view)
    ctx.ex.pos["BTCUSDT"] = dict(side=-1, size=1, avg=100.0, stop=None, tp=None, value=100.0)
    ctx.gov.at = 0.0
    assert trading.open_position(ctx, "trend4h", "BTCUSDT", 1, 2.5, 0, bar) is None, "entered on a stale account view"
    ctx.ex.pos.clear(); ctx.gov.refresh()
    # strategy open-risk cap
    core.CFG["max_open_trend"], saved = 0.0001, core.CFG["max_open_trend"]
    assert trading.open_position(ctx, "trend4h", "BTCUSDT", 1, 2.5, 0, bar) is None
    core.CFG["max_open_trend"] = saved
    # gross exposure cap
    core.CFG["max_gross_x"], saved = 0.01, core.CFG["max_gross_x"]
    assert trading.open_position(ctx, "trend4h", "BTCUSDT", 1, 2.5, 0, bar) is None
    core.CFG["max_gross_x"] = saved
    # unreadable account state blocks entries
    ctx.ex.positions_none = True; ctx.gov.refresh()
    assert trading.open_position(ctx, "trend4h", "BTCUSDT", 1, 2.5, 0, bar) is None and not ctx.ex.orders_of("order")
    ctx.ex.positions_none = False; ctx.gov.refresh()
    assert trading.open_position(ctx, "trend4h", "BTCUSDT", 1, 2.5, 0, bar) is not None     # and after all that, a clean entry works


def test_daily_loss_and_drawdown_halts():
    ctx = make_ctx()
    bar = 1_790_000_000_000 - H4
    ctx.ex.eq = 2000 * (1 - core.CFG["day_loss_halt"]) - 1
    ctx.gov.refresh()
    assert "daily loss" in ctx.gov.halted
    assert trading.open_position(ctx, "trend4h", "BTCUSDT", 1, 2.5, 0, bar) is None
    ctx2 = make_ctx()
    ctx2.acct["peak_equity"] = 3000.0
    ctx2.gov.refresh()                                                       # 2000 vs peak 3000 = -33% > 25%
    assert "drawdown" in ctx2.gov.halted and ctx2.acct["halt"].get("drawdown")
    ctx2.ex.eq = 3100.0
    ctx2.gov.refresh()
    assert "drawdown" in ctx2.gov.halted                                      # sticky until resumed by hand


def test_failed_order_and_unprotected_position():
    ctx = make_ctx()
    bar = 1_790_000_000_000 - H4
    ctx.ex.reject = "insufficient margin"
    assert trading.open_position(ctx, "trend4h", "BTCUSDT", 1, 2.5, 0, bar) is None and not ctx.pos
    ctx.ex.reject = None
    # filled without the stop attached and the broker refuses to set it -> must close the position
    ctx.ex.attach_stop, ctx.ex.stop_set_ok = False, False
    assert trading.open_position(ctx, "trend4h", "BTCUSDT", 1, 2.5, 0, bar) is None
    assert ctx.ex.orders_of("close") and "BTCUSDT" not in ctx.ex.pos and not ctx.pos
    assert events(ctx.log, "critical")
    # stop missing at first but the broker accepts it afterwards -> kept
    ctx2 = make_ctx()
    ctx2.ex.attach_stop = False
    rec = trading.open_position(ctx2, "trend4h", "BTCUSDT", 1, 2.5, 0, bar)
    assert rec and ctx2.ex.pos["BTCUSDT"]["stop"] == rec["stop"]


def test_duplicate_signal_cannot_open_twice():
    ctx = make_ctx()
    bar = 1_790_000_000_000 - H4
    assert trading.open_position(ctx, "trend4h", "BTCUSDT", 1, 2.5, 0, bar)
    ctx.ex.pos.clear(); ctx.pos.clear(); ctx.gov.refresh()                   # e.g. state lost in a crash; same signal again
    assert trading.open_position(ctx, "trend4h", "BTCUSDT", 1, 2.5, 0, bar) is None     # same client order id: rejected as duplicate -> no position
    assert len(ctx.ex.pos) == 0


def test_reconcile_books_exits():
    ctx = make_ctx()
    bar = 1_790_000_000_000 - H4
    rec = trading.open_position(ctx, "snapback", "BTCUSDT", 1, 1.0, 1.0, bar, meta={"group": "core20", "rules": ["A"]})
    # the broker's target fills: the position disappears and closed-pnl reports it
    ctx.ex.pos.pop("BTCUSDT")
    ctx.ex.closed_rows["BTCUSDT"] = [{"sym": "BTCUSDT", "exit": rec["target"], "pnl": rec["risk_usd"] * 0.9, "ts": core.now_ms()}]
    ctx.gov.refresh(); trading.reconcile(ctx)
    assert not ctx.pos
    h = ctx.state["history"][-1]
    assert h["how"] == "target" and abs(h["R_net"] - 0.9) < 0.01 and h["group"] == "core20"
    assert ctx.state["agg"]["snapback|live|core20"]["n"] == 1
    # closed-pnl not available yet: wait, then book as unknown after 3 minutes
    ctx = make_ctx()
    rec = trading.open_position(ctx, "trend4h", "BTCUSDT", 1, 2.5, 0, bar)
    ctx.ex.pos.pop("BTCUSDT"); ctx.gov.refresh(); trading.reconcile(ctx)
    assert ctx.pos, "booked before closed-pnl had a chance to appear"
    ctx.pos["BTCUSDT"]["gone_since"] = core.now_ms() - 4 * 60_000
    trading.reconcile(ctx)
    assert not ctx.pos and ctx.state["history"][-1]["R_net"] is None
    # somebody else changed the position: stop managing it, do not touch it
    ctx = make_ctx()
    trading.open_position(ctx, "trend4h", "BTCUSDT", 1, 2.5, 0, bar)
    ctx.ex.pos["BTCUSDT"]["size"] *= 2
    ctx.gov.refresh(); trading.reconcile(ctx)
    assert not ctx.pos and events(ctx.log, "external_change") and "BTCUSDT" in ctx.ex.pos


def test_snapback_time_exit():
    ctx = make_ctx()
    bar = 1_790_000_000_000 - M15
    trading.open_position(ctx, "snapback", "BTCUSDT", 1, 1.0, 1.0, bar)
    sb = snapback.Snapback(ctx, universe=["BTCUSDT"])
    sb.manage(core.now_ms() + 11 * H1)
    assert not ctx.ex.orders_of("close")
    sb.manage(core.now_ms() + 12 * H1 + 1000)
    assert ctx.ex.orders_of("close") and ctx.pos["BTCUSDT"]["exit_how"].startswith("time exit")


def _synthetic_signal_bars(seed=7):
    rnd = random.Random(seed)
    bars, p = [], 100.0
    for i in range(900):
        p *= 1 + rnd.gauss(0.0008, 0.01)
        bars.append({"t": i * H4, "o": p, "h": p * 1.004, "l": p * 0.996, "c": p})
    x = trend4h.indicators(bars)
    i = next(i for i in range(300, 900) if trend4h.signal(x, i) == 1)
    return bars[:i + 1]


def test_trend_process_is_idempotent_and_trails():
    ctx = make_ctx()
    bars = _synthetic_signal_bars()
    last = bars[-1]
    ctx.ex.tick["BTCUSDT"] = (last["c"] * 0.9995, last["c"] * 1.0005)
    trend4h.closed_bars = lambda sym, now, limit=1000: list(bars)
    t = trend4h.Trend4h(ctx, universe=["BTCUSDT"])
    now = last["t"] + H4 + 120_000
    set_time(now)
    ctx.gov.refresh()
    t.process(now, now // H4 * H4)
    assert len(ctx.pos) == 1 and len(ctx.ex.orders_of("order")) == 1
    t.process(now, now // H4 * H4)
    assert len(ctx.ex.orders_of("order")) == 1, "the same 4h bar was processed twice"
    # the next 4h bar closes higher: the stop trails up; the one after closes lower: it stays
    up = dict(last, t=last["t"] + H4, o=last["c"], h=last["c"] * 1.05, l=last["c"], c=last["c"] * 1.05)
    bars.append(up)
    now2 = up["t"] + H4 + 120_000
    set_time(now2); ctx.gov.refresh()
    old_stop = ctx.pos["BTCUSDT"]["stop"]
    t.process(now2, now2 // H4 * H4)
    new_stop = ctx.pos["BTCUSDT"]["stop"]
    assert new_stop > old_stop and ctx.ex.pos["BTCUSDT"]["stop"] == new_stop
    down = dict(up, t=up["t"] + H4, o=up["c"], h=up["c"], l=up["c"] * 0.97, c=up["c"] * 0.98)
    bars.append(down)
    now3 = down["t"] + H4 + 120_000
    set_time(now3); ctx.gov.refresh()
    t.process(now3, now3 // H4 * H4)
    assert ctx.pos["BTCUSDT"]["stop"] == new_stop


def test_stale_signal_is_not_traded():
    ctx = make_ctx()
    bars = _synthetic_signal_bars(seed=11)
    trend4h.closed_bars = lambda sym, now, limit=1000: list(bars)
    t = trend4h.Trend4h(ctx, universe=["BTCUSDT"])
    now = bars[-1]["t"] + H4 + 3 * 3_600_000               # the runner was down: three hours late
    set_time(now); ctx.gov.refresh()
    t.process(now, now // H4 * H4)
    assert not ctx.pos and not ctx.ex.orders_of("order")


def test_state_roundtrip_and_resume_archive():
    STORE.clear()
    st = core.State(store=True)
    st["positions"]["BTCUSDT"] = {"strategy": "trend4h", "sym": "BTCUSDT"}
    st["agg"]["snapback|live|core20"] = {"n": 3, "wins": 1, "sum": -1.0, "sum2": 3.0}
    st.save()
    st2 = core.State(store=True)
    assert st2["positions"]["BTCUSDT"]["strategy"] == "trend4h" and "paper_positions" in st2.d
    core.set_control(resume={"snapback": 123})
    st2["paused"]["snapback"] = {"reason": "x", "since": 0, "auto": True}
    core.apply_control(st2, new_log())
    assert "snapback" not in st2["paused"] and any(k.startswith("archived|") for k in st2["agg"]) and "snapback|live|core20" not in st2["agg"]
    STORE.clear()


def test_edge_monitor_pauses_only_failing_live_strategies():
    st = core.State(store=False)
    st["agg"]["snapback|live|core20"] = {"n": 50, "wins": 15, "sum": -12.0, "sum2": 60.0}     # 30% wins: clearly broken
    st["agg"]["trend4h|live|core20"] = {"n": 120, "wins": 40, "sum": 10.0, "sum2": 200.0}     # fine
    _, pauses = scans.edge_check(st, {"trend4h": "live", "snapback": "live"}, new_log())
    assert [p[0] for p in pauses] == ["snapback"]
    _, pauses = scans.edge_check(st, {"trend4h": "live", "snapback": "paper"}, new_log())
    assert pauses == [], "paper strategies must never be auto-paused"
    st["agg"]["snapback|live|core20"] = {"n": 20, "wins": 6, "sum": -5.0, "sum2": 25.0}        # too few trades to judge
    _, pauses = scans.edge_check(st, {"snapback": "live"}, new_log())
    assert pauses == []


def test_paper_exchange_fees_and_stop_priority():
    set_time(1_790_000_000_000)
    state, log = core.State(store=False), new_log()
    ex = core.Exchange(True, state, log)
    ctx = core.Ctx(ex, state, log)
    ex.ticker = lambda sym: {"bid": 99.95, "ask": 100.05, "last": 100.0}
    ex.spec = lambda sym: {"qty_step": 0.001, "min_qty": 0.001, "max_qty": 1e6, "min_notional": 5.0, "tick": 0.1, "max_lev": 50}
    ctx.gov.refresh()
    rec = trading.open_position(ctx, "snapback", "BTCUSDT", 1, 1.0, 1.0, 1_790_000_000_000 - M15)
    assert rec and ctx.mode == "paper" and "BTCUSDT" in state["paper_positions"] and not state["positions"]
    # one 1m candle touching both stop and target: the stop wins
    core.pub_get = lambda path, **q: {"result": {"list": [[str(core.now_ms()), "100", str(rec["target"] + 1), str(rec["stop"] - 1), "100", "1", "1"]]}}
    ex.paper_step()
    assert "BTCUSDT" not in state["paper"]["pos"]
    c = state["paper"]["closed"][-1]
    assert c["how"] == "stop" and c["exit"] == rec["stop"]
    gross = (rec["stop"] - rec["entry"]) * rec["qty"]
    assert c["pnl"] < gross, "paper exit charged no fees"
    ctx.gov.refresh(); trading.reconcile(ctx)
    assert state["history"][-1]["paper"] and state["history"][-1]["R_net"] < -1.0       # a full stop plus fees is worse than -1R
    assert state["agg"]["snapback|paper|-"]["n"] == 1
    core.pub_get = _real_pub_get


_real_pub_get = core.pub_get


def test_universes_and_groups():
    assert len(trend4h.coins()) == 42 and len(set(trend4h.coins())) == 42
    assert snapback.coins(live=True) == core.CORE20 and len(snapback.coins(live=False)) == 42
    assert core.group_of("BTCUSDT") == "core20" and core.group_of("TIAUSDT") == "extra22"


# ─── parity with the research code (needs scratch/ data; skipped otherwise) ──
def _research():
    sc = os.path.join(ROOT, "scratch")
    need = [os.path.join(sc, d) for d in ("kline_long", "kline_old", "deriv_old", "deriv_data")]
    if not all(os.path.isdir(p) for p in need) or not os.path.exists(os.path.join(sc, "snapback_lab.py")):
        return None
    sys.path.insert(0, sc)
    import snapback_lab as L
    import research_smc as SM
    import research_trend as RT
    return L, SM, RT


def test_parity_trend_signal():
    r = _research()
    if r is None:
        print("   (skipped: research data not present)")
        return
    L, SM, RT = r
    import numpy as np
    checked = diffs = 0
    for sym in ("BTCUSDT", "SOLUSDT", "DOGEUSDT"):
        d = SM.load(sym)
        R4 = SM.resample(d, 16)
        dd = dict(t=R4["t"], o=R4["o"], h=R4["h"], l=R4["l"], c=R4["c"], ft=d["ft"], fr=np.diff(d["fcs"]))
        RT.prep(dd, 30)
        bars = [{"t": int(t), "o": o, "h": h, "l": l, "c": c} for t, o, h, l, c in zip(R4["t"], R4["o"], R4["h"], R4["l"], R4["c"])]
        for i in range(1100, len(bars), 5):
            x = trend4h.indicators(bars[i - 999:i + 1])         # the 1000-candle window the live runner sees
            checked += 1
            diffs += trend4h.signal(x, 999) != RT.entry_signal(dd, i, "pullback")
    assert checked > 1000 and diffs == 0, (checked, diffs)


def test_parity_snapback_events_and_trades():
    r = _research()
    if r is None:
        print("   (skipped: research data not present)")
        return
    L, SM, RT = r
    import numpy as np
    btc = L.btc_ctx(L.load_coin("BTCUSDT"))
    d_btc = L.load_coin("BTCUSDT")
    R1b = SM.resample(d_btc, 4)
    btc1 = [{"t": int(t), "o": o, "h": h, "l": l, "c": c} for t, o, h, l, c in zip(R1b["t"], R1b["o"], R1b["h"], R1b["l"], R1b["c"])]
    total = same = 0
    for sym in ("ETHUSDT", "NEARUSDT"):
        d = L.load_coin(sym)
        ev = L.snap_events(d, btc)
        R1, R4 = SM.resample(d, 4), SM.resample(d, 16)
        mk = lambda R: [{"t": int(t), "o": o, "h": h, "l": l, "c": c} for t, o, h, l, c in zip(R["t"], R["o"], R["h"], R["l"], R["c"])]
        b15 = [{"t": int(t), "o": o, "h": h, "l": l, "c": c} for t, o, h, l, c in zip(d["t"], d["o"], d["h"], d["l"], d["c"])]
        pairs = list(zip(d["dv"]["oi_t"].tolist(), d["dv"]["oi_v"].tolist()))
        live = snapback.replay_events(b15, snapback.Htf(mk(R1), mk(R4)), snapback.BtcCalm(btc1), scans.oi_lookup_from(pairs))
        t_min, t_max = d["t"][0] + 70 * 86_400_000, d["t"][-1] - 70 * M15
        live_set = {(b15[i]["t"], r_["side"]): r_ for i, r_ in live if t_min <= b15[i]["t"] <= t_max}
        lab_set = {}
        for t_, s_, a_, b_, R_ in zip(ev["t"], ev["side"], ev["A"], ev["B"], ev["R"]):
            if (a_ or b_) and t_min <= t_ <= t_max:
                lab_set[(int(t_), int(s_))] = float(R_)
        assert lab_set, "no research events in range"
        missing, extra = set(lab_set) - set(live_set), set(live_set) - set(lab_set)
        assert len(missing) + len(extra) <= max(1, 0.01 * len(lab_set)), (sym, len(lab_set), sorted(missing)[:3], sorted(extra)[:3])
        idx = {b["t"]: i for i, b in enumerate(b15)}
        for key in sorted(set(lab_set) & set(live_set)):
            i = idx[key[0]]
            sim = snapback.simulate(b15, i, key[1], live_set[key]["atr1h"])
            total += 1
            same += abs(sim[0] - lab_set[key]) < 1e-6
    assert total > 30 and same == total, (same, total)      # every compared trade has exactly the research R


def main():
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        t0 = time.time()
        try:
            fn()
            print(f"PASS {name} ({time.time() - t0:.1f}s)")
        except Exception:
            failed += 1
            print(f"FAIL {name}")
            traceback.print_exc()
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
