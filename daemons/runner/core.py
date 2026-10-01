"""Shared infrastructure for the MASIS strategy runner.

  * Bybit server clock (the local clock is not trusted)
  * public market data with an endpoint fallback
  * durable state (Supabase / Redis / local file through backend_lib.kv)
  * exchange access, live (Bybit V5) or paper (simulated fills from live prices)
  * account-level risk limits: caps on open risk, daily loss, drawdown, gross exposure,
    one position per coin, a halt switch and per-strategy pauses

Pure Python (no numpy): the production image installs only requirements.txt.
"""
import json
import math
import os
import sys
import threading
import time
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from backend_lib.kv import kv_get_json, kv_set_json  # noqa: E402

VERSION = "MASIS-RUNNER-v1"
M1, M15, H1, H4, DAY = 60_000, 900_000, 3_600_000, 14_400_000, 86_400_000
PAPER_FEE = 0.00055        # Bybit taker fee per side, charged on paper fills
STATE_KEY, CONTROL_KEY = "masis_runner_state", "masis_runner_control"


def _truthy(name, default=""):
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes", "on")


def _f(name, default):
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return float(default)


# Coin universes.  CORE20 is where the strategies were researched; EXTRA22 were downloaded later and used
# only to test whether the rules generalise.  The group is stored with every trade so results can be split.
CORE20 = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "LINKUSDT", "SEIUSDT", "AVAXUSDT", "DOGEUSDT", "BNBUSDT", "ADAUSDT",
          "DOTUSDT", "LTCUSDT", "NEARUSDT", "APTUSDT", "SUIUSDT", "ARBUSDT", "OPUSDT", "INJUSDT", "FILUSDT", "UNIUSDT"]
EXTRA22 = ["1000PEPEUSDT", "AAVEUSDT", "ALGOUSDT", "ATOMUSDT", "BCHUSDT", "CRVUSDT", "ENAUSDT", "ETCUSDT", "GALAUSDT", "HBARUSDT",
           "ICPUSDT", "IMXUSDT", "MANAUSDT", "RENDERUSDT", "RUNEUSDT", "SANDUSDT", "STXUSDT", "TAOUSDT", "TIAUSDT", "TRXUSDT",
           "WLDUSDT", "XLMUSDT"]


def group_of(sym):
    return "core20" if sym in CORE20 else "extra22"


# Percentages in the environment are written as percent (0.25 = 0.25%) and stored as fractions.
CFG = dict(
    risk_trend=_f("RUNNER_RISK_TREND_PCT", 0.25) / 100,       # equity risked per trend trade
    risk_snap=_f("RUNNER_RISK_SNAP_PCT", 0.50) / 100,         # equity risked per snap-back trade
    max_open_trend=_f("RUNNER_MAX_OPEN_RISK_TREND_PCT", 5.5) / 100,
    max_open_snap=_f("RUNNER_MAX_OPEN_RISK_SNAP_PCT", 2.0) / 100,
    max_open_total=_f("RUNNER_MAX_OPEN_RISK_TOTAL_PCT", 7.5) / 100,
    day_loss_halt=_f("RUNNER_DAILY_LOSS_HALT_PCT", 3.0) / 100,  # no new entries for the rest of the UTC day
    dd_halt=_f("RUNNER_DRAWDOWN_HALT_PCT", 25.0) / 100,        # sticky halt until resumed by hand
    max_gross_x=_f("RUNNER_MAX_GROSS_NOTIONAL_X", 6.0),         # total notional / equity
    max_pos_x=_f("RUNNER_MAX_POSITION_NOTIONAL_X", 3.0),        # one position's notional / equity
    leverage=int(_f("RUNNER_LEVERAGE", 10)),
    paper_equity=_f("RUNNER_PAPER_EQUITY", 2000),
    data_dir=os.environ.get("RUNNER_DATA_DIR") or os.path.join(ROOT, "scratch", "runner"),
    allow_real=_truthy("MASIS_ALLOW_REAL_MONEY"),
)


# ─── clock and public data ───────────────────────────────────────────────────
def _bases():
    out = []
    for b in (os.environ.get("RUNNER_PUBLIC_URL") or "https://api.bybit.com",
              (os.environ.get("BYBIT_BASE_URL") or "https://api-demo.bybit.com")):
        b = b.strip().rstrip("/")
        if b and b not in out:
            out.append(b)
    return out


def pub_get(path, **q):
    """Public Bybit GET.  Returns the parsed JSON (retCode 0) or {} -- never raises."""
    qs = urllib.parse.urlencode(q)
    for base in _bases():
        for k in range(3):
            try:
                req = urllib.request.Request(f"{base}{path}?{qs}", headers={"User-Agent": "MASIS-runner/1"})
                with urllib.request.urlopen(req, timeout=15) as r:
                    j = json.loads(r.read().decode())
                if j.get("retCode") == 0:
                    return j
                break                       # a real API error: another base will not help much, try next base once
            except Exception:
                time.sleep(0.5 + k)
    return {}


_clock = {"off": 0, "at": 0.0}


def now_ms():
    """Bybit server time in ms."""
    if time.time() - _clock["at"] > 600:
        try:
            srv = int(pub_get("/v5/market/time")["result"]["timeNano"]) // 1_000_000
            _clock["off"] = srv - int(time.time() * 1000)
            _clock["at"] = time.time()
        except Exception:
            pass
    return int(time.time() * 1000) + _clock["off"]


def iso(ms):
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(ms / 1000))


def utc_day(ms):
    return time.strftime("%Y-%m-%d", time.gmtime(ms / 1000))


# ─── logging ─────────────────────────────────────────────────────────────────
class Log:
    def __init__(self, path=None, quiet=False):
        self.path = path or os.path.join(CFG["data_dir"], "runner_log.jsonl")
        self.quiet = quiet
        self._seen = set()
        os.makedirs(os.path.dirname(self.path), exist_ok=True)

    def __call__(self, type_, **kw):
        ev = {"ts": now_ms(), "v": VERSION, "type": type_, **kw}
        try:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(ev, default=str) + "\n")
        except Exception:
            pass
        if not self.quiet:
            print(json.dumps(ev, default=str), flush=True)
        return ev

    def once(self, key, type_, **kw):
        """Log only the first time `key` is seen (keeps repeated skip reasons out of the log)."""
        if key in self._seen:
            return None
        if len(self._seen) > 5000:
            self._seen.clear()
        self._seen.add(key)
        return self(type_, **kw)


# ─── durable state ───────────────────────────────────────────────────────────
def _new_acct():
    return {"day": {"date": "", "start_equity": 0.0}, "peak_equity": 0.0, "halt": {}}


def _new_state():
    return {"version": 2, "positions": {}, "paper_positions": {}, "history": [], "trend": {"coin_bar": {}, "cycle": 0},
            "snap": {"last_bar": {}, "last_kept": {}, "cycle": 0}, "paused": {}, "resume_seen": {},
            "acct": {"live": _new_acct(), "paper": _new_acct()}, "signals": [], "scan": {}, "paper": {"pos": {}, "closed": []}, "agg": {},
            "started": 0}


class State:
    """JSON state saved through backend_lib.kv (cloud when configured, plus a local file)."""

    def __init__(self, key=STATE_KEY, store=True):
        self.key, self.store = key, store
        self.lock = threading.RLock()
        d = (_store_get(key, None) if store else None) or {}
        self.d = _new_state()
        for k, v in d.items():
            self.d[k] = v
        for k, v in _new_state().items():      # forward-compatible: add keys introduced by newer versions
            self.d.setdefault(k, v)

    def save(self):
        if not self.store:
            return
        with self.lock:
            try:
                _store_set(self.key, self.d)
            except Exception as e:
                print(f"[state] save failed: {e}", flush=True)

    def __getitem__(self, k):
        return self.d[k]

    def __setitem__(self, k, v):
        self.d[k] = v


def _file_backend():
    """RUNNER_STATE_BACKEND=file keeps state and control in local files only (never the shared cloud store).
    Use it for local dry runs; production uses the default shared store so state survives redeploys."""
    return os.environ.get("RUNNER_STATE_BACKEND", "kv").strip().lower() == "file"


def _store_get(key, default=None):
    if _file_backend():
        try:
            with open(os.path.join(CFG["data_dir"], f"{key}.json"), "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return default
    return kv_get_json(key, default)


def _store_set(key, value):
    if _file_backend():
        os.makedirs(CFG["data_dir"], exist_ok=True)
        tmp = os.path.join(CFG["data_dir"], f"{key}.json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(value, f, default=str)
        os.replace(tmp, os.path.join(CFG["data_dir"], f"{key}.json"))
        return
    kv_set_json(key, value)


def get_control():
    try:
        return _store_get(CONTROL_KEY, {}) or {}
    except Exception:
        return {}


def control_resume(name):
    """Ask the runner to resume a paused strategy ('trend4h', 'snapback') or everything ('all')."""
    ctl = get_control()
    pause, resume = dict(ctl.get("pause") or {}), dict(ctl.get("resume") or {})
    for n in (["trend4h", "snapback"] if name == "all" else [name]):
        pause[n] = False
    resume[name] = now_ms()
    return set_control(pause=pause, resume=resume, halt=False if name == "all" else ctl.get("halt", False))


def set_control(**kw):
    c = get_control()
    c.update(kw)
    c["updated"] = now_ms()
    _store_set(CONTROL_KEY, c)
    return c


# ─── rounding and sizing ─────────────────────────────────────────────────────
def _decimals(step):
    return max(0, len(f"{step:.10f}".rstrip("0").split(".")[1])) if step < 1 else 0


def floor_step(x, step):
    """Largest multiple of step that is <= x, without float noise (98.0, not 98.00000000000001)."""
    return round(math.floor(x / step + 1e-9) * step, _decimals(step) + 2) if step else x


def ceil_step(x, step):
    return round(math.ceil(x / step - 1e-9) * step, _decimals(step) + 2) if step else x


def fmt_step(x, step):
    if not step:
        return repr(float(x))
    dec = max(0, len(f"{step:.10f}".rstrip("0").split(".")[1])) if step < 1 else 0
    return f"{x:.{dec}f}"


def size_qty(equity, risk_frac, entry, stop, spec, max_pos_x):
    """Quantity that risks `risk_frac` of equity between entry and stop, within the exchange's limits.
    Returns 0.0 when the trade cannot be sized legally."""
    dist = abs(entry - stop)
    if dist <= 0 or entry <= 0 or equity <= 0:
        return 0.0
    step = spec.get("qty_step") or 0.0
    qty = floor_step(equity * risk_frac / dist, step)
    qty = min(qty, floor_step(equity * max_pos_x / entry, step))
    if spec.get("max_qty"):
        qty = min(qty, floor_step(spec["max_qty"], step))
    if qty < (spec.get("min_qty") or 0) or qty * entry < (spec.get("min_notional") or 0) or qty <= 0:
        return 0.0
    return qty


def stop_price(raw, side, tick):
    """Round a stop to the tick, never tighter than the raw level."""
    return floor_step(raw, tick) if side == 1 else ceil_step(raw, tick)


def target_price(raw, side, tick):
    """Round a target to the tick, never further than the raw level."""
    return floor_step(raw, tick) if side == 1 else ceil_step(raw, tick)


# ─── exchange ────────────────────────────────────────────────────────────────
class Exchange:
    """Live Bybit V5 (demo or real) or paper.  Every method returns plain data and never raises."""

    def __init__(self, paper, state, log):
        self.paper, self.state, self.log = paper, state, log
        self.client = None
        self._spec, self._lev = {}, set()
        if not paper:
            from backend_lib.bybit_client import get_client
            self.client, err = get_client()
            if self.client is None:
                raise RuntimeError(f"Bybit client unavailable: {err}")
            if "api-demo" not in self.client.base_url and not CFG["allow_real"]:
                raise RuntimeError(f"{self.client.base_url} is not the demo endpoint. Set MASIS_ALLOW_REAL_MONEY=1 to trade real money.")

    @property
    def endpoint(self):
        return "paper" if self.paper else self.client.base_url

    # market data -------------------------------------------------------------
    def ticker(self, sym):
        x = (pub_get("/v5/market/tickers", category="linear", symbol=sym).get("result", {}).get("list") or [{}])[0]
        try:
            return {"bid": float(x["bid1Price"]), "ask": float(x["ask1Price"]), "last": float(x["lastPrice"])}
        except Exception:
            return None

    def spec(self, sym):
        if sym not in self._spec:
            it = (pub_get("/v5/market/instruments-info", category="linear", symbol=sym).get("result", {}).get("list") or [None])[0]
            if not it:
                return None
            lot, pf, lv = it.get("lotSizeFilter", {}), it.get("priceFilter", {}), it.get("leverageFilter", {})
            self._spec[sym] = {"qty_step": float(lot.get("qtyStep") or 0), "min_qty": float(lot.get("minOrderQty") or 0),
                               "max_qty": float(lot.get("maxMktOrderQty") or lot.get("maxOrderQty") or 0),
                               "min_notional": float(lot.get("minNotionalValue") or 5), "tick": float(pf.get("tickSize") or 0),
                               "max_lev": float(lv.get("maxLeverage") or 10)}
        return self._spec[sym]

    # account -----------------------------------------------------------------
    def equity(self):
        if self.paper:
            return CFG["paper_equity"] + sum(h.get("pnl_usd", 0.0) for h in self.state["history"] if h.get("paper"))
        r = self.client.get_wallet_balance()
        try:
            return float((r["result"]["list"][0])["totalEquity"])
        except Exception:
            return 0.0

    def positions(self):
        """{symbol: {side(+1/-1), size, avg, stop, tp, value}} for every open position in the account."""
        out = {}
        if self.paper:
            for sym, p in self.state["paper"]["pos"].items():
                out[sym] = dict(side=p["side"], size=p["size"], avg=p["avg"], stop=p["stop"], tp=p.get("tp"), value=p["size"] * p["avg"])
            return out
        r = self.client.get_positions()
        if r.get("retCode") != 0:
            return None                       # unknown -- callers must not assume "flat"
        for p in r.get("result", {}).get("list", []):
            try:
                size = float(p.get("size") or 0)
            except ValueError:
                size = 0
            if size > 0:
                out[p["symbol"]] = dict(side=1 if p["side"] == "Buy" else -1, size=size, avg=float(p.get("avgPrice") or 0),
                                        stop=float(p["stopLoss"]) if p.get("stopLoss") not in (None, "", "0") else None,
                                        tp=float(p["takeProfit"]) if p.get("takeProfit") not in (None, "", "0") else None,
                                        value=float(p.get("positionValue") or 0))
        return out

    def order_symbols(self):
        """Symbols with a resting order.  None when it cannot be determined."""
        if self.paper:
            return set()
        r = self.client.get_open_orders()
        if r.get("retCode") != 0:
            return None
        return {o["symbol"] for o in r.get("result", {}).get("list", [])}

    # trading -----------------------------------------------------------------
    def ensure_leverage(self, sym):
        if self.paper or sym in self._lev:
            return
        sp = self.spec(sym) or {}
        lev = max(1, min(CFG["leverage"], int(sp.get("max_lev", CFG["leverage"]))))
        r = self.client.set_leverage(sym, lev)
        if r.get("retCode") in (0, 110043):
            self._lev.add(sym)
        else:
            self.log("warn", what="set_leverage", symbol=sym, ret=r.get("retCode"), msg=r.get("retMsg"))

    def market_order(self, sym, side, qty, sl, tp, link):
        """Market entry with broker-side stop (and optional target) attached.
        Returns (ok, retCode, message).  A duplicate link id means an earlier attempt already went through."""
        sp = self.spec(sym)
        if self.paper:
            tk = self.ticker(sym)
            if not tk:
                return False, -1, "no ticker"
            px = tk["ask"] if side == 1 else tk["bid"]
            self.state["paper"]["pos"][sym] = dict(side=side, size=qty, avg=px, stop=sl, tp=tp, opened=now_ms(), checked=now_ms(), link=link)
            return True, 0, "paper"
        r = self.client.place_order("linear", sym, "Buy" if side == 1 else "Sell", "Market", fmt_step(qty, sp["qty_step"]),
                                    tp=fmt_step(tp, sp["tick"]) if tp else None, sl=fmt_step(sl, sp["tick"]), order_link_id=link)
        return r.get("retCode") == 0 or r.get("retCode") == 110072, r.get("retCode"), r.get("retMsg", "")

    def set_stop(self, sym, sl=None, tp=None):
        sp = self.spec(sym)
        if self.paper:
            p = self.state["paper"]["pos"].get(sym)
            if not p:
                return False
            if sl is not None:
                p["stop"] = sl
            if tp is not None:
                p["tp"] = tp
            return True
        r = self.client.set_trading_stop("linear", sym, stop_loss=fmt_step(sl, sp["tick"]) if sl is not None else None,
                                         take_profit=fmt_step(tp, sp["tick"]) if tp is not None else None)
        return r.get("retCode") in (0, 34040)       # 34040 = not modified (already at that level)

    def close(self, sym, side, qty):
        sp = self.spec(sym)
        if self.paper:
            tk = self.ticker(sym)
            p = self.state["paper"]["pos"].pop(sym, None)
            if tk and p:
                self._paper_exit(sym, p, tk["bid"] if p["side"] == 1 else tk["ask"], "market")
            return True
        r = self.client.close_position("linear", sym, "Buy" if side == 1 else "Sell", fmt_step(qty, sp["qty_step"]))
        return r.get("retCode") == 0

    def closed_pnl(self, sym, since_ms):
        """Closed-position records for `sym` newer than since_ms: [{'exit', 'pnl', 'ts'}]."""
        if self.paper:
            return [c for c in self.state["paper"]["closed"] if c["sym"] == sym and c["ts"] >= since_ms]
        r = self.client.signed_request("GET", "/v5/position/closed-pnl", {"category": "linear", "symbol": sym, "limit": "20"})
        rows = []
        for x in r.get("result", {}).get("list", []):
            if int(x.get("updatedTime", 0)) >= since_ms:
                rows.append({"sym": sym, "exit": float(x.get("avgExitPrice") or 0), "pnl": float(x.get("closedPnl") or 0), "ts": int(x["updatedTime"])})
        return rows

    # paper engine ------------------------------------------------------------
    def _paper_exit(self, sym, p, px, how):
        pnl = p["side"] * (px - p["avg"]) * p["size"] - PAPER_FEE * (p["avg"] + px) * p["size"]
        self.state["paper"]["closed"].append(dict(sym=sym, exit=px, pnl=pnl, ts=now_ms(), how=how))
        del self.state["paper"]["closed"][:-200]

    def paper_step(self):
        """Fill paper stops / targets from 1-minute candles since the last check (stop wins ties)."""
        if not self.paper:
            return
        for sym, p in list(self.state["paper"]["pos"].items()):
            rows = pub_get("/v5/market/kline", category="linear", symbol=sym, interval=1, start=int(p["checked"]) - M1, limit=1000).get("result", {}).get("list", [])
            for b in sorted(rows, key=lambda b: int(b[0])):
                hi, lo = float(b[2]), float(b[3])
                hit_sl = (lo <= p["stop"]) if p["side"] == 1 else (hi >= p["stop"])
                hit_tp = p.get("tp") and ((hi >= p["tp"]) if p["side"] == 1 else (lo <= p["tp"]))
                if hit_sl or hit_tp:
                    self._paper_exit(sym, p, p["stop"] if hit_sl else p["tp"], "stop" if hit_sl else "target")
                    del self.state["paper"]["pos"][sym]
                    break
            else:
                p["checked"] = now_ms()


# ─── account context and risk governor ──────────────────────────────────────
class Ctx:
    """One trading account (live or paper): its exchange, its positions and its risk limits."""

    def __init__(self, ex, state, log):
        self.ex, self.state, self.log = ex, state, log
        self.mode = "paper" if ex.paper else "live"
        self.gov = Governor(self)

    @property
    def pos(self):
        return self.state["positions"] if self.mode == "live" else self.state["paper_positions"]

    @property
    def acct(self):
        return self.state["acct"][self.mode]


class Governor:
    def __init__(self, ctx):
        self.ctx = ctx
        self.equity = 0.0
        self.snapshot = None
        self.halted = ""
        self.at = 0.0

    def fresh(self, max_age=20.0):
        """Re-read the account if the last read is older than max_age seconds."""
        if time.time() - self.at > max_age:
            self.refresh()
        return self.snapshot

    def refresh(self):
        """Read equity and positions, roll the daily baseline, evaluate the halts.  Returns the snapshot."""
        ctx = self.ctx
        ex, st, log, a = ctx.ex, ctx.state, ctx.log, ctx.acct
        eq = ex.equity()
        pos, ords = ex.positions(), ex.order_symbols()
        self.at = time.time()
        self.snapshot = {"positions": pos, "orders": ords, "ok": eq > 0 and pos is not None and ords is not None}
        if eq <= 0:
            self.halted = "equity unavailable"
            return self.snapshot
        self.equity = eq
        now = now_ms()
        with st.lock:
            d = a["day"]
            if d["date"] != utc_day(now):
                d["date"], d["start_equity"] = utc_day(now), eq
            if eq > a["peak_equity"]:
                a["peak_equity"] = eq
            h = a["halt"]
            if not h.get("drawdown") and eq <= a["peak_equity"] * (1 - CFG["dd_halt"]):
                h["drawdown"] = {"since": now, "equity": eq, "peak": a["peak_equity"]}
                log("halt", account=ctx.mode, reason="drawdown", equity=eq, peak=a["peak_equity"], limit_pct=CFG["dd_halt"] * 100)
            if d["start_equity"] > 0 and eq <= d["start_equity"] * (1 - CFG["day_loss_halt"]) and h.get("day") != d["date"]:
                h["day"] = d["date"]
                log("halt", account=ctx.mode, reason="daily loss", equity=eq, day_start=d["start_equity"], limit_pct=CFG["day_loss_halt"] * 100)
        ctl = get_control()
        reasons = []
        if _truthy("MASIS_HALT") or ctl.get("halt"):
            reasons.append("halt switch" + (f" ({ctl.get('note')})" if ctl.get("note") else ""))
        if a["halt"].get("drawdown"):
            reasons.append("drawdown limit")
        if a["halt"].get("day") == a["day"]["date"]:
            reasons.append("daily loss limit")
        if not self.snapshot["ok"]:
            reasons.append("account state unreadable")
        self.halted = ", ".join(reasons)
        return self.snapshot

    def open_risk(self, strategy=None):
        return sum(p["risk_usd"] for p in self.ctx.pos.values() if strategy in (None, p["strategy"]))

    def can_open(self, strategy, sym, risk_usd, notional):
        if self.halted:
            return False, f"halted: {self.halted}"
        if strategy in self.ctx.state["paused"]:
            return False, f"{strategy} paused: {self.ctx.state['paused'][strategy]['reason']}"
        snap = self.snapshot
        if sym in snap["positions"]:
            return False, "coin already has a position"
        if sym in snap["orders"]:
            return False, "coin has a resting order"
        cap_s = self.equity * CFG["max_open_trend" if strategy == "trend4h" else "max_open_snap"]
        if self.open_risk(strategy) + risk_usd > cap_s + 1e-9:
            return False, f"{strategy} open-risk cap ({cap_s:.0f} USD)"
        if self.open_risk() + risk_usd > self.equity * CFG["max_open_total"] + 1e-9:
            return False, "total open-risk cap"
        gross = sum(p["value"] for p in snap["positions"].values()) + notional
        if gross > self.equity * CFG["max_gross_x"]:
            return False, "gross exposure cap"
        return True, ""


def apply_control(state, log):
    """Manual pauses / resumes from the control record (shared by both accounts)."""
    ctl = get_control()
    with state.lock:
        for name in ("trend4h", "snapback"):
            if ctl.get("pause", {}).get(name) and name not in state["paused"]:
                state["paused"][name] = {"reason": "paused by hand", "since": now_ms(), "auto": False}
                log("pause", strategy=name, reason="paused by hand")
        for name, ts in (ctl.get("resume") or {}).items():
            if ts > state["resume_seen"].get(name, 0):
                state["resume_seen"][name] = ts
                if name == "all":
                    for acct in state["acct"].values():
                        acct["halt"].pop("drawdown", None)
                    state["paused"].clear()
                    log("resume", what="everything")
                elif state["paused"].pop(name, None):
                    for k in [k for k in state["agg"] if k.startswith(f"{name}|live|")]:
                        state["agg"][f"archived|{ts}|{k}"] = state["agg"].pop(k)     # a resumed strategy gets a fresh record
                    log("resume", what=name, note="live statistics archived; the edge monitor starts counting again")
