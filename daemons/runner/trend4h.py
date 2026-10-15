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

from .core import CORE20, EXTRA22, H1, H4, M1, group_of, now_ms, pub_get, stop_price
from . import trading

NAME = "trend4h"
DEFAULT_COINS = CORE20 + EXTRA22
STOP_ATR, TRAIL_ATR = 2.5, 4.0
GRACE_MS = 60_000
FRESH_MS = 20 * M1                      # a signal older than this (runner was down) is not traded


def closed_1h_bars(sym, now, limit=40):
    rows = pub_get("/v5/market/kline", category="linear", symbol=sym, interval="60", limit=limit).get("result", {}).get("list", [])
    bars = [{"t": int(b[0]), "o": float(b[1]), "h": float(b[2]), "l": float(b[3]), "c": float(b[4])} for b in rows]
    return sorted([b for b in bars if b["t"] + H1 + 30_000 <= now], key=lambda b: b["t"])



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

    def manage(self, now):
        """Active position defense: excursion ratchets and 1H EMA20 breakdown guard."""
        ctx = self.ctx
        for sym, p in list(ctx.pos.items()):
            if p.get("strategy") != NAME or p.get("exit_how"):
                continue
            try:
                side = p["side"]
                sp = ctx.ex.spec(sym)
                bars1h = closed_1h_bars(sym, now, limit=30)
                if not bars1h:
                    continue
                last_bar = bars1h[-1]
                curr_px = last_bar["c"]
                p["best"] = max(p.get("best", curr_px), curr_px) if side == 1 else min(p.get("best", curr_px), curr_px)
                gain_R = side * (curr_px - p["entry"]) / p["risk"]
                p["peak_R"] = max(p.get("peak_R", 0.0), gain_R)

                # 1. Break-Even Ratchet at +0.75R peak excursion (protect against round trips; +0.05R covers fees)
                if p["peak_R"] >= 0.75 and not p.get("be_set"):
                    be_stop = stop_price(p["entry"] + side * 0.05 * p["risk"], side, sp["tick"])
                    if side * (be_stop - p["stop"]) > 0:
                        if ctx.ex.set_stop(sym, sl=be_stop):
                            ctx.log("trail", strategy=NAME, symbol=sym, old=p["stop"], new=be_stop,
                                    reason="Break-Even Ratchet (+0.75R peak)", locked_R=0.05)
                            p["stop"] = be_stop
                            p["be_set"] = True

                # 2. Stepped Ratchet at +1.50R peak excursion (locks in +0.75R)
                if p["peak_R"] >= 1.50 and not p.get("r15_set"):
                    lock_stop = stop_price(p["entry"] + side * 0.75 * p["risk"], side, sp["tick"])
                    if side * (lock_stop - p["stop"]) > 0:
                        if ctx.ex.set_stop(sym, sl=lock_stop):
                            ctx.log("trail", strategy=NAME, symbol=sym, old=p["stop"], new=lock_stop,
                                    reason="Stepped Ratchet (+1.50R peak)", locked_R=0.75)
                            p["stop"] = lock_stop
                            p["r15_set"] = True

                # 3. Hourly 1H EMA20 Breakdown Guard
                # On each newly closed 1H bar: if trade reached >= 0.50R and closes beyond 1H EMA20
                if len(bars1h) >= 20 and last_bar["t"] > p.get("last_1h_checked", 0):
                    p["last_1h_checked"] = last_bar["t"]
                    c_list = [b["c"] for b in bars1h]
                    e20_1h = ema(c_list, 20)[-1]
                    breakdown = (last_bar["c"] < e20_1h) if side == 1 else (last_bar["c"] > e20_1h)
                    if breakdown and p.get("peak_R", 0.0) >= 0.50:
                        ctx.log("guard_exit", strategy=NAME, symbol=sym, side=side,
                                reason="1H candle closed beyond 1H EMA20",
                                close=last_bar["c"], ema20=round(e20_1h, 4), peak_R=round(p.get("peak_R", 0.0), 2))
                        trading.exit_position(ctx, sym, "1h EMA20 breakdown guard")
            except Exception as e:
                ctx.log("error", strategy=NAME, symbol=sym, what="manage_failed", msg=str(e))

