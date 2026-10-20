"""Places the pasted breakout setups as real orders on the Bybit DEMO / testnet account (never a real-money account).

Per setup (setup_tracker's list): two conditional market orders that trigger when price reaches the entry (a long when it rises to it, a short when
it falls to it), each with the setup's stop and its own take-profit: half of the size exits at TP1, half at TP2 (a setup too small to split goes
out as one order exiting at TP1). The size is the risk in USD (default 2) divided by the entry-to-stop distance, rounded to the exchange's steps.
The long and the short of one coin are an either-or pair (one-way position mode): once one of them is in, the other is cancelled. A coin where
you already hold a position or have an order of your own is skipped, and so is a setup whose entry price the market has already passed.
Untriggered orders are cancelled when the list expires, is replaced or is cleared. Open trades keep their stop and take-profit on Bybit.
State is kept under the setup_orders key (what was placed, skipped and why); orderLinkIds start with tg-."""
import json
import threading
import time
import urllib.request

from . import sbgz_auto, setup_tracker, sbgz_trade as T

KEY = "setup_orders"
PREFIX = "tg-"
DEFAULT_RISK = 2.0
LEVERAGE = 10
MAX_SETUPS = 60
_lock = threading.Lock()
_last_manage = 0.0


def _f(x, d=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


def _load():
    doc, ok = sbgz_auto._read_key(KEY)
    return (doc or dict(created=None, orders={}, skipped=[], placed_at=None)), ok


def _link(created, coin, side, leg):
    return f"{PREFIX}{int(created) // 1000 % 10**9:x}-{coin}-{side[0]}{leg}"[:36]


def _plan_setup(client, r, risk_usd, spec):
    """One setup -> list of order bodies (sans link / leverage), or (None, reason)."""
    sd = 1 if r["side"] == "LONG" else -1; tick, step = spec["tick"], spec["qty_step"]
    entry = T.round_step(r["entry"], tick)
    stop = T.floor_step(r["stop"], tick) if sd == 1 else T.ceil_step(r["stop"], tick)             # the stop away from the entry
    tps = [T.floor_step(r["tp1"], tick) if sd == 1 else T.ceil_step(r["tp1"], tick)]               # a target towards the entry
    if r.get("tp2"): tps.append(T.floor_step(r["tp2"], tick) if sd == 1 else T.ceil_step(r["tp2"], tick))
    dist = abs(entry - stop)
    if dist <= 0 or sd * (stop - entry) >= 0 or any(sd * (t - entry) <= 0 for t in tps): return None, "levels collapse on the exchange's price step"
    total = T.floor_step(risk_usd / dist, step)
    if spec.get("max_qty"): total = min(total, T.floor_step(spec["max_qty"], step))
    ok = lambda q: q > 0 and q >= (spec.get("min_qty") or 0) and q * entry >= (spec.get("min_notional") or 0)
    if not ok(total): return None, f"too small for Bybit's minimum order at ${risk_usd:g} risk (needs about ${(spec.get('min_notional') or 5):g} notional)"
    if len(tps) == 2:
        q1 = T.floor_step(total / 2, step); q2 = round(total - q1, 12)
        legs = [(1, q1, tps[0]), (2, q2, tps[1])] if ok(q1) and ok(q2) else [(1, total, tps[0])]
    else:
        legs = [(1, total, tps[0])]
    return dict(entry=entry, stop=stop, legs=legs, risk=round(total * dist, 4)), None


def _body(coin, side, entry, stop, qty, tp, link, spec):
    return dict(category="linear", symbol=coin + "USDT", side="Buy" if side == "LONG" else "Sell", orderType="Market", qty=T.fmt(qty, spec["qty_step"]),
                triggerPrice=T.fmt(entry, spec["tick"]), triggerDirection=1 if side == "LONG" else 2, triggerBy="LastPrice",
                stopLoss=T.fmt(stop, spec["tick"]), takeProfit=T.fmt(tp, spec["tick"]), tpslMode="Partial", tpOrderType="Limit", tpLimitPrice=T.fmt(tp, spec["tick"]),
                slOrderType="Market", tpTriggerBy="LastPrice", slTriggerBy="LastPrice", orderLinkId=link)


def _prices():
    """{symbol: last price} of every linear perpetual (one public request); {} when Bybit does not answer."""
    try:
        with urllib.request.urlopen(urllib.request.Request("https://api.bybit.com/v5/market/tickers?category=linear", headers={"User-Agent": "MASIS/3.0"}), timeout=8) as r:
            rows = (json.loads(r.read().decode()).get("result") or {}).get("list") or []
        return {x["symbol"]: _f(x.get("lastPrice")) for x in rows if x.get("symbol") and _f(x.get("lastPrice")) > 0}
    except Exception:
        return {}


def _past(side, price, level):
    """True when the price is at or beyond a level on the stop's side (a LONG's stop / invalidation is below, a SHORT's above)."""
    return bool(level and price) and ((price <= level) if side == "LONG" else (price >= level))


def _exchange(client):
    """(positions {symbol: row}, other-order symbols set, error). Read-only."""
    pos = client.get_positions()
    if pos.get("retCode") != 0: return None, None, f"Bybit positions: {pos.get('retMsg')} (code {pos.get('retCode')})"
    positions = {p["symbol"]: p for p in (pos.get("result") or {}).get("list") or [] if _f(p.get("size")) > 0 and p.get("symbol")}
    busy = set(); cursor = None
    for flt in ("Order", "StopOrder"):
        cursor = None
        for _ in range(6):
            q = {"category": "linear", "settleCoin": "USDT", "limit": "50", "orderFilter": flt}
            if cursor: q["cursor"] = cursor
            od = client.signed_request("GET", "/v5/order/realtime", q)
            if od.get("retCode") != 0: return None, None, f"Bybit orders: {od.get('retMsg')} (code {od.get('retCode')})"
            for o in (od.get("result") or {}).get("list") or []:
                if not str(o.get("orderLinkId") or "").startswith(PREFIX) and o.get("symbol"): busy.add(o["symbol"])
            cursor = (od.get("result") or {}).get("nextPageCursor")
            if not cursor: break
    return positions, busy, None


def cancel_all(client, why="replaced"):
    """Cancel every untriggered order this module placed. Returns the number cancelled."""
    with _lock:
        st, ok = _load()
        if not ok: return 0
        n = 0
        for link, o in st["orders"].items():
            if o["status"] != "waiting": continue
            r = client.signed_request("POST", "/v5/order/cancel", body=dict(category="linear", symbol=o["coin"] + "USDT", orderLinkId=link, orderFilter="StopOrder"))
            if r.get("retCode") in (0, 110001, 110010, 20001): o["status"] = "cancelled"; o["why"] = why; n += 1
        sbgz_auto._write_key(KEY, st)
        return n


def cancel_setup(client, coin, side, why):
    """Cancel the untriggered orders of one setup (its price went past the invalidation level before the entry). Returns the number cancelled."""
    with _lock:
        st, ok = _load()
        if not ok: return 0
        n = 0
        for link, o in st["orders"].items():
            if o["coin"] != coin or o["side"] != side or o["status"] != "waiting": continue
            r = client.signed_request("POST", "/v5/order/cancel", body=dict(category="linear", symbol=coin + "USDT", orderLinkId=link, orderFilter="StopOrder"))
            if r.get("retCode") in (0, 110001, 110010, 20001): o["status"] = "cancelled"; o["why"] = why; n += 1
        if n: sbgz_auto._write_key(KEY, st)
        return n


PACE = 0.15                            # seconds between order requests: Bybit refuses bursts ("Too many visits", retCode 10006)
RATE_CODES = (10006, 10018)


def _rate_limited(res):
    return res.get("retCode") in RATE_CODES or "too many visits" in str(res.get("retMsg") or "").lower()


def _call(client, path, body, tries=5):
    """One POST that waits and tries again while Bybit says it is being asked too often."""
    res = {}
    for i in range(tries):
        res = client.signed_request("POST", path, body=body)
        if not _rate_limited(res): break
        time.sleep(0.8 * (i + 1))
    time.sleep(PACE)
    return res


def place_all(client, created, rows, risk_usd=DEFAULT_RISK, resume=False):
    """Place the orders of a freshly pasted list (resume=True: the same list again, only the orders that are missing). Requests are paced and
    retried when Bybit says "too many visits". Returns dict(ok, placed_orders, placed_setups, skipped=[(coin, side, why)], error)."""
    if T.account(client) == "REAL":
        return dict(ok=False, error="this server is on a REAL-money account: orders are only placed on a demo / testnet account")
    risk_usd = float(risk_usd or DEFAULT_RISK)
    old, ok = _load()
    resume = bool(resume and ok and old.get("created") == created)
    if not resume: cancel_all(client, "a new list was pasted")
    positions, busy, err = _exchange(client)
    if err: return dict(ok=False, error=err)
    st = old if resume else dict(created=created, orders={}, skipped=[], placed_at=int(time.time() * 1000), risk=risk_usd)
    skipped = []; prices = _prices()
    with _lock:
        for r in rows[:MAX_SETUPS]:
            coin, side, sym = r["coin"], r["side"], r["coin"] + "USDT"
            mine = [o for o in st["orders"].values() if o["coin"] == coin and o["side"] == side]
            px = prices.get(sym)
            if not mine and _past(side, px, r.get("inval")): skipped.append((coin, side, f"price {px:g} is already past the invalidation level {r['inval']:g}: the setup is void")); continue
            if not mine and px and ((px >= r["entry"]) if side == "LONG" else (px <= r["entry"])):
                skipped.append((coin, side, f"price {px:g} is already through the entry {r['entry']:g}: a breakout order there would fill at once")); continue
            if sym in positions and not mine: skipped.append((coin, side, "you already hold a position on this coin")); continue
            if sym in busy: skipped.append((coin, side, "you already have an order of your own on this coin")); continue
            spec = T.instrument(sym)
            if not spec or not spec.get("tick") or not spec.get("qty_step"): skipped.append((coin, side, "not tradable on Bybit demo")); continue
            plan, why = _plan_setup(client, r, risk_usd, spec)
            if not plan: skipped.append((coin, side, why)); continue
            if all(_link(created, coin, side, leg) in st["orders"] for leg, _, _ in plan["legs"]): continue        # already placed
            if sym in positions: continue                                                                           # its pair or this side is in already
            lv = client.set_leverage(sym, LEVERAGE)
            if _rate_limited(lv): time.sleep(1.0); lv = client.set_leverage(sym, LEVERAGE)
            if lv.get("retCode") not in (0, 110043): print(f"[setup orders] leverage {sym}: {lv.get('retCode')} {lv.get('retMsg')} (placing anyway)")
            time.sleep(PACE)
            first_err = None; got = 0
            for leg, qty, tp in plan["legs"]:
                link = _link(created, coin, side, leg)
                if link in st["orders"]: got += 1; continue
                res = _call(client, "/v5/order/create", _body(coin, side, plan["entry"], plan["stop"], qty, tp, link, spec))
                if res.get("retCode") in (0, 110072):                                                              # 110072: this link is already on Bybit
                    got += 1
                    st["orders"][link] = dict(coin=coin, side=side, leg=leg, qty=qty, entry=plan["entry"], stop=plan["stop"], tp=tp, inval=r.get("inval"), status="waiting",
                                              placed=int(time.time() * 1000))
                else:
                    first_err = first_err or f"Bybit refused it: {res.get('retMsg')} (code {res.get('retCode')})"
            if not got: skipped.append((coin, side, first_err or "not placed"))
            elif first_err: skipped.append((coin, side, "only one of its two orders was accepted: " + first_err))
        st["skipped"] = [list(x) for x in skipped]
        sbgz_auto._write_key(KEY, st)
    placed_setups = len({(o["coin"], o["side"]) for o in st["orders"].values()})
    print(f"[setup orders] {len(st['orders'])} orders for {placed_setups} setups, skipped {len(skipped)}")
    return dict(ok=True, placed_orders=len(st["orders"]), placed_setups=placed_setups, skipped=skipped, risk=risk_usd)


def retryable(res):
    """True when a placement report has setups that only failed because Bybit was being asked too often."""
    return bool(res.get("ok")) and any(_rate_limited(dict(retMsg=w)) or "code 10006" in w for _, _, w in res["skipped"])


def manage(client, now=None, force=False):
    """One pass (the caller runs it every ~20 s; at most every 15 s): an order whose coin now holds a position of its side, opened after the order
    was placed, is 'filled' and its either-or partner is cancelled; an untriggered order is cancelled once the price is past its setup's
    invalidation level, or once the list has expired. Returns a list of event strings (for Telegram)."""
    global _last_manage
    now = now or int(time.time() * 1000)
    if not force and now - _last_manage < 15_000: return []
    _last_manage = now
    events = []; expire_ms = setup_tracker.expiry_ms()
    with _lock:
        st, ok = _load()
        if not ok or not any(o["status"] in ("waiting", "filled") for o in st["orders"].values()): return events
        positions, busy, err = _exchange(client)
        if err: return events
        prices = _prices() if any(o["status"] == "waiting" and o.get("inval") for o in st["orders"].values()) else {}
        sbgz_auto_changed = False
        for link, o in st["orders"].items():
            sym = o["coin"] + "USDT"; pos = positions.get(sym)
            side_ok = bool(pos) and pos.get("side") == ("Buy" if o["side"] == "LONG" else "Sell") and (int(_f(pos.get("createdTime"))) or 10**15) >= o.get("placed", 0) - 5_000   # a position you already had is not a fill
            if o["status"] == "waiting" and side_ok:
                o["status"] = "filled"; o["fill"] = now; sbgz_auto_changed = True
                if not any(e.startswith(f"🟡 {o['coin']} {o['side']} ") for e in events):
                    events.append(f"🟡 {o['coin']} {o['side']} triggered at about {pos.get('avgPrice')} (stop {o['stop']}, targets on Bybit)")
                for l2, o2 in st["orders"].items():                                         # the either-or partner (other side of the coin)
                    if o2["coin"] == o["coin"] and o2["side"] != o["side"] and o2["status"] == "waiting":
                        r = client.signed_request("POST", "/v5/order/cancel", body=dict(category="linear", symbol=sym, orderLinkId=l2, orderFilter="StopOrder"))
                        if r.get("retCode") in (0, 110001, 110010, 20001): o2["status"] = "cancelled"; o2["why"] = "the other side triggered"
            elif o["status"] == "filled" and not pos: o["status"] = "done"; o["done"] = now; sbgz_auto_changed = True
            elif o["status"] == "waiting" and _past(o["side"], prices.get(sym), o.get("inval")):
                r = client.signed_request("POST", "/v5/order/cancel", body=dict(category="linear", symbol=sym, orderLinkId=link, orderFilter="StopOrder"))
                if r.get("retCode") in (0, 110001, 110010, 20001):
                    o["status"] = "cancelled"; o["why"] = "invalidated"; sbgz_auto_changed = True
                    if not any(e.startswith(f"🚫 {o['coin']} {o['side']} ") for e in events):
                        events.append(f"🚫 {o['coin']} {o['side']} invalidated before the entry (price {prices[sym]:g} past {o['inval']:g}): its orders are cancelled")
            elif o["status"] == "waiting" and now - (st.get("created") or now) > expire_ms:
                r = client.signed_request("POST", "/v5/order/cancel", body=dict(category="linear", symbol=sym, orderLinkId=link, orderFilter="StopOrder"))
                if r.get("retCode") in (0, 110001, 110010, 20001): o["status"] = "cancelled"; o["why"] = "the list expired"; sbgz_auto_changed = True
        if sbgz_auto_changed: sbgz_auto._write_key(KEY, st)
    return events


def busy_symbols():
    """Coins where the pasted-setup orders wait or are open: the SBGZ / AMD auto-orders leave them alone (their order lists do not show
    conditional orders, and one-way mode would merge two strategies' positions on one coin)."""
    st, ok = _load()
    return {o["coin"] + "USDT" for o in st["orders"].values() if o["status"] in ("waiting", "filled")} if ok else set()


def summary():
    """For the dashboard / Telegram: dict(ok, placed_at, risk, waiting, filled, done, cancelled, skipped=[...])."""
    st, ok = _load()
    if not ok: return dict(ok=False)
    c = {}
    for o in st["orders"].values(): c[o["status"]] = c.get(o["status"], 0) + 1
    return dict(ok=True, created=st.get("created"), placed_at=st.get("placed_at"), risk=st.get("risk"), orders=len(st["orders"]), waiting=c.get("waiting", 0), filled=c.get("filled", 0),
                done=c.get("done", 0), cancelled=c.get("cancelled", 0), skipped=st.get("skipped") or [])
