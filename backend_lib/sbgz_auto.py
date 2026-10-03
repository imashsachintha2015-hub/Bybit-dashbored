"""SBGZ auto-orders: the dashboard server places, watches and cancels the SBGZ limit orders for you (demo / testnet accounts only).

A thread of the dashboard server (start_thread, once a minute) looks at the radar's strong-break setups and
  * places the same order as the "Place order" button (sbgz_trade.plan / place: post-only limit entry, limit take-profit, stop-market
    stop) for every setup that passes your filters (volume-confirmed only by default), within caps on waiting orders, open trades,
    total risk, orders per day, and a daily loss stop;
  * keeps each waiting order equal to its setup, the way the backtest treats the setup: the impulse extended -> the order is moved to
    the new levels; the setup ended (a new swing formed before the fill, the break is no longer 13 VU, ...) -> the order is cancelled;
  * optionally cancels a waiting order, or closes an open trade at the market, once BTC has moved 0.5R against it
    (the BTC rule of the radar history; backend_lib/sbgz.py btc_exit).
It does NOT cancel an order just because the price is falling or the trend turned: 4,258 backtested trades show no gain from that
(the fall is what fills a pullback order).

Modes (settings.mode): off = does nothing; preview = decides and logs what it WOULD do and sends nothing (needs no trust in the
exchange calls); live = sends orders. Going live needs an explicit confirmation. Only a DEMO / TESTNET base url is accepted.
Every order it places carries orderLinkId 'sbgz-<symbol>-<interval>-<L|S>-z<time>' ('z' = auto); it only ever manages those.
State (settings, tracked orders / trades, log) lives in the shared store (Supabase app_state key sbgz_auto; SBGZ_AUTO_BACKEND=file
keeps it in scratch/ for local runs). The store being unreadable means nothing is traded.
"""
import json
import os
import threading
import time

from . import sbgz, sbgz_trade as T

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STORE_KEY = "sbgz_auto"
MODES = ("off", "preview", "live")
# equity_usd: the account size every trade is calculated on (USDT; 0 = the account's real equity). 20 because that is the account the
# user wants to trade; at 20 USDT Bybit's smallest order (about 5 USDT) needs a risk of at least ~0.5% per trade (38 of the 42 radar coins
# fit at 1%), so risk_pct defaults to 1.
DEFAULTS = dict(mode="off", equity_usd=20.0, risk_pct=1.0, vol_only=True, intervals=["15", "60"], max_resting=8, max_open=6, max_open_risk_pct=5.0,
                max_new_per_cycle=3, max_orders_per_day=30, daily_stop_r=8.0, btc_close=True, btc_cancel=False, leverage=10)
LIMITS = dict(equity_usd=(0.0, 1_000_000.0), risk_pct=(0.05, 5.0), max_resting=(1, 20), max_open=(1, 20), max_open_risk_pct=(0.25, 10.0),
              max_new_per_cycle=(1, 5), max_orders_per_day=(1, 100), daily_stop_r=(1.0, 50.0), leverage=(1, 25))
FLOATS = ("equity_usd", "risk_pct", "max_open_risk_pct", "daily_stop_r")
BOOLS = ("vol_only", "btc_close", "btc_cancel")
LOG_MAX = 150
KEEP_DONE_MS = 3 * 86_400_000          # finished orders / trades stay listed for 3 days
RETRY_AFTER_MS = 600_000               # an order Bybit refused is not tried again for 10 minutes
SKIP_RETRY_MS = 300_000                # a setup that cannot be planned (price already through the entry, too small, ...) is looked at again after 5 minutes
LIVE_STATUSES = ("New", "PartiallyFilled", "Untriggered", "Created")
REPEAT_LOG_MS = 1_800_000              # the same message is logged once per half hour
CLEANUP_STOP_TYPES = ("PartialTakeProfit", "PartialStopLoss", "TakeProfit", "StopLoss", "TrailingStop")

LEASE_KEY = STORE_KEY + "_lease"
LEASE_MS = 150_000                     # a server that wrote its heartbeat within this time is considered alive
_INSTANCE = f"{os.getpid()}-{int(time.time())}-{os.urandom(3).hex()}"      # this server process

_lock = threading.RLock()              # the state dict and the store
_cycle_lock = threading.Lock()         # one pass / settings change / kill at a time
_state = None
_status = dict(ok=None, msg="not started yet", t=None, counts={})
_stop = threading.Event()
_thread = None
_last_log = {}
_skipped = {}                          # setup key -> reason it could not be placed (logged once)
_retry_at = {}                         # setup key -> ms before which it is not tried again
_marks = {}                            # order link -> where it stands now (price, R now, PnL, distance to the entry): refreshed every pass, not stored


# ── settings ─────────────────────────────────────────────────────────────────────────────────────────────────────────
def clean_settings(patch, base):
    """A validated copy of `base` with `patch` applied; ValueError(message) on bad input. Unknown keys are ignored."""
    s = dict(base)
    for k, v in (patch or {}).items():
        if k not in DEFAULTS: continue
        if k == "mode":
            if v not in MODES: raise ValueError("mode must be off, preview or live")
            s[k] = v
        elif k in BOOLS:
            if not isinstance(v, bool): raise ValueError(f"{k} must be true or false")
            s[k] = v
        elif k == "intervals":
            if not isinstance(v, (list, tuple)) or not v or any(str(i) not in T.INTERVALS for i in v):
                raise ValueError("intervals must be a list with 15 and / or 60")
            s[k] = sorted({str(i) for i in v})
        else:
            lo, hi = LIMITS[k]
            try:
                x = float(v)
            except (TypeError, ValueError):
                raise ValueError(f"{k} must be a number")
            if not lo <= x <= hi: raise ValueError(f"{k} must be between {lo:g} and {hi:g}")
            s[k] = x if k in FLOATS else int(x)
    return s


# ── store ────────────────────────────────────────────────────────────────────────────────────────────────────────────
def _store_file(key=STORE_KEY):
    return os.path.join(ROOT, "scratch", key + ".json")


def _file_backend():
    return os.environ.get("SBGZ_AUTO_BACKEND", "").strip().lower() == "file"


def _read_key(key):
    """(value or None, ok). ok is False when the store could not be read: then nothing is written and nothing is traded."""
    if _file_backend():
        try:
            with open(_store_file(key), "r", encoding="utf-8") as f:
                return json.load(f), True
        except FileNotFoundError:
            return None, True
        except Exception:
            return None, False
    from .supabase_client import supabase_get
    got = supabase_get("app_state", {"key": f"eq.{key}", "select": "value"})
    if got is None: return None, False
    return (got[0].get("value") if got else None), True


def _write_key(key, value):
    if _file_backend():
        os.makedirs(os.path.dirname(_store_file(key)), exist_ok=True)
        with open(_store_file(key) + ".tmp", "w", encoding="utf-8") as f:
            json.dump(value, f)
        os.replace(_store_file(key) + ".tmp", _store_file(key))
        return True
    from .supabase_client import supabase_kv_set
    return supabase_kv_set(key, value) is not None


def _read_store():
    return _read_key(STORE_KEY)


def _write_store(value):
    return _write_key(STORE_KEY, value)


def _fresh():
    return dict(settings=dict(DEFAULTS), tracked={}, keys={}, pkeys={}, day=dict(d="", orders=0, r=0.0, closed=0), log=[])


def _normalise(v):
    st = _fresh()
    if isinstance(v, dict):
        if isinstance(v.get("tracked"), dict): st["tracked"] = v["tracked"]
        if isinstance(v.get("keys"), dict): st["keys"] = v["keys"]
        if isinstance(v.get("pkeys"), dict): st["pkeys"] = v["pkeys"]
        if isinstance(v.get("log"), list): st["log"] = v["log"][:LOG_MAX]
        if isinstance(v.get("day"), dict): st["day"].update(v["day"])
        try:
            st["settings"] = clean_settings(v.get("settings") or {}, DEFAULTS)
        except ValueError:
            pass
    return st


def _ensure():
    """The state, loaded once; None while the store cannot be read (the caller then does nothing)."""
    global _state
    with _lock:
        if _state is None:
            v, ok = _read_store()
            if not ok: return None
            _state = _normalise(v)
        return _state


_saved_blob = None


def _save():
    """Write the state to the store when it changed since the last write. False when the write failed (it is retried at the next pass)."""
    global _saved_blob
    with _lock:
        if _state is None: return False
        blob = json.dumps(_state, sort_keys=True)
        if blob == _saved_blob: return True
        try:
            ok = bool(_write_store(_state))
        except Exception as e:
            print(f"[sbgz auto] could not save the state: {e}")
            return False
        if ok: _saved_blob = blob
        return ok


def reset_for_tests():
    global _state, _saved_blob
    with _lock:
        _state = None; _saved_blob = None
        _status.update(ok=None, msg="not started yet", t=None, counts={})
        _last_log.clear(); _skipped.clear(); _retry_at.clear(); _marks.clear(); _prices.clear()


# ── helpers ──────────────────────────────────────────────────────────────────────────────────────────────────────────
def equity_cap():
    """The 'account size' setting in USDT (0 = the account's real equity); the default (20) when the state cannot be read."""
    st = _ensure()
    return _f(st["settings"].get("equity_usd"), DEFAULTS["equity_usd"]) if st else DEFAULTS["equity_usd"]


def _now(client):
    """Bybit's clock (this PC's can be off by hours): local time plus the client's measured offset."""
    return int(time.time() * 1000) + int(getattr(client, "time_offset", 0) or 0)


def _log(st, now, kind, text, t=None, symbol=None, interval=None, side=None, mode=None, once=False):
    """Newest first, at most LOG_MAX. once=True: the same message is logged at most once per half hour."""
    t = t or {}
    symbol, interval, side = symbol or t.get("symbol"), interval or t.get("interval"), side or t.get("side")
    if once:
        k = (kind, symbol, text)
        if now - _last_log.get(k, 0) < REPEAT_LOG_MS: return
        _last_log[k] = now
    with _lock:
        st["log"].insert(0, dict(t=now, kind=kind, symbol=symbol, interval=interval, side=side, text=text,
                                 mode=mode or st["settings"]["mode"]))
        del st["log"][LOG_MAX:]


def _f(x, default=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _fmt(x):
    return f"{x:.8g}"


def _setup_key(sym, iv, side, since):
    return f"{sym}|{iv}|{side}|{since}"


_prices = {}                           # (coin, interval) -> the last price of the radar scan


def _collect(results):
    """From a radar scan: the strong waiting setup per (coin, interval, side), each chart's closed candles, BTC's closes by candle
    start, and why recently ended setups ended."""
    setups, series, btc, ended = {}, {}, {}, {}
    for (sym, iv), r in results:
        if not r: continue
        times, closes = r["times"], r.get("closes") or []
        series[(sym, iv)] = (times, closes); _prices[(sym, iv)] = r.get("price")
        if sym == "BTCUSDT": btc[iv] = dict(zip(times, closes))
        for z in r["setups"]:
            sb = z.get("since_bar")
            if not z.get("strong") or sb is None or not 0 <= sb < len(times): continue
            setups[(sym, iv, z["side"])] = dict(symbol=sym, interval=iv, side=z["side"], entry=z["entry"], stop=z["stop"], target=z["target"],
                                               vol_ok=bool(z.get("vol_ok")), bvol=z.get("bvol"), break_vu=z.get("break_vu"), since=times[sb])
        for m in r.get("missed") or []:
            if 0 <= m.get("arm_bar", -1) < len(times): ended[(sym, iv, m["side"], times[m["arm_bar"]])] = m.get("why")
    return setups, series, btc, ended


def _mark(st, ex, live):
    """Where each order / trade of this mode stands now. Open trades: current R (price move / risk, before exit fees) and the position's
    unrealised PnL; waiting orders: how far the price still is from the entry (% on the far side of it)."""
    for link, t in st["tracked"].items():
        if t["status"] not in ("resting", "open", "closing") or bool(t.get("virtual")) == live: continue
        sd = 1 if t["side"] == "LONG" else -1; pos = ex["positions"].get(t["symbol"]) if t["status"] != "resting" else None
        price = (_f(pos.get("markPrice")) if pos else 0) or _prices.get((t["symbol"], t["interval"]))
        if not price: continue
        m = dict(now=price)
        if t["status"] == "resting": m["dist_pct"] = sd * (price - t["entry"]) / price * 100
        else:
            m["r_now"] = sbgz._r_now(t["side"], (_f(pos.get("avgPrice")) if pos else 0) or t["entry"], t["stop"], price)
            if pos: m["pnl_usd"] = _f(pos.get("unrealisedPnl"))
        _marks[link] = m
    for k in [k for k in _marks if k not in st["tracked"] or st["tracked"][k]["status"] not in ("resting", "open", "closing")]: del _marks[k]


def _read_exchange(client):
    """(dict(equity, positions {symbol: row}, orders [rows]), error text). Read-only calls; any failure means no pass."""
    eq, err = T.equity_of(client)
    if err: return None, err
    pos = client.get_positions()
    if pos.get("retCode") != 0: return None, f"Bybit positions: {pos.get('retMsg')} (code {pos.get('retCode')})"
    od = client.signed_request("GET", "/v5/order/realtime", {"category": "linear", "settleCoin": "USDT", "limit": "50"})
    if od.get("retCode") != 0: return None, f"Bybit orders: {od.get('retMsg')} (code {od.get('retCode')})"
    positions = {}
    for p in (pos.get("result") or {}).get("list") or []:
        if _f(p.get("size")) > 0 and p.get("symbol"): positions[p["symbol"]] = p
    return dict(equity=eq, positions=positions, orders=(od.get("result") or {}).get("list") or []), None


def _used(st, virtual):
    """(waiting orders, open trades, risk in USDT) the auto-orders hold, real (virtual=False) or previewed (virtual=True)."""
    rows = [t for t in st["tracked"].values() if bool(t.get("virtual")) == virtual]
    waiting = [t for t in rows if t["status"] == "resting"]
    opened = [t for t in rows if t["status"] in ("open", "closing")]
    return len(waiting), len(opened), sum(_f(t.get("risk_usd")) for t in waiting + opened)


def _day(st, now):
    d = time.strftime("%Y-%m-%d", time.gmtime(now / 1000))
    if st["day"].get("d") != d: st["day"] = dict(d=d, orders=0, r=0.0, closed=0)
    return st["day"]


def _btc_against_r(t, btc_map, series_btc_last):
    """BTC's move against the trade since the setup armed, in R (negative = against). None when BTC candles are missing."""
    b0, b1 = btc_map.get(t.get("since")), btc_map.get(series_btc_last)
    if not b0 or not b1 or not t.get("entry") or t["entry"] == t.get("stop"): return None
    sd = 1 if t["side"] == "LONG" else -1
    return sd * (b1 / b0 - 1) / (abs(t["entry"] - t["stop"]) / abs(t["entry"]))


# ── one pass ─────────────────────────────────────────────────────────────────────────────────────────────────────────
def _hold_lease(now):
    """True when this server may act. Only one server trades at a time (a redeploy briefly runs two; a local copy of the server may run too):
    the one that wrote a heartbeat in the last LEASE_MS is the one. Taking the lease over from another server reloads the state first, so
    the new holder works with what the old one saved. False when the lease cannot be read or written (fail closed)."""
    global _state, _saved_blob
    v, ok = _read_key(LEASE_KEY)
    if not ok: return False
    other = isinstance(v, dict) and v.get("id") != _INSTANCE
    if other and now - int(_f(v.get("t"))) < LEASE_MS: return False
    if other or v is None:
        with _lock:
            fresh, ok2 = _read_store()
            if not ok2: return False
            _state = _normalise(fresh); _saved_blob = None
    try:
        return bool(_write_key(LEASE_KEY, dict(id=_INSTANCE, t=now)))
    except Exception as e:
        print(f"[sbgz auto] could not write the lease: {e}")
        return False


def _done(ok, msg, counts=None, now=None):
    _status.update(ok=ok, msg=msg, t=now or int(time.time() * 1000), counts=counts or {})
    return dict(_status)


def _cancel_order(client, st, now, t, link, why, kind="cancel", block=False):
    """Cancel one waiting order (live) or drop the virtual one (preview). Returns True when it is gone.
    block=True: the setup is not placed again (BTC rule, price already through the new entry). Otherwise it can be, should the same setup
    come back (a one-candle flicker of 'strong')."""
    key = _setup_key(t["symbol"], t["interval"], t["side"], t.get("since")); keys = st["pkeys"] if t.get("virtual") else st["keys"]
    if t.get("virtual"):
        t["status"] = "cancelled"; t["done"] = now
        _log(st, now, kind, f"WOULD cancel: {why}", t, mode="preview")
    else:
        c = T.cancel(client, t["symbol"], link)
        if not c["ok"]:
            _log(st, now, "error", f"could not cancel the waiting order ({c['error']})", t, once=True)
            return False
        t["status"] = "cancelled"; t["done"] = now
        _log(st, now, kind, f"cancelled the waiting order: {why}", t)
    if block: keys[key] = now
    else: keys.pop(key, None)
    return True


def _manage_waiting(client, st, cfg, now, setups, series, btc, ended, counts, live):
    """Each waiting order against its setup: ended -> cancel, moved -> re-place (or cancel when the price is already through)."""
    for link, t in list(st["tracked"].items()):
        if t["status"] != "resting" or bool(t.get("virtual")) == live: continue
        sym, iv, side = t["symbol"], t["interval"], t["side"]
        if (sym, iv) not in series: continue                      # no candles for this chart now: leave the order alone
        cur = setups.get((sym, iv, side))
        if cur is None or cur["since"] != t.get("since"):
            why = sbgz.MISSED_WHY.get(ended.get((sym, iv, side, t.get("since"))), "the setup ended (a newer one replaced it, or the break is no longer 13 VU)")
            if _cancel_order(client, st, now, t, link, why): counts["cancelled"] += 1
            continue
        spec = T.instrument(sym)
        if spec and spec.get("tick"):
            lv = T.round_levels(cur, spec); old = (t["entry"], t["stop"], t["target"])
            if any(abs(a - b) >= spec["tick"] / 2 for a, b in zip(lv, old)):
                counts["moved"] += 1; _move(client, st, cfg, now, link, t, cur, lv, live); continue
        if cfg["btc_cancel"] and sym != "BTCUSDT" and iv in btc and ("BTCUSDT", iv) in series:
            mv = _btc_against_r(t, btc[iv], series[("BTCUSDT", iv)][0][-1])
            if mv is not None and mv <= -sbgz.BX_R:
                if _cancel_order(client, st, now, t, link, f"BTC has moved {abs(mv):.1f}R against the trade since the signal", block=True): counts["cancelled"] += 1


def _move(client, st, cfg, now, link, t, cur, lv, live):
    """The impulse extended: the waiting order follows the setup to its new entry / stop / target."""
    txt = f"{_fmt(t['entry'])} -> {_fmt(lv[0])} (stop {_fmt(lv[1])}, target {_fmt(lv[2])})"
    if not live:
        t.update(entry=lv[0], stop=lv[1], target=lv[2]); _log(st, now, "move", f"WOULD move the order {txt}", t, mode="preview"); return
    with T._order_lock:
        p = T.plan(client, t["symbol"], t["interval"], t["side"], cfg["risk_pct"], cfg["leverage"], replace=True)
        if p.get("ok") and set(p.get("replace") or []) <= {link}:
            res = T.place(client, p, tag=T.AUTO_TAG)
            if res.get("placed"):
                t["status"] = "replaced"; t["done"] = now
                new = _track(st, now, res, cur, False); new["placed"] = t["placed"]
                _log(st, now, "move", f"moved the order {txt}", new); return
            _log(st, now, "error", f"could not re-place the order ({res.get('error')})", t, once=True); return
        why = (p.get("error") or "the plan changed") if not p.get("ok") else "a foreign order rests on the coin"
    # the new entry is already behind the price (the backtest would have filled it inside that candle; a resting order cannot):
    # the old levels are no longer the setup, so the order goes and the setup is not placed again
    _cancel_order(client, st, now, t, link, f"the setup moved to {txt} but it cannot be re-placed ({why})", kind="move", block=True)


def _track(st, now, p, setup, virtual):
    """Register a placed (or previewed) order. p: an ok plan() (+ placed result)."""
    link = p.get("order_link_id") or f"preview-{p['symbol']}-{p['interval']}-{p['side'][0]}-{now}"
    t = dict(link=link, symbol=p["symbol"], interval=p["interval"], side=p["side"], bybit_side=p["bybit_side"], since=p.get("since") or setup.get("since"),
             entry=p["entry"], stop=p["stop"], target=p["target"], qty=p["qty"], risk_usd=p["risk_usd"], vol_ok=p["vol_ok"], status="resting",
             virtual=virtual, placed=now)
    st["tracked"][link] = t
    return t


def _place_new(client, st, cfg, now, ex, setups, counts, live):
    """New orders for the setups that pass the filters, best first, inside the caps."""
    day = _day(st, now); mode = "live" if live else "preview"
    if day["r"] <= -cfg["daily_stop_r"]:
        _log(st, now, "halt", f"daily loss stop reached ({day['r']:+.1f}R today, limit -{cfg['daily_stop_r']:g}R): no new orders until tomorrow (UTC)", once=True, mode=mode)
        return
    n_wait, n_open, risk = _used(st, not live)
    equity = ex["equity"]; keys = st["keys"] if live else st["pkeys"]
    busy = set(ex["positions"]) | {o.get("symbol") for o in ex["orders"]}       # a coin with a position or any order is left alone
    held = {t["symbol"] for t in st["tracked"].values() if t["status"] in ("resting", "open", "closing") and bool(t.get("virtual")) == (not live)}
    cands = sorted((s for s in setups.values() if s["interval"] in cfg["intervals"] and (s["vol_ok"] or not cfg["vol_only"])),
                   key=lambda s: (not s["vol_ok"], s["interval"] != "60", -(s["since"] or 0)))
    new = 0
    for s in cands:
        sym, iv, side = s["symbol"], s["interval"], s["side"]; key = _setup_key(sym, iv, side, s["since"])
        if key in keys or _retry_at.get(key, 0) > now or sym in busy or sym in held: continue
        if new >= cfg["max_new_per_cycle"] or n_wait >= cfg["max_resting"] or n_open >= cfg["max_open"]: break
        if day["orders"] >= cfg["max_orders_per_day"]:
            _log(st, now, "halt", f"{cfg['max_orders_per_day']} orders placed today: no more until tomorrow (UTC)", once=True, mode=mode); break
        with T._order_lock:
            if live and st["settings"]["mode"] != "live": return                       # switched off while this pass was running
            p = T.plan(client, sym, iv, side, cfg["risk_pct"], cfg["leverage"])
            if not p.get("ok"):
                _retry_at[key] = now + SKIP_RETRY_MS
                if _skipped.get(key) != p.get("error"):
                    _skipped[key] = p.get("error"); _log(st, now, "skip", f"not placed: {p.get('error')}", s, mode=mode)
                continue
            if cfg["vol_only"] and not p.get("vol_ok"): continue
            if (risk + p["risk_usd"]) > equity * cfg["max_open_risk_pct"] / 100 + 1e-9:
                _log(st, now, "skip", f"not placed: total risk would pass {cfg['max_open_risk_pct']:g}% of the account size ({equity:g} USDT)", s, once=True, mode=mode); continue
            if not live:
                t = _track(st, now, p, s, True); keys[key] = now
                _log(st, now, "place", f"WOULD place a post-only limit {p['side']} {p['qty_str']} @ {p['price_str']['entry']}, stop {p['price_str']['stop']}, "
                     f"target {p['price_str']['target']} (risk {p['risk_usd']:.2f} USDT = {p['risk_pct']:g}%{', volume-confirmed' if p['vol_ok'] else ''})", t, mode="preview")
            else:
                res = T.place(client, p, tag=T.AUTO_TAG)
                if not res.get("placed"):
                    _retry_at[key] = now + RETRY_AFTER_MS
                    _log(st, now, "error", f"Bybit refused the order: {res.get('error')}", s, once=True); continue
                t = _track(st, now, res, s, False); keys[key] = now; day["orders"] += 1
                _log(st, now, "place", f"placed a post-only limit {p['side']} {p['qty_str']} @ {p['price_str']['entry']}, stop {p['price_str']['stop']}, "
                     f"target {p['price_str']['target']} (risk {p['risk_usd']:.2f} USDT = {p['risk_pct']:g}%{', volume-confirmed' if p['vol_ok'] else ''})", t)
        new += 1; counts["placed"] += 1; n_wait += 1; risk += p["risk_usd"]; held.add(sym)


# ── live bookkeeping: fills, open trades, closed trades ──────────────────────────────────────────────────────────────
def _adopt(st, now, ex, setups):
    """An auto-tagged order on Bybit that the state does not know (the answer to a create call got lost): take it over, as the order of the
    waiting setup on that coin (if there is none, the next step cancels it like any order whose setup ended)."""
    for o in ex["orders"]:
        link = str(o.get("orderLinkId") or ""); parts = link.split("-")
        if len(parts) != 5 or parts[0] + "-" != T.LINK_PREFIX or parts[4][:1] != T.AUTO_TAG or link in st["tracked"] or parts[2] not in T.INTERVALS: continue
        entry, stop, qty = _f(o.get("price")), _f(o.get("stopLoss")), _f(o.get("qty")); side = "LONG" if parts[3] == "L" else "SHORT"
        cur = setups.get((o.get("symbol") or parts[1], parts[2], side))
        st["tracked"][link] = dict(link=link, symbol=o.get("symbol") or parts[1], interval=parts[2], side=side,
                                   bybit_side=o.get("side"), since=cur["since"] if cur else None, entry=entry, stop=stop, target=_f(o.get("takeProfit")), qty=qty,
                                   risk_usd=qty * abs(entry - stop), vol_ok=None, status="resting", virtual=False, placed=int(_f(o.get("createdTime"), now)), adopted=True)
        _log(st, now, "info", "found a waiting auto order that was not in the list (a reply was lost); managing it again", st["tracked"][link])


def _outcome(client, t):
    """(R, text) of a finished trade from Bybit's closed-PnL list; (None, text) when it is not there yet."""
    r = client.signed_request("GET", "/v5/position/closed-pnl", {"category": "linear", "symbol": t["symbol"], "limit": "10"})
    rows = [x for x in ((r.get("result") or {}).get("list") or []) if int(_f(x.get("updatedTime"))) >= t["placed"] - 60_000] if r.get("retCode") == 0 else []
    if not rows: return None, "closed (the result is not on Bybit's list yet)"
    x = max(rows, key=lambda y: int(_f(y.get("updatedTime"))))
    px, pnl = _f(x.get("avgExitPrice")), _f(x.get("closedPnl")); sd = 1 if t["side"] == "LONG" else -1; tol = 0.05 * abs(t["entry"] - t["stop"])
    how = "target hit" if sd * (px - t["target"]) >= -tol else "stop hit" if sd * (px - t["stop"]) <= tol else "closed by the BTC rule" if t.get("close_why") == "btc" else "closed early"
    R = pnl / t["risk_usd"] if t.get("risk_usd") else None
    return R, f"{how} at {_fmt(px)}" + (f": {R:+.2f}R ({pnl:+.2f} USDT after fees)" if R is not None else "")


def _cleanup_orphans(client, st, now, sym, ex):
    """After a trade is over, remove what is left of its take-profit / stop orders so the coin is free again."""
    for o in ex["orders"]:
        if o.get("symbol") != sym or str(o.get("orderLinkId") or "").startswith(T.LINK_PREFIX): continue
        if o.get("reduceOnly") or o.get("closeOnTrigger") or o.get("stopOrderType") in CLEANUP_STOP_TYPES:
            r = client.signed_request("POST", "/v5/order/cancel", body=dict(category="linear", symbol=sym, orderId=o.get("orderId")))
            _log(st, now, "info", f"removed a leftover {o.get('stopOrderType') or 'reduce-only'} order" + ("" if r.get("retCode") == 0 else f" (Bybit: {r.get('retMsg')})"), symbol=sym)


def _sync(client, st, cfg, now, ex, setups, counts):
    """Match the tracked real orders / trades with Bybit: fills, finished trades (with their R), orders cancelled from outside."""
    _adopt(st, now, ex, setups)
    day = _day(st, now); resting = {str(o.get("orderLinkId")) for o in ex["orders"] if o.get("orderLinkId")}
    for link, t in list(st["tracked"].items()):
        if t.get("virtual") or t["status"] not in ("resting", "open", "closing"): continue
        try:
            pos = ex["positions"].get(t["symbol"]); same_side = bool(pos) and pos.get("side") == t.get("bybit_side")
            if t["status"] == "resting":
                if same_side:                                              # filled (maybe only partly: the rest may still rest)
                    t.update(status="open", fill_ms=int(_f(pos.get("createdTime"), now)), fill_price=_f(pos.get("avgPrice")) or t["entry"])
                    counts["filled"] += 1
                    _log(st, now, "fill", f"filled at {_fmt(t['fill_price'])}: the trade is open (stop {_fmt(t['stop'])}, target {_fmt(t['target'])})", t)
                elif link not in resting:                                  # gone without a position: filled and already over, or cancelled from outside
                    h = client.signed_request("GET", "/v5/order/history", {"category": "linear", "symbol": t["symbol"], "orderLinkId": link, "limit": "1"})
                    rows = ((h.get("result") or {}).get("list") or []) if h.get("retCode") == 0 else []
                    sts = rows[0].get("orderStatus") if rows else None
                    if sts == "Filled":
                        t.update(status="closing", fill_ms=t["placed"], close_why="exchange")
                    elif sts in LIVE_STATUSES:
                        pass                                                 # still active on Bybit's side (the list lagged): look again next pass
                    else:
                        t["checks"] = t.get("checks", 0) + 1
                        if sts is not None or t["checks"] >= 5:            # known to be cancelled / rejected, or not found five passes in a row
                            t.update(status="gone", done=now)
                            st["keys"].setdefault(_setup_key(t["symbol"], t["interval"], t["side"], t.get("since")), now)
                            _log(st, now, "info", f"the waiting order is no longer on Bybit ({sts or 'not found'}); it is not placed again", t)
            if t["status"] in ("open", "closing") and not same_side:       # the position is over: stop, target, or our market close
                R, txt = _outcome(client, t)
                if R is None and t.get("tries", 0) < 4:
                    t["tries"] = t.get("tries", 0) + 1; continue           # Bybit's closed-PnL list can lag a few seconds
                t.update(status="done", done=now, R=R); day["closed"] += 1
                if R is not None: day["r"] += R
                counts["closed"] += 1
                _log(st, now, "closed", txt, t); _cleanup_orphans(client, st, now, t["symbol"], ex)
        except Exception as e:
            _log(st, now, "error", f"bookkeeping failed for {t.get('symbol')}: {e}", t, once=True)


def _manage_open(client, st, cfg, now, ex, series, btc, counts):
    """Open auto trades: close at the market once BTC has moved 0.5R against them (the radar history's BTC rule)."""
    if not cfg["btc_close"]: return
    for link, t in list(st["tracked"].items()):
        if t.get("virtual") or t["status"] != "open" or t["symbol"] == "BTCUSDT": continue
        pos = ex["positions"].get(t["symbol"]); iv = t["interval"]
        if not pos or (t["symbol"], iv) not in series or iv not in btc: continue
        times, closes = series[(t["symbol"], iv)]; step = sbgz.TF_MS[iv] // 1000
        fill_s = int(t.get("fill_ms") or now) // 1000 // step * step
        row = dict(symbol=t["symbol"], side=t["side"], entry=_f(pos.get("avgPrice")) or t["entry"], stop=t["stop"], t_fill=fill_s, t_end=None)
        bx = sbgz.btc_exit(row, times, closes, btc[iv], step)
        if not bx or not bx.get("fired"): continue
        body = dict(category="linear", symbol=t["symbol"], side="Sell" if t["side"] == "LONG" else "Buy", orderType="Market", qty=pos.get("size"),
                    reduceOnly=True, timeInForce="IOC")
        res = client.signed_request("POST", "/v5/order/create", body=body)
        if res.get("retCode") == 0:
            t.update(status="closing", close_why="btc", closed_seen=now); counts["closed"] += 1
            _log(st, now, "close", f"closed at the market: BTC moved {sbgz.BX_R:g}R against the trade (candle closed {time.strftime('%H:%M', time.gmtime(bx['t']))} UTC)", t)
        else:
            _log(st, now, "error", f"could not close at the market: {res.get('retMsg')} (code {res.get('retCode')})", t, once=True)


def run_cycle(client, scan=None, now=None):
    """One pass of the auto-orders. Returns the status dict; never raises."""
    try:
        return _run_cycle(client, scan, now)
    except Exception as e:
        print(f"[sbgz auto] pass failed: {e}")
        return _done(False, f"the pass failed: {e}")


def _run_cycle(client, scan, now):
    st = _ensure()
    if st is None: return _done(False, "the auto-order store could not be read: nothing was done")
    if not _cycle_lock.acquire(blocking=False): return dict(_status)
    try:
        cfg = dict(st["settings"]); mode = cfg["mode"]
        if mode == "off": return _done(True, "off")
        now = now or _now(client); live = mode == "live"
        if not _hold_lease(now): return _done(True, "another server is running the auto-orders (or the lease could not be read): this one waits", now=now)
        st = _state; cfg = dict(st["settings"]); mode = cfg["mode"]; live = mode == "live"         # (a lease takeover reloaded the state)
        if mode == "off": return _done(True, "off", now=now)
        if T.account(client) == "REAL":
            st["settings"]["mode"] = "off"
            _log(st, now, "halt", "auto-orders only run on a demo / testnet account; this server is on a REAL-money account, so they were switched off", mode=mode)
            _save(); return _done(False, "real-money account: switched off", now=now)
        gen, results = scan if scan is not None else sbgz._scan(("15", "60"), list(sbgz.RADAR_COINS), 40)
        charts = sum(1 for _, r in results if r)
        if charts < 0.5 * max(1, len(results)): return _done(False, f"only {charts} of {len(results)} charts downloaded: no action this pass", now=now)
        setups, series, btc, ended = _collect(results)
        ex, err = _read_exchange(client)
        if err:
            _log(st, now, "error", err, once=True, mode=mode); _save(); return _done(False, err, now=now)
        ex["equity_real"] = ex["equity"]; ex["equity"] = min(ex["equity"], cfg["equity_usd"]) if cfg["equity_usd"] > 0 else ex["equity"]   # the account size caps are counted on
        _status.update(equity_real=ex["equity_real"], equity_used=ex["equity"])
        counts = dict(placed=0, cancelled=0, moved=0, filled=0, closed=0)
        if live: _sync(client, st, cfg, now, ex, setups, counts)
        _manage_waiting(client, st, cfg, now, setups, series, btc, ended, counts, live)
        if live: _manage_open(client, st, cfg, now, ex, series, btc, counts)
        _place_new(client, st, cfg, now, ex, setups, counts, live)
        _mark(st, ex, live)
        for k in [k for k, t in st["tracked"].items() if t["status"] in ("cancelled", "replaced", "gone", "done") and now - t.get("done", now) > KEEP_DONE_MS]:
            del st["tracked"][k]
        for d in ("keys", "pkeys"):
            for k in [k for k, v in st[d].items() if now - v > 14 * 86_400_000]: del st[d][k]
        nw, no, risk = _used(st, not live)
        _save()
        return _done(True, f"{mode}: {len(setups)} strong setups on {charts} charts; {nw} waiting orders, {no} open trades; "
                     f"this pass placed {counts['placed']}, moved {counts['moved']}, cancelled {counts['cancelled']}, closed {counts['closed']}", counts, now)
    finally:
        _cycle_lock.release()


# ── API ──────────────────────────────────────────────────────────────────────────────────────────────────────────────
def status(client=None):
    """GET /api/sbgz/auto."""
    st = _ensure()
    if st is None: return dict(ok=False, error="the auto-order store could not be read; try again in a minute")
    with _lock:
        tr = sorted(st["tracked"].values(), key=lambda t: -t.get("placed", 0))
        rows = [dict({k: t.get(k) for k in ("link", "symbol", "interval", "side", "status", "entry", "stop", "target", "qty", "risk_usd", "virtual", "placed", "R", "vol_ok")},
                     **_marks.get(t.get("link"), {})) for t in tr]
        open_r = [r["r_now"] for r in rows if r.get("status") in ("open", "closing") and not r.get("virtual") and r.get("r_now") is not None]
        day = dict(st["day"]); cfg = dict(st["settings"])
        return dict(ok=True, account=T.account(client) if client else None, settings=cfg, day=day,
                    equity=dict(used=_status.get("equity_used"), real=_status.get("equity_real")), open_r=dict(n=len(open_r), r=sum(open_r)),
                    halted=day.get("r", 0) <= -cfg["daily_stop_r"], status=dict(_status), tracked=rows, log=list(st["log"][:60]),
                    running=bool(_thread and _thread.is_alive()))


def _leave_live(client, st, now, close_positions=False):
    """Cancel the waiting real orders (and with close_positions the open trades) the auto-orders hold. Returns a short summary."""
    n_c = n_x = 0; errs = []
    for link, t in list(st["tracked"].items()):
        if t.get("virtual"):
            t["status"] = "cancelled"; t["done"] = now; continue
        if t["status"] == "resting" and _cancel_order(client, st, now, t, link, "auto-orders switched off"): n_c += 1
        elif t["status"] == "resting": errs.append(t["symbol"])
        if close_positions and t["status"] == "open":
            r = client.signed_request("GET", "/v5/position/list", {"category": "linear", "symbol": t["symbol"]})
            for p in (r.get("result") or {}).get("list") or [] if r.get("retCode") == 0 else []:
                if _f(p.get("size")) > 0:
                    res = client.signed_request("POST", "/v5/order/create", body=dict(category="linear", symbol=t["symbol"], side="Sell" if p.get("side") == "Buy" else "Buy",
                                                                                      orderType="Market", qty=p.get("size"), reduceOnly=True, timeInForce="IOC"))
                    if res.get("retCode") == 0: t.update(status="closing", close_why="manual", closed_seen=now); n_x += 1; _log(st, now, "close", "closed at the market (auto-orders switched off)", t)
                    else: errs.append(t["symbol"])
    return dict(cancelled=n_c, closed=n_x, failed=errs)


def update_settings(patch, client, confirm=False):
    """POST /api/sbgz/auto/settings."""
    st = _ensure()
    if st is None: return dict(ok=False, error="the auto-order store could not be read; try again in a minute")
    try:
        new = clean_settings(patch, st["settings"])
    except ValueError as e:
        return dict(ok=False, error=str(e))
    old_mode, mode = st["settings"]["mode"], new["mode"]
    if mode != "off" and T.account(client) == "REAL":
        return dict(ok=False, error="auto-orders only run on a demo / testnet account; this server is on a REAL-money account")
    if mode == "live" and old_mode != "live" and confirm is not True:
        return dict(ok=False, need_confirm=True, error="going live places orders on the demo account by itself: confirm it")
    if mode == old_mode:                                          # filters, limits, account size: nothing to wind down, no need to wait for a pass
        with _lock: st["settings"] = new
        _save()
        return dict(ok=True, settings=new, note=None)
    if not _cycle_lock.acquire(timeout=30): return dict(ok=False, error="a pass is running; try again in a few seconds")
    try:
        now = _now(client); note = None
        with _lock: st["settings"] = new
        if old_mode == "live" and mode != "live":
            note = _leave_live(client, st, now)
            _log(st, now, "info", f"left live mode: cancelled {note['cancelled']} waiting order(s); open trades keep their stop and target on Bybit", mode=mode)
        elif mode != old_mode:
            for t in [t for t in st["tracked"].values() if t.get("virtual")]: t["status"] = "cancelled"; t["done"] = now
            st["pkeys"] = {}
            _log(st, now, "info", f"mode: {old_mode} -> {mode}", mode=mode)
        if mode == "live" and old_mode != "live": _retry_at.clear(); _skipped.clear()
        _save()
        return dict(ok=True, settings=new, note=note)
    finally:
        _cycle_lock.release()


def kill(client, close_positions=False):
    """POST /api/sbgz/auto/kill: switch off, cancel the waiting auto orders, optionally close the open auto trades at the market."""
    st = _ensure()
    if st is None: return dict(ok=False, error="the auto-order store could not be read; try again in a minute")
    with _lock: st["settings"]["mode"] = "off"                 # no new order from this moment, even if a pass is running
    if not _cycle_lock.acquire(timeout=30): return dict(ok=False, error="switched off, but a pass is still running; press again in a few seconds to cancel the waiting orders")
    try:
        now = _now(client); res = _leave_live(client, st, now, close_positions)
        _log(st, now, "info", f"switched off: cancelled {res['cancelled']} waiting order(s)" + (f", closed {res['closed']} open trade(s)" if close_positions else ""), mode="off")
        _save()
        return dict(ok=not res["failed"], **res, error=("could not cancel / close: " + ", ".join(res["failed"])) if res["failed"] else None)
    finally:
        _cycle_lock.release()


def start_thread(get_client, period=60.0, delay=75.0):
    """The background loop (daemon thread). delay: wait after start so a deploy's old and new server do not both act at once."""
    global _thread
    if _thread and _thread.is_alive(): return _thread
    _stop.clear()

    def loop():
        _stop.wait(delay)
        while not _stop.is_set():
            t0 = time.time()
            try:
                st = _ensure()
                if st and st["settings"]["mode"] != "off": run_cycle(get_client())
            except Exception as e:
                print(f"[sbgz auto] loop: {e}")
            _stop.wait(max(5.0, period - (time.time() - t0)))

    _thread = threading.Thread(target=loop, name="sbgz-auto", daemon=True)
    _thread.start()
    return _thread
