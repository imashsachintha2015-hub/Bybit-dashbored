#!/usr/bin/env python3
"""Kalman Trend engine: the funding-aware Kalman trend system (research_archive/longshort) as a dashboard mode.

Every 4H close (UTC 00/04/08/12/16/20 + a short delay) it
  - refreshes 4H bars and funding for the coin universe from Bybit's public market data,
  - computes the system's state for every coin (Kalman z, BTC daily trend, funding rule) and the "shadow book":
    every trade the system takes before the 8-position limit (the forward test, recorded in SQLite),
  - writes scratch/kalman_trend_live.json (dashboard panel) and scratch/kalman/<SYM>.json (chart indicator).
When the dashboard mode is 'kalman' it also trades the portfolio (0.5% risk, max 8 open, one per coin):
  - PAPER (default): virtual equity, research accounting (fills at the next 4H open, stop-first, fees + real funding);
  - LIVE: orders on the Bybit account of BYBIT_BASE_URL -- only if KALMAN_LIVE=1 is set on the server AND live is
    switched on in the dashboard; like the strategy runner, a real-money endpoint also needs MASIS_ALLOW_REAL_MONEY=1.
    Market entry with a broker-side stop; trend-flip and 20-day exits by reduce-only market orders. Positions are
    always managed to their exit, also after the mode is switched away.
    The account may be shared with the strategy runner (daemons/masis_runner.py) and the SBGZ auto-orders: the engine
    never enters a coin that already has a position or a resting order, and it only manages and books the position it
    opened itself (same side, size and entry price on the exchange).
The trade logic is backend_lib/kalman_trend.py (parity with the research: research_archive/live_module/)."""
import os, sys, json, math, time, sqlite3, uuid, traceback
import urllib.request, urllib.parse
from datetime import datetime, timezone

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT_DIR)

from backend_lib import kalman_trend as K

H4 = K.H4
VERSION = "KT-1.0"
SCRATCH = os.path.join(ROOT_DIR, "scratch")
IND_DIR = os.path.join(SCRATCH, "kalman")
SNAPSHOT_PATH = os.path.join(SCRATCH, "kalman_trend_live.json")
SETTINGS_KEY = K.SETTINGS_KEY
BAR_DELAY_MS = 45 * 1000          # wait this long after a 4H boundary so the exchange has closed the bar
FRESH_MS = 30 * 60 * 1000         # only act on a signal bar that closed less than 30 minutes ago
COIN_BARS, BTC_BARS = 1000, 2000  # warm-up windows: parity with the full history (research_archive/live_module)
DEFAULT_SYMBOLS = [s + "USDT" for s in (
    "AAVE ADA APT ARB ATOM AVAX BCH BNB BTC CRV DOGE DOT ETC ETH FIL HBAR ICP INJ LINK LTC MKR NEAR ONDO OP POL "
    "RUNE SEI SOL STX SUI TIA TON TRX UNI WLD XRP").split()]
clean_settings = K.clean_settings


def now_ms(): return int(time.time() * 1000)
def iso(ms): return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC") if ms else None


def log(msg, level="INFO"):
    line = f"[KALMAN {level} {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    try: print(line, flush=True)
    except Exception: pass


# ======================================================================= market data (Bybit public V5)
class BybitMarket:
    """Signals use the real market: Bybit's mainnet public data (KALMAN_MARKET_URL to override). If that host cannot be
    reached from the server, it fails over to the BYBIT_BASE_URL endpoint (the demo domain serves the same market data)."""
    def __init__(self, base_url=None):
        fix = lambda u: (u if u.startswith("http") else "https://" + u).rstrip("/")
        first = (base_url or os.environ.get("KALMAN_MARKET_URL") or "https://api.bybit.com").strip()
        second = (os.environ.get("BYBIT_BASE_URL") or "https://api-demo.bybit.com").strip()
        self.bases = [fix(first)] + ([fix(second)] if fix(second) != fix(first) else [])
        self.base = self.bases[0]

    def _get(self, path, params, retries=2):
        for base in [self.base] + [b for b in self.bases if b != self.base]:
            url = f"{base}{path}?{urllib.parse.urlencode(params)}"
            for k in range(retries + 1):
                try:
                    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "MASIS-Kalman/1.0"}), timeout=10) as r:
                        d = json.loads(r.read().decode())
                    if d.get("retCode") == 0:
                        if base != self.base: log(f"market data now from {base}", "WARN"); self.base = base
                        return d.get("result") or {}
                    log(f"market {path} {params.get('symbol', '')}: {d.get('retCode')} {d.get('retMsg')}", "WARN")
                    return None                                          # an API answer, not a network problem: no failover
                except Exception as e:
                    if k == retries: log(f"market {base}{path} {params.get('symbol', '')}: {e}", "WARN")
                    else: time.sleep(0.5 * (k + 1))
        return None

    def instruments(self):
        out, cursor = {}, ""
        for _ in range(10):
            p = {"category": "linear", "limit": 1000}
            if cursor: p["cursor"] = cursor
            r = self._get("/v5/market/instruments-info", p)
            if not r: break
            for x in r.get("list", []):
                lot, pf = x.get("lotSizeFilter", {}), x.get("priceFilter", {})
                out[x["symbol"]] = dict(status=x.get("status"), qty_step=float(lot.get("qtyStep") or 0.001),
                                        min_qty=float(lot.get("minOrderQty") or 0), min_notional=float(lot.get("minNotionalValue") or 5),
                                        tick=float(pf.get("tickSize") or 0.0001), funding_min=int(x.get("fundingInterval") or 480))
            cursor = r.get("nextPageCursor") or ""
            if not cursor: break
        return out

    def klines_4h(self, sym, bars):
        """ascending [start, o, h, l, c] including the bar that is still forming"""
        rows, end = {}, None
        while len(rows) < bars:
            p = {"category": "linear", "symbol": sym, "interval": "240", "limit": min(1000, bars - len(rows) + 1)}
            if end: p["end"] = end
            r = self._get("/v5/market/kline", p)
            lst = (r or {}).get("list", [])
            if not lst: break
            for b in lst: rows[int(b[0])] = [int(b[0]), float(b[1]), float(b[2]), float(b[3]), float(b[4])]
            oldest = min(int(b[0]) for b in lst)
            if len(lst) < p["limit"] or (end and oldest >= end): break
            end = oldest - 1
        return [rows[k] for k in sorted(rows)]

    def funding(self, sym, limit=200):
        r = self._get("/v5/market/funding/history", {"category": "linear", "symbol": sym, "limit": limit})
        return sorted((int(x["fundingRateTimestamp"]), float(x["fundingRate"])) for x in (r or {}).get("list", []))

    def open_interest_1h(self, sym, hours=760):
        out, cursor = {}, ""
        for _ in range(6):
            p = {"category": "linear", "symbol": sym, "intervalTime": "1h", "limit": 200}
            if cursor: p["cursor"] = cursor
            r = self._get("/v5/market/open-interest", p)
            if not r: break
            for x in r.get("list", []): out[int(x["timestamp"])] = float(x["openInterest"])
            cursor = r.get("nextPageCursor") or ""
            if not cursor or len(out) >= hours: break
        return sorted(out.items())

    def tickers(self):
        r = self._get("/v5/market/tickers", {"category": "linear"})
        return {x["symbol"]: float(x.get("lastPrice") or 0) for x in (r or {}).get("list", [])}


# ======================================================================= persistence
class Store:
    def __init__(self, path=None):
        d = os.environ.get("DATA_DIR") or ("/data" if os.path.isdir("/data") else SCRATCH)
        os.makedirs(d, exist_ok=True)
        self.path = path or os.path.join(d, "kalman_trend.db")
        with self._c() as c:
            c.execute("""CREATE TABLE IF NOT EXISTS positions (id TEXT PRIMARY KEY, sym TEXT, side TEXT, mode TEXT,
                signal_t INTEGER, entry_t INTEGER, entry_p REAL, stop REAL, qty REAL, risk_usd REAL, equity_at_entry REAL,
                risk_pct REAL, status TEXT, exit_t INTEGER, exit_p REAL, reason TEXT, r_net REAL, pnl_usd REAL,
                booked INTEGER DEFAULT 0, order_id TEXT, note TEXT)""")
            c.execute("""CREATE TABLE IF NOT EXISTS shadow (id TEXT PRIMARY KEY, sym TEXT, side TEXT, signal_t INTEGER,
                entry_t INTEGER, entry_p REAL, stop REAL, exit_t INTEGER, exit_p REAL, reason TEXT, r_net REAL)""")
            c.execute("CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT)")

    def _c(self):
        c = sqlite3.connect(self.path, timeout=15); c.row_factory = sqlite3.Row; return c

    def get(self, k, default=None):
        with self._c() as c:
            r = c.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
        return json.loads(r["v"]) if r else default

    def put(self, k, v):
        with self._c() as c: c.execute("INSERT INTO meta (k, v) VALUES (?, ?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, json.dumps(v)))

    def positions(self, status=None):
        with self._c() as c:
            q = "SELECT * FROM positions" + (" WHERE status=?" if status else "") + " ORDER BY entry_t"
            return [dict(r) for r in c.execute(q, (status,) if status else ())]

    def save_position(self, p):
        cols = list(p.keys())
        with self._c() as c:
            c.execute(f"INSERT INTO positions ({','.join(cols)}) VALUES ({','.join('?' * len(cols))}) "
                      f"ON CONFLICT(id) DO UPDATE SET {','.join(f'{k}=excluded.{k}' for k in cols if k != 'id')}", [p[k] for k in cols])

    def save_shadow(self, rows):
        with self._c() as c:
            for r in rows:
                c.execute("INSERT OR IGNORE INTO shadow VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                          (r["id"], r["sym"], r["side"], r["signal_t"], r["entry_t"], r["entry_p"], r["stop"], r["exit_t"], r["exit_p"], r["reason"], r["r_net"]))

    def shadow_stats(self):
        with self._c() as c:
            rs = [r[0] for r in c.execute("SELECT r_net FROM shadow WHERE r_net IS NOT NULL")]
        n = len(rs)
        return dict(n=n, mean_r=(sum(rs) / n) if n else None, win_rate=(sum(1 for r in rs if r > 0) / n * 100) if n else None)


# ======================================================================= execution
class PaperBroker:
    live = False
    def equity(self, store): return store.get("paper_equity", None)


class LiveBroker:
    """orders on the Bybit account of BYBIT_BASE_URL through backend_lib.bybit_client"""
    live = True
    def __init__(self, client): self.c = client

    def equity(self, store):
        try:
            r = self.c.get_wallet_balance(); return float(r["result"]["list"][0]["totalEquity"])
        except Exception as e:
            log(f"wallet balance unavailable: {e}", "WARN"); return None

    def has_position(self, sym):
        r = self.c.get_positions(sym)
        if (r or {}).get("retCode") != 0: return None
        return any(float(p.get("size") or 0) > 0 for p in r["result"]["list"])

    def has_orders(self, sym):
        """a resting order on the coin (another strategy's limit entry, say); None when it cannot be checked"""
        r = self.c.get_open_orders(sym)
        if (r or {}).get("retCode") != 0: return None
        return any(o.get("symbol") == sym for o in (r.get("result") or {}).get("list") or [])

    def position(self, sym):
        r = self.c.get_positions(sym)
        if (r or {}).get("retCode") != 0: return None
        for p in r["result"]["list"]:
            if float(p.get("size") or 0) > 0: return p
        return {}

    def open(self, sym, side, qty, stop, leverage, link=None):
        r = self.c.set_leverage(sym, leverage)
        if (r or {}).get("retCode") not in (0, 110043): log(f"set leverage {sym}: {r}", "WARN")
        return self.c.place_order("linear", sym, "Buy" if side == "L" else "Sell", "Market", qty, sl=stop, order_link_id=link)

    def set_stop(self, sym, stop): return self.c.set_trading_stop("linear", sym, stop_loss=stop)

    def close(self, sym, side, qty): return self.c.close_position("linear", sym, "Buy" if side == "L" else "Sell", qty)

    def closed_pnl(self, sym, since_ms):
        """closed-P&L rows of the coin newer than since_ms (all strategies; the engine picks out its own)"""
        r = self.c.signed_request("GET", "/v5/position/closed-pnl", {"category": "linear", "symbol": sym, "limit": "50"})
        return [x for x in (r or {}).get("result", {}).get("list", []) if x.get("symbol") == sym and int(x.get("updatedTime") or 0) >= since_ms]


def round_step(x, step, mode="down"):
    if step <= 0: return x
    k = x / step; k = math.floor(k + 1e-9) if mode == "down" else math.ceil(k - 1e-9) if mode == "up" else round(k)
    dec = max(0, -int(math.floor(math.log10(step)))) if step < 1 else 0
    return round(k * step, dec)


# ======================================================================= engine
class KalmanTrendEngine:
    def __init__(self, market=None, store=None, settings_source=None, mode_source=None, clock=now_ms, live_client=None,
                 symbols=None, write_files=True, oi_source=None):
        self.m = market or BybitMarket()
        self.store = store or Store()
        self.settings_source = settings_source or self._kv_settings
        self.mode_source = mode_source or self._kv_mode
        self.clock = clock; self.live_client = live_client; self.write_files = write_files
        self.symbols = symbols or DEFAULT_SYMBOLS
        self.instr = {}; self.bars = {}; self.fund = {}; self.state = {}; self.events = []
        self.oi_source = oi_source
        self.live_env, self.live_why = K.live_permission()
        self.last_light = 0; self.prices = {}

    # ------------------------------------------------ shared dashboard state
    @staticmethod
    def _kv_settings():
        try:
            from backend_lib.kv import kv_get_json
            return kv_get_json(SETTINGS_KEY, {}) or {}
        except Exception: return {}

    @staticmethod
    def _kv_mode():
        try:
            from backend_lib import auto_trade_state
            return str(auto_trade_state.load().get("strategyMode") or "")
        except Exception: return ""

    def event(self, msg, level="INFO"):
        log(msg, level)
        self.events.append({"t": self.clock(), "level": level, "msg": msg}); self.events = self.events[-40:]

    # ------------------------------------------------ data
    def refresh_data(self, first):
        if not self.instr:
            self.instr = self.m.instruments() or {}
            if self.instr:
                missing = [s for s in self.symbols if s not in self.instr or self.instr[s].get("status") != "Trading"]
                if missing: self.event(f"not tradable on Bybit, skipped: {', '.join(missing)}", "WARN")
                self.symbols = [s for s in self.symbols if s in self.instr and self.instr[s].get("status") == "Trading"]
        for s in self.symbols:
            need = BTC_BARS if s == "BTCUSDT" else COIN_BARS
            if s not in self.bars or len(self.bars[s]) < 50:
                self.bars[s] = self.m.klines_4h(s, need + 1)
            else:
                new = self.m.klines_4h(s, 6)
                if new:
                    keep = {b[0]: b for b in self.bars[s]}; keep.update({b[0]: b for b in new})
                    self.bars[s] = [keep[k] for k in sorted(keep)][-(need + 1):]
            f = self.m.funding(s, 200 if (first or s not in self.fund) else 30)
            if f:
                old = dict(self.fund.get(s, [])); old.update(dict(f)); self.fund[s] = sorted(old.items())[-400:]

    def closed_arrays(self, sym, bar_t):
        """bars up to and including the closed bar opening at bar_t, plus the open of the next bar (None if missing)"""
        rows = [b for b in self.bars.get(sym, []) if b[0] <= bar_t]
        nxt = [b for b in self.bars.get(sym, []) if b[0] == bar_t + H4]
        t = [b[0] for b in rows]; o = [b[1] for b in rows]; h = [b[2] for b in rows]; l = [b[3] for b in rows]; c = [b[4] for b in rows]
        return t, o, h, l, c, (nxt[0][1] if nxt else None)

    # ------------------------------------------------ the main step
    def step(self):
        now = self.clock()
        bar_t = (now - BAR_DELAY_MS) // H4 * H4 - H4                      # open time of the newest closed 4H bar
        last = self.store.get("last_bar_t")
        did = False
        if last is None or bar_t > last:
            first = last is None or not self.bars
            try:
                self.on_bar_close(bar_t, now, first)
                self.store.put("last_bar_t", bar_t); did = True
            except Exception as e:
                self.event(f"bar {iso(bar_t)} failed: {e}", "ERROR"); traceback.print_exc()
        if not did and now - self.last_light >= 60 * 1000:
            self.light(now)
        return did

    def settings(self):
        s = clean_settings(self.settings_source())
        tok = s.get("paperResetToken"); old = self.store.get("paper_reset_token")
        if self.store.get("paper_equity") is None:
            self.store.put("paper_equity", s["paperStartEquity"]); self.store.put("paper_start", s["paperStartEquity"]); self.store.put("paper_reset_token", tok)
        elif tok != old:
            if any(p["mode"] == "PAPER" for p in self.store.positions("OPEN")):
                self.event("paper reset ignored: close the open paper positions first", "WARN")
            else:
                self.store.put("paper_equity", s["paperStartEquity"]); self.store.put("paper_start", s["paperStartEquity"])
                self.event(f"paper account restarted at ${s['paperStartEquity']:.2f}")
            self.store.put("paper_reset_token", tok)
        return s

    def broker(self, s, active):
        if active and s["live"] and self.live_env:
            if self.live_client is None:
                try:
                    from backend_lib.bybit_client import get_client
                    self.live_client, err = get_client()
                    if err: self.event(f"live disabled: {err}", "WARN")
                except Exception as e:
                    self.event(f"live disabled: {e}", "WARN")
            if self.live_client is not None: return LiveBroker(self.live_client)
        return PaperBroker()

    def exit_broker(self):
        if self.live_client is None:
            try:
                from backend_lib.bybit_client import get_client
                self.live_client, err = get_client()
                if err: self.event(f"cannot manage live positions: {err}", "ERROR")
            except Exception as e:
                self.event(f"cannot manage live positions: {e}", "ERROR")
        return LiveBroker(self.live_client) if self.live_client is not None else PaperBroker()

    def on_bar_close(self, bar_t, now, first):
        s = self.settings(); mode = self.mode_source(); active = mode == "kalman"
        broker = self.broker(s, active)
        self.refresh_data(first)
        bt, bo, bh, bl, bc, _ = self.closed_arrays("BTCUSDT", bar_t)
        btc_tr = dict(zip(bt, K.daily_trend(bt, bc))) if bt else {}
        fresh_ok = now - (bar_t + H4) <= FRESH_MS
        if self.store.get("engine_start") is None: self.store.put("engine_start", bar_t + H4)
        record_from = self.store.get("engine_start")                     # the forward test only counts signals after the start
        self.state = {}; finished_shadow = []
        for sym in self.symbols:
            t, o, h, l, c, next_open = self.closed_arrays(sym, bar_t)
            if len(c) < K.WARMUP + 2 or t[-1] != bar_t: continue
            z, level = K.kalman(c); a = K.atr14(h, l, c)
            ft = [x[0] for x in self.fund.get(sym, [])]; fr = [x[1] for x in self.fund.get(sym, [])]
            fav = lambda tc, ft=ft, fr=fr: K.funding_avg(ft, fr, tc)
            chain = K.coin_trades(t, o, h, l, c, z, a, lambda ti: btc_tr.get(ti, 0), fav)
            for tr in chain:
                if tr["exit_t"] is not None and t[tr["signal_i"]] + H4 >= record_from:
                    finished_shadow.append(dict(id=f"{sym}|{t[tr['signal_i']]}|{tr['side']}", sym=sym, side=tr["side"], signal_t=t[tr["signal_i"]],
                                                entry_t=tr["entry_t"], entry_p=tr["entry_p"], stop=tr["stop"], exit_t=tr["exit_t"],
                                                exit_p=tr["exit_p"], reason=tr["reason"], r_net=K.net_r(tr, ft, fr)))
            open_tr = chain[-1] if chain and chain[-1]["exit_t"] is None else None
            busy_until = max([tr["exit_t"] for tr in chain if tr["exit_t"] is not None] or [0])
            sgn = K.crossing(z[-2], z[-1]); side = "L" if sgn == 1 else "S" if sgn == -1 else None
            fav_now = fav(bar_t + H4); btc_now = btc_tr.get(bar_t, 0)
            sig = None
            if side:
                ok, why = K.entry_allowed(side, btc_now, fav_now)
                if open_tr is not None or bar_t + H4 < busy_until: ok, why = False, "a trade on this coin is still open"
                sig = dict(side=side, ok=ok, why=why, stop=K.initial_stop(side, c[-1], a[-1], next_open or c[-1]))
            self.state[sym] = dict(t=t, o=o, h=h, l=l, c=c, z=z, level=level, a=a, chain=chain, open_tr=open_tr, sig=sig,
                                   next_open=next_open, favg=fav_now, ft=ft, fr=fr)
        if finished_shadow: self.store.save_shadow(finished_shadow)

        # 1) exits of open positions. Live positions are always managed through the exchange, also when the
        #    mode or the live switch has been turned off since they were opened.
        open_now = self.store.positions("OPEN")
        exit_broker = broker if broker.live else (self.exit_broker() if any(p["mode"] == "LIVE" for p in open_now) else broker)
        for p in open_now:
            self.manage_exit(p, bar_t, now, exit_broker)
        # 2) book closed positions whose exit bar has ended (research accounting: P&L counts at the end of the exit bar)
        self.book(bar_t + H4)
        # 3) entries on fresh signals
        if active and fresh_ok:
            cands = sorted((bar_t + H4, sym) for sym, st in self.state.items() if st["sig"] and st["sig"]["ok"])
            for te, sym in cands: self.try_entry(sym, bar_t, now, s, broker)
        elif not active and any(st["sig"] and st["sig"]["ok"] for st in self.state.values()):
            self.event("signals this bar were recorded only: the Kalman mode is not selected")
        self.write_snapshot(s, mode, broker, bar_t, now)
        self.write_indicators(bar_t)

    # ------------------------------------------------ positions
    def manage_exit(self, p, bar_t, now, broker):
        st = self.state.get(p["sym"])
        if st is None:
            self.event(f"{p['sym']}: no fresh 4H bar, position not checked this bar", "WARN"); return
        try: i = st["t"].index(p["signal_t"])
        except ValueError: i = None
        if p["mode"] == "PAPER":
            if i is None: self.event(f"{p['sym']}: signal bar outside the data window", "WARN"); return
            tr = K._walk(st["t"], st["o"], st["h"], st["l"], st["c"], st["z"], i, p["side"], p["stop"])
            if tr["exit_t"] is not None:
                self._close(p, tr["exit_t"], tr["exit_p"], tr["reason"], K.net_r(tr, st["ft"], st["fr"]))
            elif tr.get("pending_exit") and st["next_open"] is not None:   # flip at this close: out at the next open
                tr.update(exit_t=bar_t + 2 * H4, exit_p=st["next_open"], reason="TREND_FLIP",
                          r_gross=(1 if p["side"] == "L" else -1) * (st["next_open"] - tr["entry_p"]) / tr["risk"])
                self._close(p, tr["exit_t"], tr["exit_p"], "TREND_FLIP", K.net_r(tr, st["ft"], st["fr"]))
            return
        if not broker.live:
            self.event(f"{p['sym']}: live position cannot be managed (no Bybit client); only its exchange stop protects it", "ERROR")
            return
        live_pos = broker.position(p["sym"])
        if live_pos is None: return                                       # API problem: try again next bar
        if not live_pos or not self._is_ours(p, live_pos):
            # ours is gone (its stop, or closed by hand), or the coin's position is not the one we opened: never touch it
            rows = self._own_closed(broker, p)
            if rows:
                pnl = sum(float(x.get("closedPnl") or 0) for x in rows)
                last = max(rows, key=lambda x: int(x.get("updatedTime") or 0))
                px = float(last.get("avgExitPrice") or p["stop"])
                at_stop = abs(px - p["stop"]) <= 0.15 * abs(p["entry_p"] - p["stop"])
                self._close(p, min(now, int(last.get("updatedTime") or now)), px, "STOP" if at_stop else "CLOSED_ON_EXCHANGE",
                            pnl / p["risk_usd"] if p["risk_usd"] else None, pnl)
                if live_pos: self.event(f"{p['sym']}: the position now open on this coin is not Kalman's (another strategy or a manual trade); left alone")
            elif live_pos:
                self._close(p, now, None, "EXTERNAL_CHANGE", None)
                self.event(f"{p['sym']}: the exchange position was changed outside Kalman (expected {'long' if p['side'] == 'L' else 'short'} "
                           f"{p['qty']} at {p['entry_p']:.6g}, found {live_pos.get('side')} {live_pos.get('size')} at {live_pos.get('avgPrice')}); "
                           f"Kalman stopped managing it and its exchange stop stays", "ERROR")
            else:
                self._close(p, now, None, "CLOSED_ON_EXCHANGE", None)
                self.event(f"{p['sym']}: closed on the exchange, but no closed-P&L record of it was found; result unknown", "WARN")
            return
        held = (bar_t - (p["signal_t"] + H4)) // H4 + 1                     # closed bars since the entry bar opened
        reason = "TREND_FLIP" if K.exit_now(p["side"], st["z"][-1]) else "TIME" if held >= K.HOLD else None
        if reason:
            r = broker.close(p["sym"], p["side"], p["qty"])
            if (r or {}).get("retCode") == 0:
                rows = self._own_closed(broker, p)
                pnl = sum(float(x.get("closedPnl") or 0) for x in rows) if rows else None
                px = float(max(rows, key=lambda x: int(x.get("updatedTime") or 0)).get("avgExitPrice") or 0) if rows else None
                self._close(p, now, px or st["next_open"] or st["c"][-1], reason,
                            pnl / p["risk_usd"] if (pnl is not None and p["risk_usd"]) else None, pnl)
            else:
                self.event(f"LIVE close {p['sym']} failed: {r}", "ERROR")

    def _is_ours(self, p, lp):
        """the exchange position lp is still the one this engine opened: same side, same size, same average entry"""
        try: size, avg = float(lp.get("size") or 0), float(lp.get("avgPrice") or 0)
        except (TypeError, ValueError): return False
        step = (self.instr.get(p["sym"]) or {}).get("qty_step") or 0
        return (lp.get("side") == ("Buy" if p["side"] == "L" else "Sell") and abs(size - (p["qty"] or 0)) <= max(step / 2, 1e-9 * size)
                and abs(avg - p["entry_p"]) <= 1e-6 * p["entry_p"])

    def _own_closed(self, broker, p, tries=3):
        """closed-P&L rows of our position: after its entry and at its entry price, so another strategy's trade on the
        same coin is never booked as ours. The record can lag the fill by a few seconds."""
        for k in range(tries):
            rows = broker.closed_pnl(p["sym"], p["entry_t"] - 60000) or []
            mine = [x for x in rows if abs(float(x.get("avgEntryPrice") or 0) - p["entry_p"]) <= 1e-6 * p["entry_p"]]
            if mine: return mine
            if k + 1 < tries: time.sleep(2.0)
        return []

    def _close(self, p, exit_t, exit_p, reason, r_net, pnl=None):
        p.update(status="CLOSED", exit_t=int(exit_t), exit_p=exit_p, reason=reason, r_net=r_net,
                 pnl_usd=pnl if pnl is not None else (p["equity_at_entry"] * p["risk_pct"] / 100 * r_net if r_net is not None else None))
        self.store.save_position(p)
        self.event(f"{p['mode']} exit {p['sym']} {'long' if p['side'] == 'L' else 'short'} ({reason})"
                   + (f" at {exit_p:.6g}" if exit_p is not None else "") + (f": {r_net:+.2f}R" if r_net is not None else ": result unknown"))

    def book(self, t_now):
        eq = self.store.get("paper_equity", 10.0)
        for p in self.store.positions("CLOSED"):
            if p["mode"] == "PAPER" and not p["booked"] and p["exit_t"] <= t_now:
                eq += p["pnl_usd"] or 0.0; p["booked"] = 1; self.store.save_position(p)
        self.store.put("paper_equity", eq)

    def slots_used(self, t_now):
        return [p for p in self.store.positions() if p["status"] == "OPEN" or (p["exit_t"] or 0) > t_now]

    def try_entry(self, sym, bar_t, now, s, broker):
        st = self.state[sym]; sig = st["sig"]; side = sig["side"]; te = bar_t + H4
        used = self.slots_used(te)
        if len(used) >= s["maxOpen"]: self.event(f"{sym} {side} signal skipped: {len(used)} positions open"); return
        if any(p["sym"] == sym for p in used): return
        if side == "L" and s["oiFilter"]:
            pts = (self.oi_source or self.m.open_interest_1h)(sym)
            above = K.oi_above_mean(pts, bar_t)
            if above is False: self.event(f"{sym} long skipped: open interest below its 30-day mean (filter on)"); return
        if s["corrCap"]:
            mine = K.returns_by_time(st["t"], st["c"], bar_t)
            others = [K.returns_by_time(self.state[p["sym"]]["t"], self.state[p["sym"]]["c"], bar_t)
                      for p in used if p["side"] == side and p["sym"] in self.state]
            if K.corr_cap_blocks(mine, others): self.event(f"{sym} {side} skipped: correlation cap"); return
        if not broker.live:
            if st["next_open"] is None: self.event(f"{sym} paper entry skipped: next bar's open not available", "WARN"); return
            eq = self.store.get("paper_equity", 10.0)
            if eq < 1: return
            ep = st["next_open"]; stop = K.initial_stop(side, st["c"][-1], st["a"][-1], ep)
            p = dict(id=f"P-{sym}-{bar_t}-{uuid.uuid4().hex[:6]}", sym=sym, side=side, mode="PAPER", signal_t=bar_t, entry_t=te, entry_p=ep,
                     stop=stop, qty=None, risk_usd=eq * s["riskPct"] / 100, equity_at_entry=eq, risk_pct=s["riskPct"], status="OPEN",
                     exit_t=None, exit_p=None, reason=None, r_net=None, pnl_usd=None, booked=0, order_id=None, note=None)
            self.store.save_position(p)
            self.event(f"PAPER entry {sym} {'long' if side == 'L' else 'short'} at {ep:.6g}, stop {stop:.6g}, risk ${p['risk_usd']:.4f}")
            return
        # LIVE. The account may be shared with the strategy runner and the SBGZ auto-orders: a coin that has a position
        # or a resting order is not ours to trade.
        held, orders = broker.has_position(sym), broker.has_orders(sym)
        if held is None or orders is None: self.event(f"{sym} live entry skipped: the account check failed", "WARN"); return
        if held or orders:
            self.event(f"{sym} live entry skipped: the account already {'holds this coin' if held else 'has an order on this coin'} "
                       f"(another strategy or a manual trade)", "WARN"); return
        eq = broker.equity(self.store)
        if not eq: return
        ins = self.instr.get(sym, {}); px = st["next_open"] or st["c"][-1]
        stop = K.initial_stop(side, st["c"][-1], st["a"][-1], px)
        qty, risk_usd, note = K.order_qty(eq, s["riskPct"], px, stop, ins.get("qty_step", 0.001), ins.get("min_qty", 0),
                                          ins.get("min_notional", 5.0), s["maxRiskOnMinPct"])
        if qty <= 0: self.event(f"{sym} live entry skipped: {note}", "WARN"); return
        tick, step = ins.get("tick", 0.0001), ins.get("qty_step", 0.001)
        stop_r = round_step(stop, tick, "down" if side == "L" else "up")
        link = f"kt-{sym[:-4] if sym.endswith('USDT') else sym}-{bar_t // 60000}"[:36]     # one order per signal, also on a retry
        r = broker.open(sym, side, qty, stop_r, int(s["leverage"]), link)
        if (r or {}).get("retCode") not in (0, 110072): self.event(f"LIVE order {sym} rejected: {r}", "ERROR"); return
        lp = None
        for _ in range(12):                                               # wait for the fill to show up in the account
            lp = broker.position(sym)
            if lp: break
            time.sleep(0.5)
        if not lp: self.event(f"LIVE order {sym} accepted but no position found: check the account", "ERROR"); return
        size = float(lp.get("size") or 0)
        if size > qty + step / 2:
            # another order on this coin filled at the same moment and the exchange merged the two into one position
            rc = broker.close(sym, side, qty)
            self.event(f"{sym}: another order on this coin filled together with Kalman's and the exchange merged them; Kalman closed "
                       f"its own {qty}{'' if (rc or {}).get('retCode') == 0 else ' -- THAT CLOSE FAILED, check the account'} and leaves the rest alone", "ERROR")
            return
        fill = float(lp.get("avgPrice") or px)
        stop2 = round_step(K.initial_stop(side, st["c"][-1], st["a"][-1], fill), tick, "down" if side == "L" else "up")
        try: has_stop = float(lp.get("stopLoss") or 0) > 0
        except (TypeError, ValueError): has_stop = False
        if abs(stop2 - stop_r) > 1e-12 or not has_stop:
            for _ in range(3):
                rs = broker.set_stop(sym, stop2)
                if (rs or {}).get("retCode") in (0, 34040): stop_r, has_stop = stop2, True; break
                time.sleep(0.5)
        if not has_stop:
            # an unprotected position is worse than a closed one
            rc = broker.close(sym, side, size)
            self.event(f"LIVE {sym}: the stop could not be set, so the position was closed"
                       f"{'' if (rc or {}).get('retCode') == 0 else ' -- THAT CLOSE FAILED, close it by hand'}", "ERROR")
            return
        p = dict(id=f"L-{sym}-{bar_t}-{uuid.uuid4().hex[:6]}", sym=sym, side=side, mode="LIVE", signal_t=bar_t, entry_t=now, entry_p=fill,
                 stop=stop_r, qty=size, risk_usd=abs(fill - stop_r) * size, equity_at_entry=eq,
                 risk_pct=s["riskPct"], status="OPEN", exit_t=None, exit_p=None, reason=None, r_net=None, pnl_usd=None, booked=1,
                 order_id=(r.get("result") or {}).get("orderId") or link, note=note)
        self.store.save_position(p)
        self.event(f"LIVE entry {sym} {'long' if side == 'L' else 'short'} qty {p['qty']} at {fill:.6g}, stop {stop_r:.6g} ({note})")

    # ------------------------------------------------ between bars
    def light(self, now):
        self.last_light = now
        try:
            if self.store.positions("OPEN"):
                self.prices = self.m.tickers() or self.prices
        except Exception: pass
        if os.path.exists(SNAPSHOT_PATH) and self.write_files:
            try:
                snap = json.load(open(SNAPSHOT_PATH))
                for p in snap.get("positions", []):
                    px = self.prices.get(p["sym"])
                    if px and p.get("risk_per_unit"):
                        p["price"] = px; p["r_now"] = (1 if p["side"] == "L" else -1) * (px - p["entry_p"]) / p["risk_per_unit"]
                snap["prices_at"] = now
                self._write_json(SNAPSHOT_PATH, snap)
            except Exception: pass

    # ------------------------------------------------ dashboard files
    def _write_json(self, path, obj):
        if not self.write_files: return
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f: json.dump(obj, f, separators=(",", ":"))
        os.replace(tmp, path)

    def write_snapshot(self, s, mode, broker, bar_t, now):
        active = mode == "kalman"
        execution = ("LIVE" if broker.live else "PAPER") if active else "STANDBY"
        allp = self.store.positions(); open_p = [p for p in allp if p["status"] == "OPEN"]
        closed = [p for p in allp if p["status"] == "CLOSED" and p["r_net"] is not None]
        rs = [p["r_net"] for p in closed]
        positions = []
        for p in open_p:
            st = self.state.get(p["sym"], {}); price = self.prices.get(p["sym"]) or (st.get("c") or [None])[-1]
            rpu = abs(p["entry_p"] - p["stop"])
            positions.append(dict(sym=p["sym"], side=p["side"], mode=p["mode"], entry_t=p["entry_t"], entry_p=p["entry_p"], stop=p["stop"],
                                  price=price, risk_per_unit=rpu, r_now=((1 if p["side"] == "L" else -1) * (price - p["entry_p"]) / rpu) if (price and rpu) else None,
                                  bars_held=max(0, (bar_t - p["signal_t"]) // H4), z=(st.get("z") or [None])[-1], risk_usd=p["risk_usd"], qty=p["qty"]))
        radar = []
        for sym, st in sorted(self.state.items()):
            z = st["z"]; zz = z[-1]
            radar.append(dict(sym=sym, price=st["c"][-1], z=zz, z_prev=z[-2],
                              state="UP" if zz > K.Z_IN else "DOWN" if zz < -K.Z_IN else "NEUTRAL",
                              favg=st["favg"], signal=(st["sig"] or {}).get("side"), signal_ok=(st["sig"] or {}).get("ok"),
                              signal_why=(st["sig"] or {}).get("why"),
                              in_trade=(st["open_tr"] or {}).get("side"), last_trade=self._trade_brief(st, st["chain"][-1]) if st["chain"] else None))
        bt = self.state.get("BTCUSDT")
        btc_trend = None
        if bt:
            tr = K.daily_trend(bt["t"], bt["c"]); btc_trend = tr[-1]
        snap = dict(engine="Kalman Trend (funding-aware, 4H)", version=VERSION, updated_at=now, mode_selected=active, execution=execution,
                    live_env=self.live_env, live_why=self.live_why, live_requested=bool(s["live"]), settings=s, last_bar_close=bar_t + H4, next_bar_close=bar_t + 2 * H4,
                    btc=dict(trend=btc_trend, price=bt["c"][-1] if bt else None), symbols=len(self.state),
                    account=dict(paper_equity=self.store.get("paper_equity"), paper_start=self.store.get("paper_start"),
                                 live_equity=broker.equity(self.store) if broker.live else None,
                                 open_count=len(open_p), slots_used=len(self.slots_used(bar_t + H4)), max_open=s["maxOpen"],
                                 open_risk_pct=sum(p["risk_pct"] for p in open_p), closed_trades=len(rs),
                                 win_rate=(sum(1 for r in rs if r > 0) / len(rs) * 100) if rs else None,
                                 avg_r=(sum(rs) / len(rs)) if rs else None, total_r=sum(rs) if rs else 0.0),
                    positions=positions, radar=radar,
                    recent_trades=[dict(sym=p["sym"], side=p["side"], mode=p["mode"], entry_t=p["entry_t"], entry_p=p["entry_p"], exit_t=p["exit_t"],
                                        exit_p=p["exit_p"], reason=p["reason"], r_net=p["r_net"], pnl_usd=p["pnl_usd"]) for p in closed[-30:]][::-1],
                    shadow=self.store.shadow_stats(), events=self.events[-20:][::-1])
        self._write_json(SNAPSHOT_PATH, snap)

    def _trade_brief(self, st, tr):
        return dict(side=tr["side"], signal_t=st["t"][tr["signal_i"]], entry_t=tr["entry_t"], entry_p=tr["entry_p"], stop=tr["stop"],
                    exit_t=tr["exit_t"], exit_p=tr["exit_p"], reason=tr["reason"],
                    r_net=K.net_r(tr, st["ft"], st["fr"]) if tr["exit_t"] is not None else None)

    def write_indicators(self, bar_t, keep=400):
        for sym, st in self.state.items():
            n = len(st["c"]); k0 = max(0, n - keep)
            bars = [[st["t"][i], round(st["level"][i], 10), None if math.isnan(st["z"][i]) else round(st["z"][i], 4)] for i in range(k0, n)]
            trades = [self._trade_brief(st, tr) for tr in st["chain"] if (tr["exit_t"] or bar_t + H4) >= st["t"][k0]]
            pos = [p for p in self.store.positions("OPEN") if p["sym"] == sym]
            self._write_json(os.path.join(IND_DIR, f"{sym}.json"), dict(sym=sym, updated_at=self.clock(), bar_close=bar_t + H4, bars=bars, trades=trades,
                                                                         position=(dict(side=pos[0]["side"], entry_p=pos[0]["entry_p"], stop=pos[0]["stop"],
                                                                                        entry_t=pos[0]["entry_t"], mode=pos[0]["mode"]) if pos else None),
                                                                         signal=st["sig"], favg=st["favg"]))

    def run(self, poll=30):
        log(f"Kalman Trend engine {VERSION} started: {len(self.symbols)} coins, market data {getattr(self.m, 'base', '?')}, "
            f"live orders {'allowed: ' + self.live_why + ' (still needs the dashboard switch)' if self.live_env else 'disabled (paper only): ' + self.live_why}")
        while True:
            try: self.step()
            except KeyboardInterrupt: break
            except Exception as e: log(f"step error: {e}", "ERROR"); traceback.print_exc()
            time.sleep(poll)


if __name__ == "__main__":
    KalmanTrendEngine().run()
