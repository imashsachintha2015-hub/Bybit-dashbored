"""Unit tests for the Kalman Trend mode: backend_lib/kalman_trend.py (rules, sizing, settings) and
daemons/kalman_trend_engine.py (paper trading on a synthetic market). No network, no research data.
Run: python3 -m unittest tests/test_kalman_trend.py
Parity with the research backtest is checked separately in research_archive/live_module/."""
import math, os, random, sys, tempfile, unittest

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


class EngineTest(unittest.TestCase):
    def run_engine(self, mode, bars=900):
        tmp = tempfile.mkdtemp()
        KE.SNAPSHOT_PATH = os.path.join(tmp, "kalman_trend_live.json"); KE.IND_DIR = os.path.join(tmp, "kalman")
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

    def test_standby_records_only(self):
        eng, tmp = self.run_engine("championship", bars=500)
        self.assertEqual(eng.store.positions(), [])
        self.assertGreater(eng.store.shadow_stats()["n"], 0)

    def test_live_mode_with_fake_exchange(self):
        tmp = tempfile.mkdtemp()
        KE.SNAPSHOT_PATH = os.path.join(tmp, "kalman_trend_live.json"); KE.IND_DIR = os.path.join(tmp, "kalman")
        clock = Clock(T0 + 1200 * H4 + 60 * 1000); market = FakeMarket(clock); ex = FakeBybit(market)
        old_env, old_sleep = os.environ.get("KALMAN_LIVE"), KE.time.sleep
        os.environ["KALMAN_LIVE"] = "1"; KE.time.sleep = lambda s: None
        try:
            eng = KE.KalmanTrendEngine(market=market, store=KE.Store(os.path.join(tmp, "kt.db")),
                                       settings_source=lambda: {"live": True, "riskPct": 0.5}, mode_source=lambda: "kalman",
                                       clock=clock, live_client=ex, symbols=list(market.data), write_files=True)
            for _ in range(700):
                eng.step(); clock.t += H4
        finally:
            KE.time.sleep = old_sleep
            if old_env is None: os.environ.pop("KALMAN_LIVE", None)
            else: os.environ["KALMAN_LIVE"] = old_env
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
        tmp = tempfile.mkdtemp()
        KE.SNAPSHOT_PATH = os.path.join(tmp, "kalman_trend_live.json"); KE.IND_DIR = os.path.join(tmp, "kalman")
        clock = Clock(T0 + 1200 * H4 + 60 * 1000); market = FakeMarket(clock); ex = FakeBybit(market)
        live = {"on": True}
        old_env, old_sleep = os.environ.get("KALMAN_LIVE"), KE.time.sleep
        os.environ["KALMAN_LIVE"] = "1"; KE.time.sleep = lambda s: None
        try:
            eng = KE.KalmanTrendEngine(market=market, store=KE.Store(os.path.join(tmp, "kt.db")),
                                       settings_source=lambda: {"live": live["on"]}, mode_source=lambda: "kalman" if live["on"] else "standard",
                                       clock=clock, live_client=ex, symbols=list(market.data), write_files=False)
            while not eng.store.positions("OPEN"):
                eng.step(); clock.t += H4
            live["on"] = False                                   # live switch and mode turned off with a position open
            opened = len(eng.store.positions()); orders = ex.orders
            for _ in range(200):
                eng.step(); clock.t += H4
        finally:
            KE.time.sleep = old_sleep
            if old_env is None: os.environ.pop("KALMAN_LIVE", None)
            else: os.environ["KALMAN_LIVE"] = old_env
        self.assertEqual(eng.store.positions("OPEN"), [])      # managed to the exit
        self.assertEqual(len(eng.store.positions()), opened)   # no new positions once switched off
        self.assertEqual(ex.orders, orders)
        self.assertEqual(ex.pos, {})


class FakeBybit:
    """just enough of backend_lib.bybit_client for LiveBroker: market fills at the forming bar's open, exchange-side
    stops that trigger on 4H highs/lows, reduce-only closes, closed-PnL rows"""
    def __init__(self, market): self.m = market; self.pos = {}; self.closed = []; self.orders = 0
    def _open_px(self, sym): return self.m.klines_4h(sym, 2)[-1][1]
    def _sweep(self):
        now_bar = self.m.clock() // H4 * H4
        for sym, p in list(self.pos.items()):
            for b in self.m.data[sym]:
                if b[0] < p["t"] or b[0] >= now_bar: continue
                hit = b[3] <= p["stop"] if p["side"] == "Buy" else b[2] >= p["stop"]
                if hit: self._close(sym, p["stop"], b[0] + H4); break
    def _close(self, sym, px, t):
        p = self.pos.pop(sym); sgn = 1 if p["side"] == "Buy" else -1
        self.closed.append(dict(symbol=sym, closedPnl=str(sgn * (px - p["avg"]) * p["size"]), avgExitPrice=str(px), updatedTime=str(t)))
    def get_wallet_balance(self): return {"retCode": 0, "result": {"list": [{"totalEquity": "1000"}]}}
    def get_positions(self, symbol=None):
        self._sweep()
        lst = [dict(symbol=s, size=str(p["size"]), avgPrice=str(p["avg"]), side=p["side"]) for s, p in self.pos.items() if symbol in (None, s)]
        return {"retCode": 0, "result": {"list": lst}}
    def set_leverage(self, symbol, leverage): return {"retCode": 110043, "retMsg": "not modified"}
    def place_order(self, category, symbol, side, order_type, qty, price=None, tp=None, sl=None):
        self.orders += 1
        self.pos[symbol] = dict(size=float(qty), avg=self._open_px(symbol), side=side, stop=float(sl), t=self.m.clock() // H4 * H4)
        return {"retCode": 0, "result": {"orderId": f"o{self.orders}"}}
    def set_trading_stop(self, category, symbol, stop_loss=None, take_profit=None, position_idx=0):
        if symbol in self.pos and stop_loss is not None: self.pos[symbol]["stop"] = float(stop_loss)
        return {"retCode": 0}
    def close_position(self, category, symbol, side, qty, is_opposing_order=False):
        if symbol in self.pos: self._close(symbol, self._open_px(symbol), self.m.clock())
        return {"retCode": 0}
    def get_closed_pnl(self, limit=50): return {"retCode": 0, "result": {"list": list(reversed(self.closed))[:limit]}}


if __name__ == "__main__":
    unittest.main()
