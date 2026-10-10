"""Unit tests for the Kalman Trend mode: backend_lib/kalman_trend.py (rules, sizing, settings) and
daemons/kalman_trend_engine.py (paper trading on a synthetic market). No network, no research data.
Run: python3 -m unittest tests/test_kalman_trend.py
Parity with the research backtest is checked separately in research_archive/live_module/."""
import json, math, os, random, sys, tempfile, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from backend_lib import kalman_trend as K
import daemons.kalman_trend_engine as KE

H4, DAY, HOUR = K.H4, K.DAY, 3600 * 1000
T0 = 1735689600000  # 2025-01-01 00:00 UTC


def series(n, drift_fn, seed=1, start=100.0, vol=0.01):
    rnd = random.Random(seed); c = [start]
    for i in range(1, n): c.append(c[-1] * math.exp(drift_fn(i) + rnd.gauss(0, vol)))
    return c


class Rules(unittest.TestCase):
    def test_kalman_follows_trend(self):
        up = series(600, lambda i: 0.004); dn = series(600, lambda i: -0.004)
        self.assertGreater(K.kalman(up)[0][-1], 1.0)
        self.assertLess(K.kalman(dn)[0][-1], -1.0)
        z, level = K.kalman(up)
        self.assertTrue(all(math.isnan(x) for x in z[:K.SPAN + 1]))
        self.assertAlmostEqual(level[-1] / up[-1], 1.0, delta=0.05)          # the filtered level tracks the price

    def test_kalman_is_antisymmetric(self):
        c = series(400, lambda i: 0.002 * math.sin(i / 40))
        z, _ = K.kalman(c); zm, _ = K.kalman([-x for x in c])                  # mirrored prices give exactly -z
        self.assertTrue(all((math.isnan(a) and math.isnan(b)) or a == -b for a, b in zip(z, zm)))

    def test_crossing(self):
        self.assertEqual(K.crossing(0.5, 1.2), 1)
        self.assertEqual(K.crossing(1.0, 1.01), 1)
        self.assertEqual(K.crossing(1.2, 1.3), 0)
        self.assertEqual(K.crossing(-0.9, -1.1), -1)
        self.assertEqual(K.crossing(float("nan"), 2.0), 0)

    def test_entry_rules(self):
        self.assertTrue(K.entry_allowed("L", 1, None)[0])
        self.assertTrue(K.entry_allowed("L", 1, 0.0003)[0])
        self.assertFalse(K.entry_allowed("L", 1, 0.00031)[0])
        self.assertFalse(K.entry_allowed("L", 0, 0.0)[0])
        self.assertTrue(K.entry_allowed("S", -1, 0.0001)[0])
        self.assertFalse(K.entry_allowed("S", -1, 0.0)[0])
        self.assertFalse(K.entry_allowed("S", -1, None)[0])
        self.assertFalse(K.entry_allowed("S", 1, 0.001)[0])

    def test_stop_and_exit(self):
        self.assertAlmostEqual(K.initial_stop("L", 100.0, 2.0, 100.0), 94.0)
        self.assertAlmostEqual(K.initial_stop("S", 100.0, 2.0, 100.0), 106.0)
        self.assertAlmostEqual(K.initial_stop("L", 100.0, 0.1, 100.0), 99.5)  # widened to 0.5%
        self.assertAlmostEqual(K.initial_stop("S", 100.0, 0.1, 100.0), 100.5)
        self.assertTrue(K.exit_now("L", -0.01)); self.assertFalse(K.exit_now("L", 0.3))
        self.assertTrue(K.exit_now("S", 0.01)); self.assertFalse(K.exit_now("S", -0.3))

    def test_funding_average(self):
        t8 = [T0 + k * 8 * HOUR for k in range(30)]; r8 = [0.0001 * (k % 3) for k in range(30)]
        close = t8[20] + 2 * HOUR
        self.assertAlmostEqual(K.funding_avg(t8, r8, close), sum(r8[12:21]) / 9)   # the last 9 payments
        t1 = [T0 + k * HOUR for k in range(200)]; r1 = [0.00001] * 200
        self.assertAlmostEqual(K.funding_avg(t1, r1, t1[150]), 72 * 0.00001 / 9)   # hourly funding: 8h-equivalent
        self.assertIsNone(K.funding_avg(t8, r8, t8[3]))                            # history too short
        self.assertAlmostEqual(K.funding_paid(t8, r8, "S", t8[0], t8[5]), -sum(r8[1:6]))

    def test_order_qty(self):
        q, risk, note = K.order_qty(1000, 0.5, 100.0, 95.0, 0.01, 0.01, 5.0, 2.0)
        self.assertAlmostEqual(q, 1.0); self.assertAlmostEqual(risk, 5.0); self.assertEqual(note, "ok")
        q, risk, note = K.order_qty(10, 0.5, 80000.0, 77000.0, 0.001, 0.001, 5.0, 2.0)
        self.assertEqual(q, 0.0); self.assertIn("minimum", note)               # 0.001 BTC would risk 30% of $10
        q, risk, note = K.order_qty(200, 0.5, 2.0, 1.9, 1.0, 1.0, 5.0, 2.0)    # $1 risk / 0.1 = 10 units ($20, above the minimum)
        self.assertEqual(q, 10.0); self.assertEqual(note, "ok")
        q, risk, note = K.order_qty(50, 0.5, 2.0, 1.9, 1.0, 1.0, 5.0, 2.0)     # wants 2 units ($4 < $5): raised to 3 units = 0.6% risk
        self.assertEqual(q, 3.0); self.assertAlmostEqual(risk, 0.3); self.assertIn("minimum", note)

    def test_settings(self):
        s = K.clean_settings({"riskPct": 9, "maxOpen": 0, "live": "true", "oiFilter": 1, "bogus": 3, "leverage": "7"})
        self.assertEqual(s["riskPct"], 2.0); self.assertEqual(s["maxOpen"], 1); self.assertIs(s["live"], True)
        self.assertIs(s["oiFilter"], True); self.assertNotIn("bogus", s); self.assertEqual(s["leverage"], 7)
        self.assertEqual(K.clean_settings({})["riskPct"], 0.5)

    def test_daily_trend_uses_completed_days(self):
        n = 6 * 120; t = [T0 + i * H4 for i in range(n)]; c = [100 * math.exp(0.002 * i) for i in range(n)]
        tr = K.daily_trend(t, c)
        self.assertEqual(tr[6 * 50 - 1], 0)               # the first 50 complete days carry no trend
        self.assertEqual(tr[-1], 1)
        c2 = c[:6 * 100] + [c[6 * 100 - 1] * math.exp(-0.01 * k) for k in range(1, n - 6 * 100 + 1)]
        tr2 = K.daily_trend(t, c2)
        self.assertEqual(tr2[6 * 100], 1)                 # the first bar of a new day still sees the previous day's trend
        self.assertEqual(tr2[-1], -1)

    def test_live_permission(self):
        self.assertFalse(K.live_permission({})[0])
        self.assertTrue(K.live_permission({"KALMAN_LIVE": "1"})[0])                          # demo endpoint by default
        self.assertTrue(K.live_permission({"KALMAN_LIVE": "1", "BYBIT_BASE_URL": "https://api-testnet.bybit.com"})[0])
        ok, why = K.live_permission({"KALMAN_LIVE": "1", "BYBIT_BASE_URL": "https://api.bybit.com"})
        self.assertFalse(ok); self.assertIn("MASIS_ALLOW_REAL_MONEY", why)                  # real money: as for the runner
        self.assertTrue(K.live_permission({"KALMAN_LIVE": "1", "BYBIT_BASE_URL": "https://api.bybit.com", "MASIS_ALLOW_REAL_MONEY": "1"})[0])

    def test_oi_and_correlation(self):
        pts = [(T0 + h * HOUR, 100.0 + (50 if h > 700 else 0)) for h in range(760)]
        self.assertTrue(K.oi_above_mean(pts, T0 + 740 * HOUR))
        self.assertIsNone(K.oi_above_mean(pts[:300], T0 + 290 * HOUR))
        a = {T0 + i * H4: math.sin(i) for i in range(180)}; b = dict(a); cneg = {k: -v for k, v in a.items()}
        self.assertTrue(K.corr_cap_blocks(a, [b, b, b])); self.assertFalse(K.corr_cap_blocks(a, [b, b, cneg]))


class FakeMarket:
    """Bybit-like data for 4 coins: a regime-switching drift per coin, hourly-aligned 4H bars, 8h funding."""
    base = "fake"
    def __init__(self, clock, n=2600):
        self.clock = clock; self.data = {}
        for j, s in enumerate(["BTCUSDT", "AAAUSDT", "BBBUSDT", "CCCUSDT"]):
            c = series(n, lambda i, j=j: 0.004 * math.sin(i / (90 + 15 * j)), seed=j + 3, vol=0.012)
            o = [c[0]] + c[:-1]
            self.data[s] = [[T0 + i * H4, o[i], max(o[i], c[i]) * 1.004, min(o[i], c[i]) * 0.996, c[i]] for i in range(n)]
        self.fund = {s: [(T0 + k * 8 * HOUR, 0.0001 if (k // 30) % 2 == 0 else -0.00005) for k in range(n // 2)] for s in self.data}
    def instruments(self): return {s: dict(status="Trading", qty_step=0.001, min_qty=0.001, min_notional=5, tick=1e-4, funding_min=480) for s in self.data}
    def klines_4h(self, sym, bars):
        now = self.clock(); forming = now // H4 * H4
        rows = [list(b) for b in self.data[sym] if b[0] <= forming][-bars:]
        if rows and rows[-1][0] == forming: rows[-1] = [rows[-1][0]] + [rows[-1][1]] * 4
        return rows
    def funding(self, sym, limit=200): return [f for f in self.fund[sym] if f[0] <= self.clock()][-limit:]
    def open_interest_1h(self, sym, hours=760): return []
    def tickers(self): return {}


class Clock:
    def __init__(self, t): self.t = t
    def __call__(self): return self.t


class LiveEnv:
    """KALMAN_LIVE=1 on the demo endpoint (or the given variables) and no sleeping, restored afterwards"""
    def __init__(self, **env):
        self.env = {"KALMAN_LIVE": "1", "BYBIT_BASE_URL": "https://api-demo.bybit.com", "MASIS_ALLOW_REAL_MONEY": None, **env}
    def __enter__(self):
        self.old = {k: os.environ.get(k) for k in self.env}; self.sleep = KE.time.sleep
        for k, v in self.env.items():
            if v is None: os.environ.pop(k, None)
            else: os.environ[k] = v
        KE.time.sleep = lambda s: None
        return self
    def __exit__(self, *a):
        KE.time.sleep = self.sleep
        for k, v in self.old.items():
            if v is None: os.environ.pop(k, None)
            else: os.environ[k] = v


class EngineBase(unittest.TestCase):
    def live_engine(self, settings=None, mode="kalman"):
        """an engine with the live switch on, trading the fake exchange (build it inside LiveEnv)"""
        tmp = tempfile.mkdtemp()
        KE.SNAPSHOT_PATH = os.path.join(tmp, "kalman_trend_live.json"); KE.IND_DIR = os.path.join(tmp, "kalman"); KE.HISTORY_PATH = os.path.join(tmp, "kalman_history.json")
        clock = Clock(T0 + 1200 * H4 + 60 * 1000); market = FakeMarket(clock); ex = FakeBybit(market)
        eng = KE.KalmanTrendEngine(market=market, store=KE.Store(os.path.join(tmp, "kt.db")),
                                   settings_source=lambda: settings or {"live": True, "riskPct": 0.5}, mode_source=lambda: mode,
                                   clock=clock, live_client=ex, symbols=list(market.data), write_files=False)
        return eng, ex, clock

    def run_until(self, eng, clock, cond, limit=900):
        for _ in range(limit):
            if cond(): return True
            eng.step(); clock.t += H4
        return cond()


class EngineTest(EngineBase):
    def run_engine(self, mode, bars=900):
        tmp = tempfile.mkdtemp()
        KE.SNAPSHOT_PATH = os.path.join(tmp, "kalman_trend_live.json"); KE.IND_DIR = os.path.join(tmp, "kalman"); KE.HISTORY_PATH = os.path.join(tmp, "kalman_history.json")
        clock = Clock(T0 + 1200 * H4 + 60 * 1000); market = FakeMarket(clock)
        eng = KE.KalmanTrendEngine(market=market, store=KE.Store(os.path.join(tmp, "kt.db")),
                                   settings_source=lambda: {"paperStartEquity": 10.0}, mode_source=lambda: mode,
                                   clock=clock, symbols=list(market.data), write_files=True)
        for _ in range(bars):
            eng.step(); clock.t += H4
        return eng, tmp

    def test_paper_mode_trades_and_writes_files(self):
        eng, tmp = self.run_engine("kalman")
        pos = eng.store.positions()
        self.assertGreater(len(pos), 3, "paper trades were expected on the synthetic trends")
        self.assertTrue(all(p["mode"] == "PAPER" for p in pos))
        closed = [p for p in pos if p["status"] == "CLOSED"]
        self.assertTrue(closed and all(p["r_net"] is not None and p["reason"] in ("STOP", "TREND_FLIP", "TIME") for p in closed))
        self.assertLessEqual(max(sum(1 for q in pos if q["entry_t"] <= p["entry_t"] and (q["exit_t"] or 9e15) > p["entry_t"]) for p in pos), 8)
        eq = eng.store.get("paper_equity"); booked = sum(p["pnl_usd"] for p in closed if p["booked"])
        self.assertAlmostEqual(eq, 10.0 + booked, places=9)
        self.assertTrue(os.path.exists(KE.SNAPSHOT_PATH)); self.assertTrue(os.path.exists(os.path.join(KE.IND_DIR, "AAAUSDT.json")))
        self.assertGreater(eng.store.shadow_stats()["n"], 0)
        with open(KE.HISTORY_PATH) as f: h = json.load(f)                      # the day filter's data
        self.assertEqual(len(h["trades"]), len(closed))
        self.assertTrue(all(t["exit_t"] and t["r_net"] is not None for t in h["trades"]))
        self.assertTrue(h["signals"] and any(x["taken"] for x in h["signals"]) and any(not x["taken"] for x in h["signals"]))
        taken = {(x["bar_t"], x["sym"]) for x in h["signals"] if x["taken"]}
        self.assertEqual(len(taken), len(pos))                                  # every position traces back to a recorded signal
        self.assertTrue(h["events"] and all(e["msg"] for e in h["events"]))

    def test_restart_rewrites_dashboard_files_without_new_trades(self):
        eng, tmp = self.run_engine("kalman", bars=700)
        before = [(p["id"], p["status"]) for p in eng.store.positions()]
        shadow = eng.store.shadow_stats()["n"]
        os.remove(KE.SNAPSHOT_PATH); os.remove(KE.HISTORY_PATH)                       # a redeploy wipes scratch/, the database survives
        clock = eng.clock; market = eng.m
        eng2 = KE.KalmanTrendEngine(market=market, store=KE.Store(eng.store.path), settings_source=lambda: {"paperStartEquity": 10.0},
                                    mode_source=lambda: "kalman", clock=clock, symbols=list(market.data), write_files=True)
        clock.t -= H4                                                                   # still inside the bar that was already processed
        eng2.step()                                                                     # not a new bar ...
        self.assertTrue(os.path.exists(KE.SNAPSHOT_PATH) and os.path.exists(KE.HISTORY_PATH))   # ... but the files are back
        self.assertEqual(before, [(p["id"], p["status"]) for p in eng2.store.positions()])       # and nothing was traded twice
        self.assertEqual(shadow, eng2.store.shadow_stats()["n"])

    def test_standby_records_only(self):
        eng, tmp = self.run_engine("championship", bars=500)
        self.assertEqual(eng.store.positions(), [])
        self.assertGreater(eng.store.shadow_stats()["n"], 0)

    def test_live_mode_with_fake_exchange(self):
        with LiveEnv():
            eng, ex, clock = self.live_engine()
            for _ in range(700):
                eng.step(); clock.t += H4
        pos = eng.store.positions()
        self.assertGreater(ex.orders, 2)
        self.assertTrue(pos and all(p["mode"] == "LIVE" for p in pos))
        closed = [p for p in pos if p["status"] == "CLOSED"]
        self.assertTrue(closed)
        for p in closed:
            self.assertIn(p["reason"], ("STOP", "TREND_FLIP", "TIME"))
            self.assertAlmostEqual(p["r_net"], p["pnl_usd"] / p["risk_usd"], places=9)
        stops = [p for p in closed if p["reason"] == "STOP"]
        self.assertTrue(all(p["r_net"] < -0.95 for p in stops))
        self.assertEqual(set(ex.pos), {p["sym"] for p in pos if p["status"] == "OPEN"})   # the book matches the exchange

    def test_live_positions_managed_after_switching_off(self):
        live = {"on": True}
        with LiveEnv():
            eng, ex, clock = self.live_engine()
            eng.settings_source = lambda: {"live": live["on"]}
            eng.mode_source = lambda: "kalman" if live["on"] else "standard"
            self.assertTrue(self.run_until(eng, clock, lambda: eng.store.positions("OPEN")))
            live["on"] = False                                   # live switch and mode turned off with a position open
            opened = len(eng.store.positions()); orders = ex.orders
            for _ in range(200):
                eng.step(); clock.t += H4
        self.assertEqual(eng.store.positions("OPEN"), [])      # managed to the exit
        self.assertEqual(len(eng.store.positions()), opened)   # no new positions once switched off
        self.assertEqual(ex.orders, orders)
        self.assertEqual(ex.pos, {})


class SharedAccountTest(EngineBase):
    """The Bybit account may be shared with the strategy runner and the SBGZ auto-orders: Kalman must only ever trade,
    manage and book its own position."""

    def test_real_money_endpoint_needs_explicit_permission(self):
        with LiveEnv(BYBIT_BASE_URL="https://api.bybit.com"):
            eng, ex, clock = self.live_engine()
            for _ in range(400):
                eng.step(); clock.t += H4
        self.assertFalse(eng.live_env); self.assertIn("MASIS_ALLOW_REAL_MONEY", eng.live_why)
        self.assertEqual(ex.orders, 0)
        self.assertTrue(eng.store.positions() and all(p["mode"] == "PAPER" for p in eng.store.positions()))

    def test_coin_with_a_resting_order_is_skipped(self):
        with LiveEnv():
            eng, ex, clock = self.live_engine()
            ex.resting = set(ex.m.data)                         # another strategy has a limit order on every coin
            for _ in range(400):
                eng.step(); clock.t += H4
        self.assertEqual(ex.orders, 0); self.assertEqual(eng.store.positions(), [])
        self.assertTrue(any("has an order on this coin" in e["msg"] for e in eng.events))

    def test_foreign_position_after_our_stop_is_left_alone(self):
        with LiveEnv():
            eng, ex, clock = self.live_engine()
            self.assertTrue(self.run_until(eng, clock, lambda: eng.store.positions("OPEN")))
            p = eng.store.positions("OPEN")[0]; sym = p["sym"]; mine = ex.pos[sym]
            ex._close(sym, mine["stop"], clock.t - 600000, mine["size"])                # our exchange stop fills
            own_pnl = float(ex.closed[-1]["closedPnl"])
            side = "Buy" if p["side"] == "L" else "Sell"
            foreign_avg = p["entry_p"] * 1.01
            ex.closed.append(dict(symbol=sym, closedPnl="999", avgExitPrice=str(foreign_avg * 1.05), avgEntryPrice=str(foreign_avg),
                                  closedSize="7", updatedTime=str(clock.t - 300000)))      # a runner trade on the coin, closed
            ex.pos[sym] = dict(size=7.0, avg=foreign_avg, side=side, stop=None, t=clock.t // H4 * H4)   # ... and a new one, open
            eng.step(); clock.t += H4
            rec = [q for q in eng.store.positions() if q["id"] == p["id"]][0]
            self.assertEqual(rec["status"], "CLOSED"); self.assertEqual(rec["reason"], "STOP")
            self.assertAlmostEqual(rec["pnl_usd"], own_pnl, places=9)                   # the runner's +999 is not ours
            self.assertAlmostEqual(rec["r_net"], own_pnl / rec["risk_usd"], places=9)
            for _ in range(150):
                eng.step(); clock.t += H4
        self.assertEqual(ex.pos[sym]["size"], 7.0); self.assertEqual(ex.pos[sym]["avg"], foreign_avg)   # never touched
        self.assertFalse([q for q in eng.store.positions() if q["sym"] == sym and q["entry_t"] > p["entry_t"]])  # coin held: no entry

    def test_position_changed_outside_kalman_is_not_managed(self):
        with LiveEnv():
            eng, ex, clock = self.live_engine()
            self.assertTrue(self.run_until(eng, clock, lambda: eng.store.positions("OPEN")))
            p = eng.store.positions("OPEN")[0]; sym = p["sym"]; x = ex.pos[sym]
            x["avg"] = (x["avg"] * x["size"] + x["avg"] * 1.02 * 2.0) / (x["size"] + 2.0); x["size"] += 2.0   # someone adds to it
            x["stop"] = 1e-9 if p["side"] == "L" else 1e12                                # (keep the fake's stop out of the way)
            before = dict(x)
            eng.step(); clock.t += H4
            rec = [q for q in eng.store.positions() if q["id"] == p["id"]][0]
            self.assertEqual(rec["status"], "CLOSED"); self.assertEqual(rec["reason"], "EXTERNAL_CHANGE"); self.assertIsNone(rec["r_net"])
            for _ in range(150):
                eng.step(); clock.t += H4
        self.assertEqual(ex.pos[sym]["size"], before["size"]); self.assertEqual(ex.pos[sym]["avg"], before["avg"])
        self.assertTrue(any("changed outside Kalman" in e["msg"] for e in eng.store.history()[2]))   # the stored events (the in-memory list keeps only the last 40)

    def test_merged_fill_closes_only_our_part(self):
        with LiveEnv():
            eng, ex, clock = self.live_engine()
            ex.collide_next = 5.0                               # another order of 5 units fills together with Kalman's next one
            self.assertTrue(self.run_until(eng, clock, lambda: ex.orders >= 1))
            sym = ex.last_symbol
        self.assertEqual(ex.pos[sym]["size"], 5.0)              # the other order's position is left as it was
        self.assertFalse([q for q in eng.store.positions() if q["sym"] == sym])
        self.assertTrue(any("merged" in e["msg"] for e in eng.events))

    def test_position_without_a_stop_is_closed(self):
        with LiveEnv():
            eng, ex, clock = self.live_engine()
            ex.attach_stop = False; ex.stop_ok = False          # the stop is neither attached to the order nor settable
            self.assertTrue(self.run_until(eng, clock, lambda: ex.orders >= 1))
        self.assertEqual(ex.pos, {}); self.assertEqual(eng.store.positions("OPEN"), [])
        self.assertTrue(any("stop could not be set" in e["msg"] for e in eng.events))


class HiddenBarMarket(FakeMarket):
    """a market that does not deliver the newest closed bar of some coins (a failed or rate-limited download)"""
    def __init__(self, clock, hidden=()):
        super().__init__(clock); self.hidden = set(hidden); self.calls = 0
    def klines_4h(self, sym, bars):
        self.calls += 1
        rows = super().klines_4h(sym, bars)
        if sym in self.hidden:
            forming = self.clock() // H4 * H4
            rows = [r for r in rows if r[0] != forming - H4]
        return rows


class BarSummaryAndRetryTest(unittest.TestCase):
    def engine(self, hidden=()):
        tmp = tempfile.mkdtemp()
        KE.SNAPSHOT_PATH = os.path.join(tmp, "kalman_trend_live.json"); KE.IND_DIR = os.path.join(tmp, "kalman"); KE.HISTORY_PATH = os.path.join(tmp, "kalman_history.json")
        clock = Clock(T0 + 1200 * H4 + 60 * 1000); market = HiddenBarMarket(clock, hidden)
        eng = KE.KalmanTrendEngine(market=market, store=KE.Store(os.path.join(tmp, "kt.db")), settings_source=lambda: {"paperStartEquity": 10.0},
                                   mode_source=lambda: "kalman", clock=clock, symbols=list(market.data), write_files=False)
        return eng, clock, market

    def test_every_bar_leaves_a_summary_line(self):
        eng, clock, market = self.engine()
        for _ in range(60):
            eng.step(); clock.t += H4
        lines = [e["msg"] for e in eng.store.history()[2] if e["msg"].startswith("4H bar closed")]
        self.assertEqual(len(lines), 60)                                     # one per bar, also the quiet ones
        self.assertTrue(all("BTC daily trend" in m and "coins checked" in m for m in lines))
        self.assertTrue(any("no coin crossed z = +-1, so no signal" in m for m in lines))
        self.assertTrue(any("crossing(s)" in m for m in lines))

    def test_incomplete_bar_is_asked_for_again(self):
        eng, clock, market = self.engine(hidden=["BBBUSDT"])
        self.assertFalse(eng.step())                                          # BBB has no newest bar: the bar is not closed off ...
        self.assertIsNone(eng.store.get("last_bar_t"))
        self.assertNotIn("BBBUSDT", eng.state)
        warns = [e["msg"] for e in eng.store.history()[2] if e["level"] == "WARN"]
        self.assertTrue(any("no data yet for BBBUSDT" in m for m in warns))
        market.hidden.clear(); clock.t += 30 * 1000                          # ... the data arrives 30 seconds later
        self.assertTrue(eng.step())
        self.assertIn("BBBUSDT", eng.state)
        self.assertEqual(eng.store.get("last_bar_t"), (clock.t - KE.BAR_DELAY_MS) // H4 * H4 - H4)
        self.assertEqual(sum(1 for e in eng.store.history()[2] if "no data yet" in e["msg"]), 1)   # asked once, not on every poll

    def test_gives_up_after_the_tries_and_says_so(self):
        eng, clock, market = self.engine(hidden=["BBBUSDT"])
        done = False
        for k in range(KE.MAX_BAR_TRIES + 2):
            done = eng.step() or done; clock.t += 30 * 1000
            if done: break
        self.assertTrue(done and eng.store.get("last_bar_t") is not None)
        msgs = [e["msg"] for e in eng.store.history()[2]]
        self.assertTrue(any("still no data for BBBUSDT" in m for m in msgs))
        self.assertTrue(any(m.startswith("4H bar closed") and "3 of 4 coins checked" in m for m in msgs))
        self.assertEqual(market.hidden, {"BBBUSDT"})

    def test_retry_after_a_restart(self):
        eng, clock, market = self.engine(hidden=["BBBUSDT"])
        eng.step()
        eng2 = KE.KalmanTrendEngine(market=market, store=KE.Store(eng.store.path), settings_source=lambda: {"paperStartEquity": 10.0},
                                    mode_source=lambda: "kalman", clock=clock, symbols=list(market.data), write_files=False)
        market.hidden.clear(); clock.t += 30 * 1000
        self.assertTrue(eng2.step()); self.assertIn("BBBUSDT", eng2.state)

    def test_rate_limit_answer_is_retried(self):
        answers = [{"retCode": 10006, "retMsg": "Too many visits."}, {"retCode": 10006, "retMsg": "Too many visits."},
                   {"retCode": 0, "result": {"list": [["1", "2", "3", "4", "5"]]}}]
        calls = []
        class R:
            def __init__(self, d): self.d = d
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return json.dumps(self.d).encode()
        def fake_urlopen(req, timeout=10):
            calls.append(req.full_url); return R(answers[len(calls) - 1])
        old_open, old_sleep = KE.urllib.request.urlopen, KE.time.sleep
        KE.urllib.request.urlopen = fake_urlopen; KE.time.sleep = lambda s: None
        try:
            r = KE.BybitMarket()._get("/v5/market/kline", {"symbol": "ADAUSDT"})
        finally:
            KE.urllib.request.urlopen, KE.time.sleep = old_open, old_sleep
        self.assertEqual(r, {"list": [["1", "2", "3", "4", "5"]]}); self.assertEqual(len(calls), 3)


class FakeBybit:
    """just enough of backend_lib.bybit_client for LiveBroker: market fills at the forming bar's open (one-way mode: a
    second fill on a coin merges into its position), exchange-side stops that trigger on 4H highs/lows, reduce-only
    closes of a given size, resting orders, per-coin closed-P&L rows"""
    def __init__(self, market):
        self.m = market; self.pos = {}; self.closed = []; self.orders = 0; self.links = set(); self.last_symbol = None
        self.resting = set()            # coins with someone else's resting order
        self.collide_next = None        # size of another order that fills together with the next one
        self.attach_stop = True; self.stop_ok = True
    def _open_px(self, sym): return self.m.klines_4h(sym, 2)[-1][1]
    def _sweep(self):
        now_bar = self.m.clock() // H4 * H4
        for sym, p in list(self.pos.items()):
            if p["stop"] is None: continue
            for b in self.m.data[sym]:
                if b[0] < p["t"] or b[0] >= now_bar: continue
                hit = b[3] <= p["stop"] if p["side"] == "Buy" else b[2] >= p["stop"]
                if hit: self._close(sym, p["stop"], b[0] + H4, p["size"]); break
    def _close(self, sym, px, t, qty):
        p = self.pos[sym]; sgn = 1 if p["side"] == "Buy" else -1; q = min(float(qty), p["size"])
        self.closed.append(dict(symbol=sym, closedPnl=str(sgn * (px - p["avg"]) * q), avgExitPrice=str(px), avgEntryPrice=str(p["avg"]),
                                closedSize=str(q), updatedTime=str(t)))
        p["size"] = round(p["size"] - q, 9)
        if p["size"] <= 0: del self.pos[sym]
    def get_wallet_balance(self): return {"retCode": 0, "result": {"list": [{"totalEquity": "1000"}]}}
    def get_positions(self, symbol=None):
        self._sweep()
        lst = [dict(symbol=s, size=str(p["size"]), avgPrice=str(p["avg"]), side=p["side"], stopLoss=str(p["stop"] or 0))
               for s, p in self.pos.items() if symbol in (None, s)]
        return {"retCode": 0, "result": {"list": lst}}
    def get_open_orders(self, symbol=None):
        return {"retCode": 0, "result": {"list": [dict(symbol=s) for s in sorted(self.resting) if symbol in (None, s)]}}
    def set_leverage(self, symbol, leverage): return {"retCode": 110043, "retMsg": "not modified"}
    def place_order(self, category, symbol, side, order_type, qty, price=None, tp=None, sl=None, order_link_id=None):
        if order_link_id in self.links: return {"retCode": 110072, "retMsg": "OrderLinkedID is duplicate"}
        self.links.add(order_link_id); self.orders += 1; self.last_symbol = symbol
        px = self._open_px(symbol)
        for q in [float(qty)] + ([self.collide_next] if self.collide_next else []):
            p = self.pos.get(symbol)
            if p: p["avg"] = (p["avg"] * p["size"] + px * q) / (p["size"] + q); p["size"] += q
            else: self.pos[symbol] = dict(size=q, avg=px, side=side, stop=None, t=self.m.clock() // H4 * H4)
        self.collide_next = None
        if sl and self.attach_stop: self.pos[symbol]["stop"] = float(sl)
        return {"retCode": 0, "result": {"orderId": f"o{self.orders}"}}
    def set_trading_stop(self, category, symbol, stop_loss=None, take_profit=None, position_idx=0):
        if not self.stop_ok: return {"retCode": 10001, "retMsg": "rejected"}
        if symbol in self.pos and stop_loss is not None: self.pos[symbol]["stop"] = float(stop_loss)
        return {"retCode": 0}
    def close_position(self, category, symbol, side, qty, is_opposing_order=False):
        if symbol in self.pos: self._close(symbol, self._open_px(symbol), self.m.clock(), qty)
        return {"retCode": 0}
    def signed_request(self, method, path, params=None, body=None):
        assert method == "GET" and path == "/v5/position/closed-pnl", path
        rows = [x for x in reversed(self.closed) if x["symbol"] == params["symbol"]][:int(params.get("limit", 50))]
        return {"retCode": 0, "result": {"list": rows}}


if __name__ == "__main__":
    unittest.main()
