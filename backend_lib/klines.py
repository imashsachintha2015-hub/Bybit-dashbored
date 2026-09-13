"""Public candle fetch, used to settle suggestions without a browser.

The engine's own shadow tracking runs in the dashboard tab and watches live
prices, which works on a desktop with the tab open and fails completely on a
phone: mobile browsers suspend background tabs, so the watcher stops and a
setup that reaches its target while the screen is off is never resolved.

Settling server-side removes the browser from the loop entirely -- the row
carries its own entry, stop and targets, so replaying candles since it was
recorded is enough to say what happened.

Bybit is tried first (the deployment runs in a region it serves) and OKX is
the fallback, matching what backtest/fetch-klines.js does.
"""
import json
import time
import urllib.error
import urllib.parse
import urllib.request

_UA = {"User-Agent": "MASIS/3.0"}


def _get(url, timeout=6):
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def fetch_1m(symbol, start_ms, limit=200):
    """Returns [{start, high, low, close}] ascending, or [] on any failure.

    Never raises: settling is a convenience and must not be able to break the
    page that triggers it.
    """
    try:
        q = urllib.parse.urlencode({
            "category": "linear", "symbol": symbol, "interval": "1",
            "start": int(start_ms), "limit": min(int(limit), 1000),
        })
        d = _get(f"https://api.bybit.com/v5/market/kline?{q}")
        rows = (d.get("result") or {}).get("list") or []
        # Bybit returns newest-first: [start, open, high, low, close, vol, turnover]
        out = [{"start": int(r[0]), "high": float(r[2]), "low": float(r[3]), "close": float(r[4])}
               for r in rows]
        if out:
            return sorted(out, key=lambda b: b["start"])
    except Exception:
        pass

    try:
        inst = symbol.replace("USDT", "-USDT-SWAP")
        q = urllib.parse.urlencode({"instId": inst, "bar": "1m", "after": "", "limit": "100"})
        d = _get(f"https://www.okx.com/api/v5/market/candles?{q}")
        rows = d.get("data") or []
        out = []
        for r in rows:
            ts = int(r[0])
            if ts < int(start_ms):
                continue
            out.append({"start": ts, "high": float(r[2]), "low": float(r[3]), "close": float(r[4])})
        return sorted(out, key=lambda b: b["start"])
    except Exception:
        return []
    return []


def settle(row, now_ms=None):
    """WIN / LOSS / None for a suggestion, by replaying candles since it fired.

    Ordering within a candle is unknowable at 1m resolution, so a bar touching
    both levels is counted as a LOSS. That is the conservative reading and it
    avoids flattering the result -- the alternative would let every ambiguous
    bar score as a win.
    """
    entry, stop = row.get("entry"), row.get("stop")
    targets = row.get("targets") or []
    started = row.get("recorded_at")
    if not (entry and stop and targets and started):
        return None
    target = targets[0]
    if target is None:
        return None
    is_long = str(row.get("direction", "")).upper().startswith("L")
    now_ms = now_ms or int(time.time() * 1000)
    if now_ms - started < 120000:          # give it at least two candles
        return None

    bars = fetch_1m(row.get("symbol", ""), started, limit=300)
    if not bars:
        return None
    for b in bars:
        hit_t = b["high"] >= target if is_long else b["low"] <= target
        hit_s = b["low"] <= stop if is_long else b["high"] >= stop
        if hit_s:
            return "LOSS"
        if hit_t:
            return "WIN"
    return None
