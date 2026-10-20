"""AMD-FVG auto-orders: the Forward Lab's AMD setups traded for real on the Bybit DEMO account (never a real-money account).

The setup comes from the Forward Lab's own ledger (backend_lib/forward_store.py, table amd_trades), so what is traded is exactly what the
frozen rule in backend_lib/amd_fvg.py found and what the forward test records. Nothing is re-computed here.

  entry   LIMIT, PostOnly, at the gap's 50% level, placed while the ledger says the setup is PENDING (valid for 12 candles)
  stop    stop-market, below the sweep (above it for shorts)
  target  take-profit limit at 2R
  exit    a market close 96 hours after the fill if neither was hit (the rule's 96-candle time exit)
  size    qty that loses risk_pct of the ACCOUNT SIZE (20 USDT unless changed) between entry and stop
  cancel  a waiting order is cancelled when the ledger says the setup ended (cancelled, expired, or the trade already over)

One position or order per coin on the whole account (a coin with a position or any order is left alone, as are SBGZ's and the others'),
at most max_open open trades and max_resting waiting orders. Orders are tagged orderLinkId 'amd-...' so they are told apart from
SBGZ's ('sbgz-'). One server acts at a time (a lease in the shared store, like sbgz_auto). Settings: GET /api/amd/auto, POST
/api/amd/auto/settings and /api/amd/auto/kill (demo accounts only, no password).
Mode: off (default) / preview (logs what it would place, sends nothing) / live (places orders).
"""
import json
import os
import threading
import time
import urllib.parse
import urllib.request

from . import forward_store, sbgz_auto as A, sbgz_trade as T

STORE_KEY = "amd_auto"
LEASE_KEY = STORE_KEY + "_lease"
LEASE_MS = 150_000
LINK_PREFIX = "amd-"
MODES = ("off", "preview", "live")
DEFAULTS = dict(mode="off", equity_usd=20.0, risk_pct=2.0, config="AMD_NO_GATE", max_open=3, max_resting=6, leverage=10, hold_hours=96)
LIMITS = dict(equity_usd=(0.0, 1_000_000.0), risk_pct=(0.05, 5.0), max_open=(1, 10), max_resting=(1, 20), leverage=(1, 25), hold_hours=(1, 200))
FLOATS = ("equity_usd", "risk_pct")
CONFIGS = ("AMD_NO_GATE", "AMD_PRIMARY")
LOG_MAX = 400
KEEP_MS = 14 * 86_400_000                  # finished trades / cancelled orders stay listed for 14 days
RETRY_MS = 600_000                          # an order Bybit refused / a setup that cannot be planned is not tried again for 10 minutes
LEDGER_DAYS = 3                             # setups younger than this are looked at
ENDED = ("CANCELLED", "EXPIRED", "CLOSED")
LIVE_STATUSES = ("New", "PartiallyFilled", "Untriggered", "Created")

_INSTANCE = f"{os.getpid()}-{int(time.time())}-{os.urandom(3).hex()}"
_lock = threading.RLock()
_cycle_lock = threading.Lock()
_state = None
_status = dict(ok=None, msg="not started yet", t=None, counts={})
_stop = threading.Event()
_thread = None
_retry = {}                                 # ledger key -> ms before which it is not tried again
_last_link_s = 0


# ── settings and store ───────────────────────────────────────────────────────────────────────────────────────────────
def clean_settings(patch, base):
    """A validated copy of `base` with `patch` applied; ValueError(message) on bad input. Unknown keys are ignored."""
    s = dict(base)
    for k, v in (patch or {}).items():
        if k not in DEFAULTS: continue
        if k == "mode":
            if v not in MODES: raise ValueError("mode must be off, preview or live")
            s[k] = v
        elif k == "config":
            if v not in CONFIGS: raise ValueError("config must be AMD_NO_GATE or AMD_PRIMARY")
            s[k] = v
        else:
            lo, hi = LIMITS[k]
            try:
                x = float(v)
            except (TypeError, ValueError):
                raise ValueError(f"{k} must be a number")
            if not lo <= x <= hi: raise ValueError(f"{k} must be between {lo:g} and {hi:g}")
            s[k] = x if k in FLOATS else int(x)
    return s


def _fresh():
    return dict(settings=dict(DEFAULTS), tracked={}, keys={}, pkeys={}, day=dict(d="", orders=0, r=0.0, closed=0), log=[])


def _normalise(v):
    st = _fresh()
    if isinstance(v, dict):
        for k in ("tracked", "keys", "pkeys"):
            if isinstance(v.get(k), dict): st[k] = v[k]
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
            v, ok = A._read_key(STORE_KEY)
            if not ok: return None
            _state = _normalise(v)
        return _state


_saved_blob = None


def _save():
    global _saved_blob
    with _lock:
        if _state is None: return False
        blob = json.dumps(_state, sort_keys=True)
        if blob == _saved_blob: return True
        try:
            ok = bool(A._write_key(STORE_KEY, _state))
        except Exception as e:
            print(f"[amd auto] could not save the state: {e}")
            return False
        if ok: _saved_blob = blob
        return ok


def reset_for_tests():
    global _state, _saved_blob, _last_link_s
    with _lock:
        _state = None; _saved_blob = None; _last_link_s = 0
        _status.update(ok=None, msg="not started yet", t=None, counts={})
        _retry.clear()


def _log(st, now, kind, text, t=None, symbol=None, side=None, mode=None, once=False):
    t = t or {}
    symbol, side = symbol or t.get("symbol"), side or t.get("side")
    if once:
        k = (kind, symbol, text)
        if now - _retry.get(("log",) + k, 0) < 1_800_000: return
        _retry[("log",) + k] = now
    with _lock:
        st["log"].insert(0, dict(t=now, kind=kind, symbol=symbol, side=side, text=text, mode=mode or st["settings"]["mode"]))
        del st["log"][LOG_MAX:]


def _day(st, now):
    d = time.strftime("%Y-%m-%d", time.gmtime(now / 1000))
    if st["day"].get("d") != d: st["day"] = dict(d=d, orders=0, r=0.0, closed=0)
    return st["day"]


def _f(x, default=0.0):
    return A._f(x, default)


def _fmt(x):
    return A._fmt(x)


# ── the Forward Lab's ledger and prices ──────────────────────────────────────────────────────────────────────────────
def _ledger(config, now):
    """{key: dict(symbol, side, signal_t, entry, stop, target, status)} of the config's setups from the last LEDGER_DAYS days."""
    con = forward_store.connect()
    try:
        rows = con.execute("SELECT key,symbol,side,signal_t,entry,stop,target,status FROM amd_trades WHERE config=? AND signal_t>=?",
                           (config, now - LEDGER_DAYS * 86_400_000)).fetchall()
    finally:
        con.close()
    return {r[0]: dict(key=r[0], symbol=r[1], side=r[2], signal_t=int(r[3]), entry=float(r[4]), stop=float(r[5]), target=float(r[6]), status=r[7]) for r in rows}


def _last_price(symbol, timeout=6):
    """Bybit's last price (public ticker); None when it cannot be read."""
    url = "https://api.bybit.com/v5/market/tickers?" + urllib.parse.urlencode(dict(category="linear", symbol=symbol))
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "MASIS/3.0"}), timeout=timeout) as r:
            it = ((json.loads(r.read().decode()).get("result") or {}).get("list") or [None])[0]
        return float(it["lastPrice"])
    except Exception:
        return None


# ── planning and placing ─────────────────────────────────────────────────────────────────────────────────────────────
def _plan(row, cfg, equity, price):
    """The order for a ledger row, or dict(ok=False, error=...). equity: the account size the trade is sized on."""
    sym, side = row["symbol"], row["side"]
    spec = T.instrument(sym)
    if not spec or not spec["tick"] or not spec["qty_step"]: return dict(ok=False, error=f"no instrument info for {sym} (not a trading USDT perpetual?)")
    sd = 1 if side == "LONG" else -1
    entry, stop, target = T.round_levels(dict(side=side, entry=row["entry"], stop=row["stop"], target=row["target"]), spec)
    if sd * (target - entry) <= 0 or sd * (entry - stop) <= 0: return dict(ok=False, error="entry, stop and target collapse at this tick size")
    if price is None: return dict(ok=False, error=f"could not read the {sym} price")
    if sd * (price - entry) <= 0:
        return dict(ok=False, error=f"price {price:g} is already through the entry {entry:g}: a post-only limit there would be cancelled (missed the fill)")
    leverage = max(1, min(int(cfg["leverage"]), int(spec["max_lev"])))
    qty = T.size_qty(equity, cfg["risk_pct"] / 100, entry, stop, spec, leverage)
    if qty <= 0:
        return dict(ok=False, error=f"too small to size at {cfg['risk_pct']:g}% risk of {equity:.2f} USDT: Bybit's smallest order is "
                                     f"{spec['min_qty']:g} {sym[:-4]} / {spec['min_notional']:g} USDT")
    return dict(ok=True, symbol=sym, side=side, bybit_side="Buy" if sd == 1 else "Sell", entry=entry, stop=stop, target=target, qty=qty,
                qty_str=T.fmt(qty, spec["qty_step"]), price_str=dict(entry=T.fmt(entry, spec["tick"]), stop=T.fmt(stop, spec["tick"]), target=T.fmt(target, spec["tick"])),
                leverage=leverage, risk_usd=qty * abs(entry - stop), rr=abs(target - entry) / abs(entry - stop))


def _send(client, p, now):
    """Place a planned order (post-only limit + attached stop and take-profit). Returns dict(placed, link, error)."""
    global _last_link_s
    lv = client.set_leverage(p["symbol"], p["leverage"])
    if lv.get("retCode") not in (0, 110043): print(f"[amd order] leverage {p['symbol']} {p['leverage']}x: {lv.get('retCode')} {lv.get('retMsg')} (placing anyway)")
    _last_link_s = max(int(now / 1000), _last_link_s + 1)
    link = f"{LINK_PREFIX}{p['symbol']}-{p['side'][0]}-{_last_link_s:x}"[:36]
    body = dict(category="linear", symbol=p["symbol"], side=p["bybit_side"], orderType="Limit", qty=p["qty_str"], price=p["price_str"]["entry"],
                timeInForce="PostOnly", orderLinkId=link, takeProfit=p["price_str"]["target"], stopLoss=p["price_str"]["stop"], tpslMode="Partial",
                tpOrderType="Limit", tpLimitPrice=p["price_str"]["target"], slOrderType="Market", tpTriggerBy="LastPrice", slTriggerBy="LastPrice")
    res = client.signed_request("POST", "/v5/order/create", body=body)
    ok = res.get("retCode") == 0
    print(f"[amd order] {p['symbol']} {p['side']} {p['qty_str']} @ {p['price_str']['entry']} SL {p['price_str']['stop']} TP {p['price_str']['target']}: "
          f"{'placed' if ok else res.get('retMsg')}")
    return dict(placed=ok, link=link, error=None if ok else f"Bybit refused the order: {res.get('retMsg')} (code {res.get('retCode')})")


def _cancel(client, st, now, t, link, why):
    """Cancel one waiting order by its link. True when it is gone."""
    r = client.signed_request("POST", "/v5/order/cancel", body=dict(category="linear", symbol=t["symbol"], orderLinkId=link))
    if r.get("retCode") not in (0, 110001, 110010):                       # 110001 / 110010: already gone
        _log(st, now, "error", f"could not cancel the waiting order ({r.get('retMsg')})", t, once=True); return False
    t["status"] = "cancelled"; t["done"] = now
    _log(st, now, "cancel", f"cancelled the waiting order: {why}", t)
    return True


def _close_market(client, st, now, t, why):
    r = client.signed_request("GET", "/v5/position/list", {"category": "linear", "symbol": t["symbol"]})
    ok = False
    for p in (r.get("result") or {}).get("list") or [] if r.get("retCode") == 0 else []:
        if _f(p.get("size")) > 0:
            res = client.signed_request("POST", "/v5/order/create", body=dict(category="linear", symbol=t["symbol"], side="Sell" if p.get("side") == "Buy" else "Buy",
                                                                              orderType="Market", qty=p.get("size"), reduceOnly=True, timeInForce="IOC"))
            ok = res.get("retCode") == 0
            if not ok: _log(st, now, "error", f"could not close the trade at the market ({res.get('retMsg')})", t, once=True)
    if ok:
        t.update(status="closing", close_why="time", closed_seen=now)
        _log(st, now, "close", why, t)
    return ok


def _cleanup(client, st, now, sym, ex):
    """After a trade is over, remove what is left of its take-profit / stop orders so the coin is free again."""
    for o in ex["orders"]:
        if o.get("symbol") != sym or str(o.get("orderLinkId") or "").startswith(LINK_PREFIX): continue
        if o.get("reduceOnly") or o.get("closeOnTrigger") or o.get("stopOrderType") in A.CLEANUP_STOP_TYPES:
            r = client.signed_request("POST", "/v5/order/cancel", body=dict(category="linear", symbol=sym, orderId=o.get("orderId")))
            _log(st, now, "info", f"removed a leftover {o.get('stopOrderType') or 'reduce-only'} order" + ("" if r.get("retCode") == 0 else f" (Bybit: {r.get('retMsg')})"), symbol=sym)


# ── one pass ─────────────────────────────────────────────────────────────────────────────────────────────────────────
def _sync(client, st, cfg, now, ex, rows, counts):
    """Match the tracked real orders / trades with Bybit and the ledger: fills, finished trades (with their R), orders to cancel, the time exit."""
    day = _day(st, now); resting = {str(o.get("orderLinkId")) for o in ex["orders"] if o.get("orderLinkId")}
    for link, t in list(st["tracked"].items()):
        if t["status"] not in ("resting", "open", "closing"): continue
        try:
            pos = ex["positions"].get(t["symbol"]); same_side = bool(pos) and pos.get("side") == t["bybit_side"]
            row = rows.get(t["key"])
            if t["status"] == "resting":
                if same_side:
                    t.update(status="open", fill_ms=int(_f(pos.get("createdTime"), now)) or now, fill_price=_f(pos.get("avgPrice")) or t["entry"])
                    counts["filled"] += 1
                    _log(st, now, "fill", f"filled at {_fmt(t['fill_price'])}: the trade is open (stop {_fmt(t['stop'])}, target {_fmt(t['target'])})", t)
                elif link not in resting:
                    h = client.signed_request("GET", "/v5/order/history", {"category": "linear", "symbol": t["symbol"], "orderLinkId": link, "limit": "1"})
                    hr = ((h.get("result") or {}).get("list") or []) if h.get("retCode") == 0 else []
                    sts = hr[0].get("orderStatus") if hr else None
                    if sts == "Filled": t.update(status="closing", fill_ms=t["placed"], close_why="exchange")
                    elif sts in LIVE_STATUSES: pass
                    else:
                        t["checks"] = t.get("checks", 0) + 1
                        if sts is not None or t["checks"] >= 5:
                            t.update(status="gone", done=now)
                            _log(st, now, "info", f"the waiting order is no longer on Bybit ({sts or 'not found'}); it is not placed again", t)
                elif row is not None and row["status"] in ENDED:
                    if _cancel(client, st, now, t, link, f"the setup ended (ledger: {row['status'].lower()})"): counts["cancelled"] += 1
            if t["status"] == "open" and same_side and now - t.get("fill_ms", now) >= cfg["hold_hours"] * 3_600_000 and not t.get("time_tried", 0) >= 3:
                t["time_tried"] = t.get("time_tried", 0) + 1
                _close_market(client, st, now, t, f"closed at the market: {cfg['hold_hours']} hours after the fill (the rule's time exit)")
            if t["status"] in ("open", "closing") and not same_side:
                R, txt = A._outcome(client, t)
                if R is None and t.get("tries", 0) < 4:
                    t["tries"] = t.get("tries", 0) + 1; continue
                t.update(status="done", done=now, R=R); day["closed"] += 1
                if R is not None: day["r"] += R
                counts["closed"] += 1
                _log(st, now, "closed", txt, t); _cleanup(client, st, now, t["symbol"], ex)
        except Exception as e:
            _log(st, now, "error", f"bookkeeping failed for {t.get('symbol')}: {e}", t, once=True)


def _active(st, live):
    return [t for t in st["tracked"].values() if bool(t.get("virtual")) == (not live) and t["status"] in ("resting", "open", "closing")]


def _place_new(client, st, cfg, now, ex, rows, counts, live, price_of):
    mode = "live" if live else "preview"; day = _day(st, now)
    keys = st["keys"] if live else st["pkeys"]
    busy = set(ex["positions"]) | {o.get("symbol") for o in ex["orders"]} | {t["symbol"] for t in _active(st, live)}
    act = _active(st, live); n_wait = sum(1 for t in act if t["status"] == "resting"); n_open = len(act) - n_wait
    for row in sorted((r for r in rows.values() if r["status"] == "PENDING"), key=lambda r: r["signal_t"]):
        key, sym = row["key"], row["symbol"]
        if key in keys or _retry.get(key, 0) > now or sym in busy: continue
        if n_wait >= cfg["max_resting"] or n_open >= cfg["max_open"]: break
        p = _plan(row, cfg, ex["equity"], price_of(sym))
        if not p.get("ok"):
            _retry[key] = now + RETRY_MS
            _log(st, now, "skip", f"not placed: {p.get('error')}", symbol=sym, side=row["side"], once=True, mode=mode); continue
        if not live:
            keys[key] = now
            _log(st, now, "place", f"WOULD place a post-only limit {p['side']} {p['qty_str']} @ {p['price_str']['entry']}, stop {p['price_str']['stop']}, "
                 f"target {p['price_str']['target']} (risk {p['risk_usd']:.2f} USDT)", symbol=sym, side=row["side"], mode="preview")
            n_wait += 1; busy.add(sym); continue
        with T._order_lock:
            if st["settings"]["mode"] != "live": return
            res = _send(client, p, now)
        if not res["placed"]:
            _retry[key] = now + RETRY_MS
            _log(st, now, "error", res["error"], symbol=sym, side=row["side"], once=True); continue
        t = dict(link=res["link"], key=key, symbol=sym, side=row["side"], bybit_side=p["bybit_side"], entry=p["entry"], stop=p["stop"], target=p["target"],
                 qty=p["qty"], risk_usd=p["risk_usd"], status="resting", virtual=False, placed=now, config=cfg["config"])
        st["tracked"][res["link"]] = t; keys[key] = now; day["orders"] += 1
        _log(st, now, "place", f"placed a post-only limit {p['side']} {p['qty_str']} @ {p['price_str']['entry']}, stop {p['price_str']['stop']}, "
             f"target {p['price_str']['target']} (risk {p['risk_usd']:.2f} USDT = {cfg['risk_pct']:g}% of {ex['equity']:g})", t)
        counts["placed"] += 1; n_wait += 1; busy.add(sym)


def _hold_lease(now):
    """True when this server may act: only the one with a heartbeat in the last LEASE_MS does. False when the lease cannot be read or written."""
    global _state, _saved_blob
    v, ok = A._read_key(LEASE_KEY)
    if not ok: return False
    other = isinstance(v, dict) and v.get("id") != _INSTANCE
    if other and now - int(_f(v.get("t"))) < LEASE_MS: return False
    if other or v is None:
        with _lock:
            fresh, ok2 = A._read_key(STORE_KEY)
            if not ok2: return False
            _state = _normalise(fresh); _saved_blob = None
    try:
        return bool(A._write_key(LEASE_KEY, dict(id=_INSTANCE, t=now)))
    except Exception as e:
        print(f"[amd auto] could not write the lease: {e}")
        return False


def _done(ok, msg, counts=None, now=None):
    _status.update(ok=ok, msg=msg, t=now or int(time.time() * 1000), counts=counts or {})
    return dict(_status)


def run_cycle(client, ledger=None, now=None, price_of=None):
    """One pass. Returns the status dict; never raises. ledger / price_of: for tests."""
    try:
        return _run_cycle(client, ledger, now, price_of or _last_price)
    except Exception as e:
        print(f"[amd auto] pass failed: {e}")
        return _done(False, f"the pass failed: {e}")


def _run_cycle(client, ledger, now, price_of):
    st = _ensure()
    if st is None: return _done(False, "the store could not be read: nothing was done")
    if not _cycle_lock.acquire(blocking=False): return dict(_status)
    try:
        cfg = dict(st["settings"])
        if cfg["mode"] == "off": return _done(True, "off")
        now = now or A._now(client)
        if not _hold_lease(now): return _done(True, "another server is running the AMD auto-orders (or the lease could not be read): this one waits", now=now)
        st = _state; cfg = dict(st["settings"]); mode = cfg["mode"]; live = mode == "live"
        if mode == "off": return _done(True, "off", now=now)
        if T.account(client) == "REAL":
            st["settings"]["mode"] = "off"
            _log(st, now, "halt", "AMD auto-orders only run on a demo / testnet account; this server is on a REAL-money account, so they were switched off", mode=mode)
            _save(); return _done(False, "real-money account: switched off", now=now)
        ex, err = A._read_exchange(client)
        if err:
            _log(st, now, "error", err, once=True, mode=mode); _save(); return _done(False, err, now=now)
        ex["equity_real"] = ex["equity"]; ex["equity"] = min(ex["equity"], cfg["equity_usd"]) if cfg["equity_usd"] > 0 else ex["equity"]
        rows = ledger if ledger is not None else _ledger(cfg["config"], now)
        counts = dict(placed=0, cancelled=0, filled=0, closed=0)
        if live: _sync(client, st, cfg, now, ex, rows, counts)
        _place_new(client, st, cfg, now, ex, rows, counts, live, price_of)
        for k in [k for k, t in st["tracked"].items() if t["status"] in ("cancelled", "gone", "done") and now - t.get("done", now) > KEEP_MS]: del st["tracked"][k]
        for d in ("keys", "pkeys"):
            for k in [k for k, v in st[d].items() if now - v > 14 * 86_400_000]: del st[d][k]
        act = _active(st, live); nw = sum(1 for t in act if t["status"] == "resting")
        _save()
        return _done(True, f"{mode}: {sum(1 for r in rows.values() if r['status'] == 'PENDING')} pending setups in the ledger; {nw} waiting orders, {len(act) - nw} open trades; "
                     f"this pass placed {counts['placed']}, cancelled {counts['cancelled']}, closed {counts['closed']}", counts, now)
    finally:
        _cycle_lock.release()


# ── API ──────────────────────────────────────────────────────────────────────────────────────────────────────────────
def status(client=None, full_log=False):
    """GET /api/amd/auto."""
    st = _ensure()
    if st is None: return dict(ok=False, error="the store could not be read; try again in a minute")
    with _lock:
        tr = sorted(st["tracked"].values(), key=lambda t: -t.get("placed", 0))
        rows = [{k: t.get(k) for k in ("link", "symbol", "side", "status", "entry", "stop", "target", "qty", "risk_usd", "placed", "done", "R", "virtual")} for t in tr]
        return dict(ok=True, account=T.account(client) if client else None, settings=dict(st["settings"]), day=dict(st["day"]), status=dict(_status),
                    tracked=rows, log=list(st["log"][:LOG_MAX if full_log else 100]), log_total=len(st["log"]), running=bool(_thread and _thread.is_alive()))


def _leave_live(client, st, now, close_positions=False):
    n_c = n_x = 0; errs = []
    for link, t in list(st["tracked"].items()):
        if t.get("virtual"):
            t["status"] = "cancelled"; t["done"] = now; continue
        if t["status"] == "resting":
            if _cancel(client, st, now, t, link, "AMD auto-orders switched off"): n_c += 1
            else: errs.append(t["symbol"])
        elif close_positions and t["status"] == "open":
            if _close_market(client, st, now, t, "closed at the market (AMD auto-orders switched off)"): n_x += 1
            else: errs.append(t["symbol"])
    return dict(cancelled=n_c, closed=n_x, failed=errs)


def update_settings(patch, client, confirm=False):
    """POST /api/amd/auto/settings."""
    st = _ensure()
    if st is None: return dict(ok=False, error="the store could not be read; try again in a minute")
    try:
        new = clean_settings(patch, st["settings"])
    except ValueError as e:
        return dict(ok=False, error=str(e))
    old_mode, mode = st["settings"]["mode"], new["mode"]
    if mode != "off" and T.account(client) == "REAL":
        return dict(ok=False, error="AMD auto-orders only run on a demo / testnet account; this server is on a REAL-money account")
    if mode == "live" and old_mode != "live" and confirm is not True:
        return dict(ok=False, need_confirm=True, error="going live places orders on the demo account by itself: confirm it")
    if mode == old_mode:
        with _lock: st["settings"] = new
        _save()
        return dict(ok=True, settings=new, note=None)
    if not _cycle_lock.acquire(timeout=30): return dict(ok=False, error="a pass is running; try again in a few seconds")
    try:
        now = A._now(client); note = None
        with _lock: st["settings"] = new
        if old_mode == "live" and mode != "live":
            note = _leave_live(client, st, now)
            _log(st, now, "info", f"left live mode: cancelled {note['cancelled']} waiting order(s); open trades keep their stop and target on Bybit", mode=mode)
        else:
            for t in [t for t in st["tracked"].values() if t.get("virtual")]: t["status"] = "cancelled"; t["done"] = now
            st["pkeys"] = {}
            _log(st, now, "info", f"mode: {old_mode} -> {mode}", mode=mode)
        if mode == "live": _retry.clear()
        _save()
        return dict(ok=True, settings=new, note=note)
    finally:
        _cycle_lock.release()


def kill(client, close_positions=False):
    """POST /api/amd/auto/kill: switch off, cancel the waiting orders, optionally close the open trades at the market."""
    st = _ensure()
    if st is None: return dict(ok=False, error="the store could not be read; try again in a minute")
    with _lock: st["settings"]["mode"] = "off"
    if not _cycle_lock.acquire(timeout=30): return dict(ok=False, error="switched off, but a pass is still running; press again in a few seconds")
    try:
        now = A._now(client)
        note = _leave_live(client, st, now, close_positions)
        _log(st, now, "halt", f"killed: cancelled {note['cancelled']} waiting order(s), closed {note['closed']} trade(s)", mode="off")
        _save()
        return dict(ok=not note["failed"], note=note, settings=dict(st["settings"]))
    finally:
        _cycle_lock.release()


def start_thread(get_client, period=60.0, delay=100.0):
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
                print(f"[amd auto] loop: {e}")
            _stop.wait(max(5.0, period - (time.time() - t0)))

    _thread = threading.Thread(target=loop, name="amd-auto", daemon=True)
    _thread.start()
    return _thread
