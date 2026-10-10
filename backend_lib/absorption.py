"""Absorption + HVN indicator (a bar-level proxy for the order-flow "heavy absorption + high-volume node" idea).

True absorption needs the order book and trades at every price (footprint / MBO). Public candles give one thing that is close:
the taker-buy volume of each bar (Binance futures klines), so aggressive buying = taker buys, aggressive selling = volume - taker buys.

A bar ABSORBS when, causally, at its close:
  * volume is heavy        : volume >= VOL_MULT x the median volume of the previous 50 bars,
  * aggression is one-sided: |delta| / volume >= DELTA_MIN, delta = taker buys - taker sells,
  * price made no progress : the close-to-close move is at most MOVE_MAX ATR(14), in the aggressor's direction (or against it).
Heavy aggressive BUYING that does not lift price = sellers absorbed it -> bearish ("sell absorption", marker above the bar).
Heavy aggressive SELLING that does not push price down = buyers absorbed it -> bullish ("buy absorption", marker below the bar).
A signal is marked HVN when its close is within HVN_NEAR ATR of a high-volume node of the profile built from the PREVIOUS PROFILE_BARS bars.

The HVN levels returned for drawing come from the last PROFILE_BARS closed bars: volume is spread uniformly over each bar's high-low range,
nodes are local maxima of the binned profile above NODE_MULT x the mean bin; the point of control is the highest bin.
Nothing here is a validated trading rule (see research_archive/scalp_hf: no 5-30 minute taker rule passed the 14 bps fee); it is a chart aid.
"""
import statistics

VOL_MULT, VOL_WINDOW = 1.8, 50
DELTA_MIN = 0.25
MOVE_MAX = 0.35
HVN_NEAR = 0.5
PROFILE_BARS, BINS, NODE_MULT = 300, 60, 1.5
ATR_N = 14


def atr(bars, n=ATR_N):
    out = []; a = None
    for i, b in enumerate(bars):
        pc = bars[i - 1]["c"] if i else b["c"]
        tr = max(b["h"] - b["l"], abs(b["h"] - pc), abs(b["l"] - pc))
        a = tr if a is None else (a * (n - 1) + tr) / n
        out.append(a if i >= n - 1 else None)
    return out


def profile(bars, bins=BINS):
    """(low, step, volumes[bins]) with each bar's volume spread uniformly over its high-low range"""
    if not bars: return None
    lo = min(b["l"] for b in bars); hi = max(b["h"] for b in bars)
    if hi <= lo: return None
    step = (hi - lo) / bins; vol = [0.0] * bins
    for b in bars:
        i0 = min(bins - 1, int((b["l"] - lo) / step)); i1 = min(bins - 1, int((b["h"] - lo) / step))
        share = b["v"] / (i1 - i0 + 1)
        for i in range(i0, i1 + 1): vol[i] += share
    return lo, step, vol


def nodes(prof, mult=NODE_MULT):
    """high-volume nodes: local maxima of the binned profile above mult x the mean bin, strongest first; the first is also the POC"""
    if not prof: return []
    lo, step, vol = prof; mean = sum(vol) / len(vol); out = []
    for i, v in enumerate(vol):
        if v < mult * mean: continue
        if (i == 0 or v >= vol[i - 1]) and (i == len(vol) - 1 or v >= vol[i + 1]):
            out.append({"price": lo + (i + 0.5) * step, "volume": v, "rel": v / mean})
    out.sort(key=lambda q: -q["volume"])
    return out


def signals(bars):
    """absorption signals on every closed bar of `bars` (dicts t, o, h, l, c, v, tb; tb = taker-buy volume), oldest first"""
    a = atr(bars); out = []
    for i in range(max(VOL_WINDOW, ATR_N), len(bars)):
        b = bars[i]
        if not a[i] or b["v"] <= 0 or b.get("tb") is None: continue
        med = statistics.median(x["v"] for x in bars[i - VOL_WINDOW:i])
        if med <= 0 or b["v"] < VOL_MULT * med: continue
        delta = 2 * b["tb"] - b["v"]; ratio = delta / b["v"]
        if abs(ratio) < DELTA_MIN: continue
        move = (b["c"] - b["o"]) / a[i]                       # progress of the bar in ATR, signed
        if ratio > 0 and move > MOVE_MAX: continue            # buyers did lift price: no absorption
        if ratio < 0 and move < -MOVE_MAX: continue
        side = "SELL" if ratio > 0 else "BUY"                 # the absorbing side
        nd = nodes(profile(bars[max(0, i - PROFILE_BARS):i]))
        at_hvn = any(abs(b["c"] - q["price"]) <= HVN_NEAR * a[i] for q in nd[:6])
        out.append({"t": b["t"], "side": side, "delta_ratio": round(ratio, 3), "vol_x": round(b["v"] / med, 2), "move_atr": round(move, 2),
                    "price": b["c"], "hvn": at_hvn})
    return out


def compute(bars, keep=500):
    """everything the chart draws for one coin / timeframe: markers for the last `keep` bars and the HVN lines of the current profile"""
    sig = signals(bars)
    cut = bars[-keep]["t"] if len(bars) > keep else 0
    nd = nodes(profile(bars[-PROFILE_BARS:]))
    return {"ok": True, "bars": len(bars), "signals": [s for s in sig if s["t"] >= cut], "hvn": nd[:5], "poc": nd[0]["price"] if nd else None,
            "rules": {"vol_mult": VOL_MULT, "delta_min": DELTA_MIN, "move_max_atr": MOVE_MAX, "hvn_near_atr": HVN_NEAR, "profile_bars": PROFILE_BARS},
            "note": "chart aid from taker-buy volume; not a validated trading rule"}


# ------------------------------------------------------------------ data: Binance USDT-M futures klines carry the taker-buy volume
import json, time, urllib.request, urllib.parse

INTERVALS = {"1": "1m", "3": "3m", "5": "5m", "15": "15m", "30": "30m", "60": "1h", "120": "2h", "240": "4h", "D": "1d"}
HOSTS = (("https://fapi.binance.com", "/fapi/v1/klines"), ("https://data-api.binance.vision", "/api/v3/klines"))      # futures first, spot data as the fallback (same fields)
_cache = {}


def fetch_bars(symbol, tf, limit=800):
    iv = INTERVALS.get(str(tf))
    if not iv: raise ValueError(f"timeframe {tf} not supported (use {', '.join(INTERVALS)})")
    err = None
    for host, path in HOSTS:
        try:
            url = f"{host}{path}?" + urllib.parse.urlencode({"symbol": symbol, "interval": iv, "limit": min(1000, limit)})
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "MASIS-absorption"}), timeout=10) as r: rows = json.loads(r.read().decode())
            now = int(time.time() * 1000)
            return [{"t": int(x[0]) // 1000, "o": float(x[1]), "h": float(x[2]), "l": float(x[3]), "c": float(x[4]), "v": float(x[5]), "tb": float(x[9])}
                    for x in rows if int(x[6]) < now]                      # closed bars only (x[6] = close time)
        except Exception as e: err = e
    raise RuntimeError(f"no taker-flow data for {symbol}: {err}")


def get(symbol, tf, keep=500):
    key = (symbol, str(tf)); c = _cache.get(key)
    if c and time.time() - c[0] < 20: return c[1]
    d = compute(fetch_bars(symbol, tf, keep + PROFILE_BARS), keep); d["symbol"] = symbol; d["tf"] = str(tf)
    _cache[key] = (time.time(), d)
    return d
