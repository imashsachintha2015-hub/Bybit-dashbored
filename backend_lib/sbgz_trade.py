"""Place an SBGZ setup on Bybit from the dashboard (POST /api/sbgz/order; server.py checks the trade password).

  entry   LIMIT, timeInForce PostOnly: it can only rest in the book and pay the maker fee; Bybit cancels it
          instead of letting it take liquidity
  target  take-profit triggered at the target and executed as a LIMIT at the target (tpslMode Partial)
  stop    stop-loss triggered at the stop and executed at MARKET (a stop-limit can be skipped in a fast move)
  size    qty that loses `risk_pct` of the account equity between entry and stop, within the exchange's
          step / minimum / maximum and a notional cap of equity x leverage

The setup is recomputed here from Bybit candles (backend_lib/sbgz.py); prices sent by the browser are never used,
only compared: the order is sent only if the numbers the user confirmed are still the numbers of the setup.
Orders are tagged orderLinkId 'sbgz-<symbol>-<interval>-<L|S>-<time>' so the dashboard lists and cancels exactly
its own orders. Rounding / sizing follow daemons/runner/core.py without importing the runner.
"""
import json
import math
import os
import re
import threading
import time
import urllib.parse
import urllib.request
from decimal import Decimal

from . import sbgz

MAX_RISK_PCT = 2.0
DEFAULT_RISK_PCT = 0.5
DEFAULT_LEVERAGE = 10
INTERVALS = ("15", "60")            # the tested timeframes
LINK_PREFIX = "sbgz-"
AUTO_TAG = "z"                      # orderLinkId 'sbgz-<symbol>-<interval>-<L|S>-z<time>' = placed by the auto-orders
FEE_MAKER, FEE_TAKER = 0.0002, 0.00055
_spec = {}
_order_lock = threading.Lock()      # one order at a time: two quick clicks cannot both pass the duplicate check
_last_link_s = 0                    # the seconds put in the last orderLinkId (every link gets a later one: Bybit refuses a repeated link)


def _decimals(step):
    return max(0, -Decimal(str(step)).normalize().as_tuple().exponent)


def floor_step(x, step):
    return round(math.floor(x / step + 1e-9) * step, _decimals(step)) if step else x


def ceil_step(x, step):
    return round(math.ceil(x / step - 1e-9) * step, _decimals(step)) if step else x


def round_step(x, step):
    return round(round(x / step) * step, _decimals(step)) if step else x


def fmt(x, step):
    return f"{x:.{_decimals(step)}f}"


def instrument(symbol, timeout=6):
    """Exchange limits of a trading linear perp, cached for a day: qty step / min / max, min notional, tick, max
    leverage. None when Bybit does not list it as trading."""
    hit = _spec.get(symbol)
    if hit and time.time() - hit[0] < 86_400: return hit[1]
    url = "https://api.bybit.com/v5/market/instruments-info?" + urllib.parse.urlencode(dict(category="linear", symbol=symbol))
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "MASIS/3.0"}), timeout=timeout) as r:
            it = ((json.loads(r.read().decode()).get("result") or {}).get("list") or [None])[0]
    except Exception:
        return None
    if not it or it.get("status", "Trading") != "Trading": return None
    lot, pf, lv = it.get("lotSizeFilter") or {}, it.get("priceFilter") or {}, it.get("leverageFilter") or {}
    spec = dict(qty_step=float(lot.get("qtyStep") or 0), min_qty=float(lot.get("minOrderQty") or 0),
                max_qty=float(lot.get("maxOrderQty") or 0), min_notional=float(lot.get("minNotionalValue") or 5),
                tick=float(pf.get("tickSize") or 0), max_lev=float(lv.get("maxLeverage") or 10))
    _spec[symbol] = (time.time(), spec)
    return spec


def size_qty(equity, risk_frac, entry, stop, spec, max_pos_x):
    """Same rule as the runner: risk_frac of equity between entry and stop, within exchange limits; 0 = not sizable."""
    dist = abs(entry - stop)
    if dist <= 0 or entry <= 0 or equity <= 0: return 0.0
    step = spec.get("qty_step") or 0.0
    qty = floor_step(equity * risk_frac / dist, step)
    qty = min(qty, floor_step(equity * max_pos_x / entry, step))
    if spec.get("max_qty"): qty = min(qty, floor_step(spec["max_qty"], step))
    if qty <= 0 or qty < (spec.get("min_qty") or 0) or qty * entry < (spec.get("min_notional") or 0): return 0.0
    return qty


def round_levels(z, spec):
    """(entry, stop, target) of a setup on the instrument's tick: the stop rounded away from the entry, the target towards it."""
    sd, tick = (1 if z["side"] == "LONG" else -1), spec["tick"]
    return (round_step(z["entry"], tick),
            floor_step(z["stop"], tick) if sd == 1 else ceil_step(z["stop"], tick),
            floor_step(z["target"], tick) if sd == 1 else ceil_step(z["target"], tick))


def account(client):
    base = (getattr(client, "base_url", "") or "").lower()
    return "DEMO" if "api-demo" in base else "TESTNET" if "testnet" in base else "REAL"


def equity_of(client):
    """(total equity of the unified account, error text)."""
    r = client.get_wallet_balance()
    if r.get("retCode") != 0: return 0.0, f"Bybit wallet: {r.get('retMsg')} (code {r.get('retCode')})"
    try:
        return float(r["result"]["list"][0]["totalEquity"]), None
    except (KeyError, IndexError, TypeError, ValueError):
        return 0.0, "could not read the account equity"


def plan(client, symbol, interval, side, risk_pct=DEFAULT_RISK_PCT, leverage=DEFAULT_LEVERAGE, replace=False):
    """Everything about the order without placing it: dict(ok=True, ...) or dict(ok=False, error=...).
    A plan that only fails because this button's own order already rests on the coin comes back with
    can_replace=True; asked again with replace=True it lists those orders in `replace` (place() cancels them)."""
    side, interval = str(side or "").upper(), str(interval)
    if side not in ("LONG", "SHORT"): return dict(ok=False, error="side must be LONG or SHORT")
    if interval not in INTERVALS: return dict(ok=False, error="SBGZ orders are for the tested 15m and 1h charts only")
    try:
        risk_pct, leverage = float(risk_pct), int(leverage)
    except (TypeError, ValueError):
        return dict(ok=False, error="risk_pct and leverage must be numbers")
    if not 0 < risk_pct <= MAX_RISK_PCT: return dict(ok=False, error=f"risk per trade must be above 0 and at most {MAX_RISK_PCT}%")
    if not 1 <= leverage <= 50: return dict(ok=False, error="leverage must be 1-50")
    acct = account(client)
    if acct == "REAL" and os.environ.get("DASHBOARD_ALLOW_REAL_MONEY", "") != "1":
        return dict(ok=False, account=acct, error="this server trades a REAL-money Bybit account: dashboard orders there need DASHBOARD_ALLOW_REAL_MONEY=1")
    r = sbgz.get(symbol, interval, 1000)
    if not r.get("ok"): return dict(ok=False, error=r.get("error") or f"no candles for {symbol}")
    price = r["candles"][-1]["close"]
    z = next((s for s in r.get("setups") or [] if s.get("side") == side and s.get("strong")), None)
    if not z: return dict(ok=False, error=f"no armed strong-break {side} setup on {symbol} {'1h' if interval == '60' else '15m'} right now")
    spec = instrument(symbol)
    if not spec or not spec["tick"] or not spec["qty_step"]: return dict(ok=False, error=f"no instrument info for {symbol} (not a trading USDT perpetual?)")
    sd, tick = (1 if side == "LONG" else -1), spec["tick"]
    entry, stop, target = round_levels(z, spec)
    if sd * (target - entry) <= 0 or sd * (entry - stop) <= 0: return dict(ok=False, error="entry, stop and target collapse at this tick size")
    if sd * (price - entry) <= 0:
        return dict(ok=False, error=f"price {price:g} is already through the entry {entry:g}: a post-only limit there would be cancelled (missed the fill)")
    leverage = max(1, min(leverage, int(spec["max_lev"])))
    eq, err = equity_of(client)
    if eq <= 0: return dict(ok=False, account=acct, error=err or "the account equity is 0")
    qty = size_qty(eq, risk_pct / 100, entry, stop, spec, leverage)
    if qty <= 0:
        return dict(ok=False, account=acct, equity=eq, error=f"too small to size at {risk_pct}% risk of {eq:.2f} USDT "
                    f"(Bybit minimum {spec['min_qty']:g} {symbol[:-4]} and {spec['min_notional']:g} USDT)")
    notional = qty * entry
    p = dict(ok=True, account=acct, symbol=symbol, interval=interval, side=side, bybit_side="Buy" if sd == 1 else "Sell",
             entry=entry, stop=stop, target=target, qty=qty, qty_str=fmt(qty, spec["qty_step"]),
             price_str=dict(entry=fmt(entry, tick), stop=fmt(stop, tick), target=fmt(target, tick)),
             price=price, dist_pct=sd * (price - entry) / price * 100,
             equity=eq, risk_pct=risk_pct, risk_usd=qty * abs(entry - stop), reward_usd=qty * abs(target - entry),
             fees_usd=dict(win=notional * FEE_MAKER + qty * target * FEE_MAKER, loss=notional * FEE_MAKER + qty * stop * FEE_TAKER),
             rr=abs(target - entry) / abs(entry - stop), notional=notional, leverage=leverage, margin=notional / leverage,
             vol_ok=bool(z.get("vol_ok")), bvol=z.get("bvol"), break_vu=z.get("break_vu"),
             since=(r.get("times") or [None])[z["since_bar"]] if isinstance(z.get("since_bar"), int) and 0 <= z["since_bar"] < len(r.get("times") or []) else None,
             exp_r=sbgz.EXP_R.get((interval, bool(z.get("vol_ok")))), replace=[])
    # one position or resting order per coin, like the rest of the dashboard; fail closed if Bybit cannot tell us
    pos, ords = client.get_positions(symbol), client.get_open_orders(symbol)
    if pos.get("retCode") != 0 or ords.get("retCode") != 0:
        bad = pos if pos.get("retCode") != 0 else ords
        return dict(p, ok=False, error=f"could not check the open positions / orders: {bad.get('retMsg')} (code {bad.get('retCode')})")
    if any(float(x.get("size") or 0) > 0 for x in (pos.get("result") or {}).get("list") or []):
        return dict(p, ok=False, error=f"already holding a {symbol} position")
    resting = (ords.get("result") or {}).get("list") or []
    mine = [o for o in resting if str(o.get("orderLinkId") or "").startswith(LINK_PREFIX)]
    if len(mine) < len(resting):
        return dict(p, ok=False, error=f"an order for {symbol} that this button did not place is resting: cancel it first")
    if mine:
        p["resting"] = [dict(side=o.get("side"), price=o.get("price"), qty=o.get("qty"), link=o.get("orderLinkId")) for o in mine]
        if not replace:
            return dict(p, ok=False, can_replace=True, error=f"your SBGZ order on {symbol} is already resting "
                        f"({mine[0].get('side')} {mine[0].get('qty')} @ {mine[0].get('price')})")
        p["replace"] = [o.get("orderLinkId") for o in mine]
    return p


def place(client, p, tag=""):
    """Send the order described by an ok plan(): cancel this button's resting order on the coin first if asked.
    tag: one letter put in front of the time in the orderLinkId ('z' = placed by the auto-orders, see sbgz_auto.py)."""
    for link in p.get("replace") or []:
        c = cancel(client, p["symbol"], link)
        if not c["ok"]: return dict(p, ok=False, placed=False, error=f"could not cancel the resting order first: {c['error']}")
    lv = client.set_leverage(p["symbol"], p["leverage"])
    if lv.get("retCode") not in (0, 110043):     # 110043 = already at that leverage
        print(f"[sbgz order] leverage {p['symbol']} {p['leverage']}x: {lv.get('retCode')} {lv.get('retMsg')} (placing anyway)")
    global _last_link_s
    _last_link_s = max(int(time.time()), _last_link_s + 1)
    link = f"{LINK_PREFIX}{p['symbol']}-{p['interval']}-{p['side'][0]}-{tag}{_last_link_s:x}"[:36]
    body = dict(category="linear", symbol=p["symbol"], side=p["bybit_side"], orderType="Limit", qty=p["qty_str"],
                price=p["price_str"]["entry"], timeInForce="PostOnly", orderLinkId=link,
                takeProfit=p["price_str"]["target"], stopLoss=p["price_str"]["stop"], tpslMode="Partial",
                tpOrderType="Limit", tpLimitPrice=p["price_str"]["target"], slOrderType="Market",
                tpTriggerBy="LastPrice", slTriggerBy="LastPrice")
    res = client.signed_request("POST", "/v5/order/create", body=body)
    ok = res.get("retCode") == 0
    print(f"[sbgz order] {p['account']} {p['symbol']} {p['side']} {p['qty_str']} @ {p['price_str']['entry']} "
          f"SL {p['price_str']['stop']} TP {p['price_str']['target']}: {'placed' if ok else res.get('retMsg')}")
    return dict(p, ok=ok, placed=ok, order_link_id=link, order_id=(res.get("result") or {}).get("orderId"), bybit=res,
                error=None if ok else f"Bybit refused the order: {res.get('retMsg')} (code {res.get('retCode')})")


def order(client, req):
    """POST /api/sbgz/order. req: symbol, interval, side, risk_pct, leverage, replace, preview (anything but an
    explicit false only previews), confirm = {qty, entry, stop, target} strings from the preview the user accepted."""
    symbol = str(req.get("symbol") or "").upper()
    if not re.fullmatch(r"[A-Z0-9]{2,20}USDT", symbol): return dict(ok=False, placed=False, error="symbol must be a USDT perpetual such as BTCUSDT")
    with _order_lock:
        p = plan(client, symbol, req.get("interval", "15"), req.get("side"), req.get("risk_pct", DEFAULT_RISK_PCT),
                 req.get("leverage", DEFAULT_LEVERAGE), replace=bool(req.get("replace")))
        if req.get("preview", True) is not False or not p.get("ok"): return dict(p, placed=False)
        seen = req.get("confirm") or {}
        if any(str(seen.get(k)) != v for k, v in dict(qty=p["qty_str"], **p["price_str"]).items()):
            return dict(p, placed=False, changed=True, notice="the setup or the size changed since your preview: check the new numbers and confirm again")
        return place(client, p)


def open_orders(client):
    """Resting orders this button placed, each with what its setup says now (same entry / moved / gone)."""
    r = client.signed_request("GET", "/v5/order/realtime", {"category": "linear", "settleCoin": "USDT", "limit": "50"})
    if r.get("retCode") != 0: return dict(ok=False, orders=[], error=f"{r.get('retMsg')} (code {r.get('retCode')})")
    out = []
    for o in (r.get("result") or {}).get("list") or []:
        link = str(o.get("orderLinkId") or "")
        if not link.startswith(LINK_PREFIX): continue
        row = dict(symbol=o.get("symbol"), side=o.get("side"), price=o.get("price"), qty=o.get("qty"), filled=o.get("cumExecQty"),
                   stop=o.get("stopLoss"), target=o.get("takeProfit"), status=o.get("orderStatus"), link=link,
                   created=o.get("createdTime"), setup="unknown", setup_entry=None)
        parts = link.split("-")
        row["auto"] = len(parts) == 5 and parts[4][:1] == AUTO_TAG
        if len(parts) == 5 and parts[2] in INTERVALS:
            row.update(interval=parts[2], sbgz_side="LONG" if parts[3] == "L" else "SHORT")
            try:
                g, spec = sbgz.get(row["symbol"], parts[2], 1000), instrument(row["symbol"])
                z = next((s for s in g.get("setups") or [] if s.get("side") == row["sbgz_side"] and s.get("strong")), None) if g.get("ok") else None
                if g.get("ok") and spec:
                    now = round_step(z["entry"], spec["tick"]) if z else None
                    row.update(setup_entry=now, setup="gone" if z is None else "same" if abs(now - float(row["price"])) < spec["tick"] / 2 else "moved")
            except Exception as e:
                print(f"[sbgz orders] {link}: {e}")
        out.append(row)
    return dict(ok=True, account=account(client), orders=out)


def cancel(client, symbol, link):
    """Cancel one resting order this button placed. By orderLinkId, so nothing else can be cancelled from here."""
    if not str(link or "").startswith(LINK_PREFIX): return dict(ok=False, error="only orders placed by the SBGZ button can be cancelled here")
    r = client.signed_request("POST", "/v5/order/cancel", body=dict(category="linear", symbol=symbol, orderLinkId=link))
    ok = r.get("retCode") == 0
    return dict(ok=ok, bybit=r, error=None if ok else f"{r.get('retMsg')} (code {r.get('retCode')})")
