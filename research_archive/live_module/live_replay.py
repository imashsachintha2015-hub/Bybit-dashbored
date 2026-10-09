"""Replay test: the live engine (daemons/kalman_trend_engine.py) driven bar by bar through history by a fake exchange
that serves the research data (OKX 4H bars, Binance funding), in PAPER mode with the Kalman mode selected.
Its paper trades must equal the research portfolio (sit_core.port) taken over the same signals.
usage: live_replay.py START END [research|live] [outdir]"""
import os, sys, json, time, calendar, tempfile, bisect
REPO = "/home/user/Bybit-dashbored"; sys.path.insert(0, REPO)
import numpy as np
import sit_core as C
from backend_lib import kalman_trend as K
import daemons.kalman_trend_engine as KE

START, END = sys.argv[1], sys.argv[2]; FUND_DEF = sys.argv[3] if len(sys.argv) > 3 else "research"
OUT = sys.argv[4] if len(sys.argv) > 4 else None
ms = lambda s: calendar.timegm(time.strptime(s, "%Y-%m-%d")) * 1000
t_start, t_end = ms(START), ms(END)
SYMS = sorted({x[1] for x in C.FINAL})
DATA = {}
for s in SYMS:
    S = C.series(s, False); DATA[s + "USDT"] = (S["t"].tolist(), S["o"].tolist(), S["h"].tolist(), S["l"].tolist(), S["c"].tolist())
FUND = {}
for s in SYMS:
    p = f"funding/{s}.json"; rows = json.load(open(p)) if os.path.exists(p) else []
    FUND[s + "USDT"] = [(int(r[0]), float(r[1])) for r in rows]

if FUND_DEF == "research":                       # isolate the engine from the 8h-window funding definition
    def fav_research(times, rates, t_close):
        b = bisect.bisect_right(times, t_close)
        return (sum(rates[b - 9:b]) / 9) if b >= 9 else None
    K.funding_avg = fav_research

class Clock:
    def __init__(self, t): self.t = t
    def __call__(self): return self.t

class FakeMarket:
    base = "replay"
    def __init__(self, clock): self.clock = clock
    def instruments(self): return {s: dict(status="Trading", qty_step=0.001, min_qty=0.001, min_notional=5, tick=1e-6, funding_min=480) for s in DATA}
    def klines_4h(self, sym, bars):
        t, o, h, l, c = DATA[sym]; now = self.clock(); forming = now // K.H4 * K.H4
        k = bisect.bisect_right(t, forming) - 1
        if k < 0: return []
        rows = [[t[j], o[j], h[j], l[j], c[j]] for j in range(max(0, k - bars + 1), k + 1)]
        if rows and rows[-1][0] == forming: rows[-1] = [t[k], o[k], o[k], o[k], o[k]]     # the forming bar: only its open is known
        return rows
    def funding(self, sym, limit=200):
        rows = FUND.get(sym, []); k = bisect.bisect_right([r[0] for r in rows], self.clock())
        return rows[max(0, k - limit):k]
    def open_interest_1h(self, sym, hours=760): return []
    def tickers(self): return {}

tmp = tempfile.mkdtemp(); clock = Clock(t_start + 60 * 1000)
eng = KE.KalmanTrendEngine(market=FakeMarket(clock), store=KE.Store(os.path.join(tmp, "kt.db")),
                           settings_source=lambda: {"riskPct": 0.5, "maxOpen": 8, "paperStartEquity": 10.0},
                           mode_source=lambda: "kalman", clock=clock, symbols=list(DATA), write_files=False)
t0 = time.time(); steps = 0
while clock.t < t_end:
    eng.step(); steps += 1; clock.t += K.H4
if OUT:                                          # one final step with files on, for the dashboard check
    KE.SNAPSHOT_PATH = os.path.join(OUT, "kalman_trend_live.json"); KE.IND_DIR = os.path.join(OUT, "kalman"); eng.write_files = True
    eng.store.put("last_bar_t", eng.store.get("last_bar_t") - K.H4); eng.step()
print(f"replayed {steps} 4H bars {START} .. {END} in {time.time() - t0:.0f}s (funding rule: {FUND_DEF})")

mine = [p for p in eng.store.positions() if p["mode"] == "PAPER"]
closed = [p for p in mine if p["status"] == "CLOSED"]
eq_engine = eng.store.get("paper_equity")
# research: the same portfolio over signals from the first processed bar
first_bar = t_start - K.H4
ref_tr = [x for x in C.FINAL if first_bar <= x[4] < t_end - K.H4]
_, _, _, rec = C.port(ref_tr, record=True)
ref_taken = {(row[1] + "USDT", int(row[4]), row[5]): row for row, pnl, eq in rec}
eng_taken = {(p["sym"], p["signal_t"], p["side"]): p for p in mine}
both = set(ref_taken) & set(eng_taken)
same = sum(1 for k in both if eng_taken[k]["status"] == "CLOSED" and abs(eng_taken[k]["r_net"] - ref_taken[k][2]) < 1e-9 and eng_taken[k]["exit_t"] == ref_taken[k][3])
open_end = sum(1 for k in both if eng_taken[k]["status"] == "OPEN")
print(f"research portfolio took {len(ref_taken)} trades, engine took {len(eng_taken)}: in both {len(both)}, "
      f"only research {len(set(ref_taken) - set(eng_taken))}, only engine {len(set(eng_taken) - set(ref_taken))}")
dr = [abs(eng_taken[k]["r_net"] - ref_taken[k][2]) for k in both if eng_taken[k]["status"] == "CLOSED"]
dt = sum(1 for k in both if eng_taken[k]["status"] == "CLOSED" and eng_taken[k]["exit_t"] != ref_taken[k][3])
print(f"closed in both: {len(dr)}; exit time differs: {dt}; net R identical (<1e-9): {sum(1 for d in dr if d < 1e-9)}; largest |net R difference| {max(dr) if dr else 0:.4f}R ({open_end} still open at the end)")
for k in sorted(set(ref_taken) ^ set(eng_taken))[:8]: print("   differs:", k, "research" if k in ref_taken else "engine")
# equity with research accounting for the closed trades only (open trades excluded on both sides)
ref_closed = [row for row, pnl, eq in rec if (row[1] + "USDT", int(row[4]), row[5]) in eng_taken and eng_taken[(row[1] + "USDT", int(row[4]), row[5])]["status"] == "CLOSED"]
print(f"paper equity in the engine ${eq_engine:.4f} (booked trades); closed trades {len(closed)}, open {len(mine) - len(closed)}; "
      f"shadow (forward-test) trades recorded: {eng.store.shadow_stats()}")
if os.environ.get("REPLAY_DEBUG"):
    f = lambda m: time.strftime("%m-%d %H:%M", time.gmtime(m / 1000)) if m else "-"
    for k in sorted(both):
        e = eng_taken[k]; r = ref_taken[k]
        if e["status"] != "CLOSED": continue
        if abs(e["r_net"] - r[2]) > 1e-9 or e["exit_t"] != r[3]:
            print(f"   {k[0]:9s} {k[2]} sig {f(k[1])} | engine exit {f(e['exit_t'])} {e['reason']:10s} R {e['r_net']:+.4f} entry {e['entry_p']:.6g} stop {e['stop']:.6g}"
                  f" | research exit {f(r[3])} R {r[2]:+.4f}")
