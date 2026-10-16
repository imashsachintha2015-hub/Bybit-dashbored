"""4h trend pullback (TREND4H-v1).

Validated on Jan 2023 - Sep 2026 (scratch/validate_trend_robust.py): positive in all three
periods, every calendar year and 17 of 20 coins, about +0.1R per trade.

  trend    4h EMA50 above EMA200 -> longs only, below -> shorts only
  setup    the previous closed 4h candle touched the EMA20
  trigger  the latest closed 4h candle closes beyond the previous candle's extreme,
           on the trend side of the EMA20
  entry    market, right after that candle closes
  stop     2.5 x ATR(14) from the fill, broker-side, attached to the order
  exit     trailing stop: best close since entry -/+ 4 x ATR, tightened after every 4h
           close (broker-side), never loosened.  No fixed target.

Expect a ~34% win rate, winners around +1.5R and losers around -0.7R.  Nearly all of the
profit comes from a few long trends, so skipping signals or cutting winners breaks it.
"""
import os

from .core import CORE20, EXTRA22, H4, M1, group_of, now_ms, pub_get, stop_price
from . import trading

NAME = "trend4h"
DEFAULT_COINS = CORE20 + EXTRA22
STOP_ATR, TRAIL_ATR = 2.5, 4.0
GRACE_MS = 60_000
FRESH_MS = 20 * M1                      # a signal older than this (runner was down) is not traded


def coins():
    raw = os.environ.get("RUNNER_TREND_COINS", "").strip()
    return [c.strip().upper() for c in raw.split(",") if c.strip()] or DEFAULT_COINS


def ema(xs, n):
    k, out = 2 / (n + 1), [xs[0]]
    for x in xs[1:]:
        out.append(x * k + out[-1] * (1 - k))
    return out


def indicators(bars):
    c = [b["c"] for b in bars]
    h = [b["h"] for b in bars]
    l = [b["l"] for b in bars]
    pc = [c[0]] + c[:-1]
    tr = [max(h[i] - l[i], abs(h[i] - pc[i]), abs(l[i] - pc[i])) for i in range(len(c))]
    return {"c": c, "h": h, "l": l, "e20": ema(c, 20), "e50": ema(c, 50), "e200": ema(c, 200), "atr": ema(tr, 14)}


def signal(x, i):
    """+1 long, -1 short, 0 none.  Identical to the research definition (checked in tests/test_runner.py)."""
    up, dn = x["e50"][i] > x["e200"][i], x["e50"][i] < x["e200"][i]
    c, h, l, e20 = x["c"], x["h"], x["l"], x["e20"]
    if up and l[i - 1] <= e20[i - 1] and c[i] > h[i - 1] and c[i] > e20[i]:
        return 1
    if dn and h[i - 1] >= e20[i - 1] and c[i] < l[i - 1] and c[i] < e20[i]:
        return -1
    return 0


def closed_bars(sym, now, limit=1000):
    rows = pub_get("/v5/market/kline", category="linear", symbol=sym, interval=240, limit=limit).get("result", {}).get("list", [])
    bars = [{"t": int(b[0]), "o": float(b[1]), "h": float(b[2]), "l": float(b[3]), "c": float(b[4])} for b in rows]
    return sorted([b for b in bars if b["t"] + H4 + GRACE_MS <= now], key=lambda b: b["t"])


class Trend4h:
    name = NAME

    def __init__(self, ctx, universe=None):
        self.ctx = ctx
        self.universe = universe or coins()

    def due(self, now):
        boundary = now // H4 * H4
        return now >= boundary + GRACE_MS and self.ctx.state["trend"]["cycle"] < boundary, boundary

    def process(self, now, boundary):
        ctx = self.ctx
        for sym in self.universe:
            try:
                bars = closed_bars(sym, now)
                if len(bars) < 260:
                    continue
                bar_t = bars[-1]["t"]
                seen = ctx.state["trend"]["coin_bar"]
                if seen.get(sym, 0) >= bar_t:
                    continue
                x = indicators(bars)
                i = len(bars) - 1
                held = ctx.pos.get(sym)
                if held and held["strategy"] == NAME:
                    self._trail(held, x, i, bar_t)
                else:
                    s = signal(x, i)
                    if s and held:
                        ctx.log.once(f"t4:{sym}:{bar_t}", "skip", strategy=NAME, symbol=sym, side=s, reason=f"coin held by {held['strategy']}", bar=bar_t)
                        trading.journal(ctx, NAME, sym, s, "skipped", f"coin held by {held['strategy']}")
                    elif s and now - (bar_t + H4) > FRESH_MS:
                        ctx.log("skip", strategy=NAME, symbol=sym, side=s, reason="signal is stale (candle closed > 20 min ago)", bar=bar_t)
                    elif s:
                        trading.open_position(ctx, NAME, sym, s, STOP_ATR * x["atr"][i], 0.0, bar_t,
                                              meta={"atr": x["atr"][i], "rules": ["pullback"], "group": group_of(sym)})
                seen[sym] = bar_t
            except Exception as e:
                ctx.log("error", strategy=NAME, symbol=sym, what=type(e).__name__, msg=str(e))
        with ctx.state.lock:
            ctx.state["trend"]["cycle"] = boundary
            ctx.state.save()

    def _trail(self, p, x, i, bar_t):
        ctx = self.ctx
        side, sym = p["side"], p["sym"]
        sp = ctx.ex.spec(sym)
        p["best"] = max(p["best"], x["c"][i]) if side == 1 else min(p["best"], x["c"][i])
        new = stop_price(p["best"] - side * TRAIL_ATR * x["atr"][i], side, sp["tick"])
        if side * (new - p["stop"]) <= 0:
            return
        if not ctx.ex.set_stop(sym, sl=new):
            ctx.log("error", strategy=NAME, symbol=sym, what="set_trading_stop failed", wanted=new)
            return
        ctx.log("trail", strategy=NAME, symbol=sym, old=p["stop"], new=new, best_close=p["best"], bar=bar_t,
                locked_R=round(side * (new - p["entry"]) / p["risk"], 2))
        p["stop"] = new
