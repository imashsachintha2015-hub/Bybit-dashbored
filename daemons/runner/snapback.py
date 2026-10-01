"""4h-EMA20 snap-back (SNAPBACK-v1).

Found by scratch/research_confluence.py (18,426 candidate rules; discovered 2023-01..2024-06,
validated 2024-07..2025-09, tested 2025-09..2026-09) and re-checked in snapback_robust.py.

  trigger   a 15m candle closes more than 2.5 x ATR(15m) from its EMA20 for the first time
            (the previous candle did not): fade it -- stretched down = BUY, up = SELL
  location  the close is within 0.5 x ATR(4h) of the last closed 4h EMA20
  regime    1h ATR > 1.3 x its mean over the previous 720 hours
  rule A    open interest fell >= 1% over the last 4 hours
  rule B    BTC's last closed 1h candle moved <= 0.5 x its 1h ATR (the move is coin-specific)
            (default rule set "A|B": either; env RUNNER_SNAP_RULES = A | B | A|B | A&B)
  cooldown  a trigger within 2h of the previous kept trigger on the same coin and side is ignored
  trade     market entry; stop = target = 1 x ATR(1h) from the fill, attached to the order;
            closed at market after 12h if neither is hit

Break-even after fees is about 53-54% wins; 2023-2026 results were ~60% wins, +0.14R per trade.

Everything that decides a signal is a pure function of candle lists, so the live scanner and
the history replay (scans.py, tests) run exactly the same code.
"""
import bisect
import os

from .core import CORE20, EXTRA22, H1, H4, M1, M15, group_of, now_ms, pub_get
from . import trading

NAME = "snapback"
STRETCH, LOC, VOL, OI_DROP, CALM = 2.5, 0.5, 1.3, 0.01, 0.5
COOL_BARS, HOLD_MS, WIN_BARS = 8, 12 * H1, 48
FRESH_MS = 20 * M1
GRACE_MS = 45_000
COST = 0.0012


def coins(live=False):
    """Paper tracking covers both groups (the forward data decides whether the rule generalises);
    live orders default to the coins the rule was researched on."""
    raw = os.environ.get("RUNNER_SNAP_COINS", "").strip()
    if raw:
        return [c.strip().upper() for c in raw.split(",") if c.strip()]
    return list(CORE20) if live else CORE20 + EXTRA22


def rule_mode():
    return os.environ.get("RUNNER_SNAP_RULES", "A|B").strip().upper() or "A|B"


def rules_pass(a, b, mode=None):
    mode = mode or rule_mode()
    return {"A": a, "B": b, "A|B": a or b, "A&B": a and b}.get(mode, a or b)


# ─── pure series functions ───────────────────────────────────────────────────
def ema(xs, n):
    k, out = 2 / (n + 1), [xs[0]]
    for x in xs[1:]:
        out.append(x * k + out[-1] * (1 - k))
    return out


def atr_list(bars):
    pc = [bars[0]["c"]] + [b["c"] for b in bars[:-1]]
    return ema([max(b["h"] - b["l"], abs(b["h"] - p), abs(b["l"] - p)) for b, p in zip(bars, pc)], 14)


def trigger_series(b15):
    """Per 15m candle: (ATR15, 'closed > 2.5 ATR below EMA20' flags, 'closed > 2.5 ATR above' flags)."""
    c = [b["c"] for b in b15]
    e20, a15 = ema(c, 20), atr_list(b15)
    down = [c[i] < e20[i] - STRETCH * a15[i] for i in range(len(c))]
    up = [c[i] > e20[i] + STRETCH * a15[i] for i in range(len(c))]
    return a15, down, up


def fresh_side(down, up, i):
    if i < 1:
        return 0
    if down[i] and not down[i - 1]:
        return 1
    if up[i] and not up[i - 1]:
        return -1
    return 0


class Htf:
    """Higher-timeframe context as of any 15m close, from CLOSED candles only:
    1h ATR and its ratio to the previous 720-hour mean; 4h EMA20 and ATR."""

    def __init__(self, b1, b4):
        a1 = atr_list(b1)
        pre = [0.0]
        for x in a1:
            pre.append(pre[-1] + x)
        self.end1 = [b["t"] + H1 for b in b1]
        self.atr1 = a1
        self.ratio1 = [None] * len(a1)
        for j in range(720, len(a1)):
            self.ratio1[j] = a1[j] / ((pre[j] - pre[j - 720]) / 720)
        self.end4 = [b["t"] + H4 for b in b4]
        self.e20_4 = ema([b["c"] for b in b4], 20)
        self.atr4 = atr_list(b4)

    def at(self, close_t):
        j1 = bisect.bisect_right(self.end1, close_t) - 1
        j4 = bisect.bisect_right(self.end4, close_t) - 1
        if j1 < 0 or j4 < 20 or self.ratio1[j1] is None:
            return None
        return {"atr1": self.atr1[j1], "ratio1": self.ratio1[j1], "e20_4": self.e20_4[j4], "atr4": self.atr4[j4]}


class BtcCalm:
    """Was BTC's last closed 1h candle small (body <= 0.5 ATR) as of a given time?"""

    def __init__(self, btc1):
        a = atr_list(btc1)
        self.end = [b["t"] + H1 for b in btc1]
        self.calm = [abs(b["c"] - b["o"]) <= CALM * x for b, x in zip(btc1, a)]

    def at(self, close_t):
        j = bisect.bisect_right(self.end, close_t) - 1
        return self.calm[j] if j >= 0 else False


def oi_change_4h(lookup, close_t):
    """Open-interest change over the 4h ending an hour before the signal candle closed.
    lookup(stamp_ms) -> latest hourly value stamped at or before stamp_ms (or None)."""
    now_v, then_v = lookup(close_t - H1), lookup(close_t - 5 * H1)
    return (now_v / then_v - 1) if now_v and then_v else None


def evaluate(b15, i, side, htf, btc_calm, oi_lookup):
    """All conditions for a (kept, fresh) trigger on candle i.  Returns a dict (or None if data is short)."""
    close_t = b15[i]["t"] + M15
    h = htf.at(close_t)
    if h is None:
        return None
    c = b15[i]["c"]
    res = {"close_t": close_t, "close": c, "side": side, "at_4h_ema20": abs(c - h["e20_4"]) <= LOC * h["atr4"],
           "dist_4h_atr4": (c - h["e20_4"]) / h["atr4"], "high_vol": h["ratio1"] > VOL, "vol_ratio": h["ratio1"], "atr1h": h["atr1"],
           "btc_calm": bool(btc_calm.at(close_t)), "oi_chg4h": None, "oi_falling": False, "go": False, "rules": []}
    if res["at_4h_ema20"] and res["high_vol"]:
        chg = oi_change_4h(oi_lookup, close_t)
        res["oi_chg4h"], res["oi_falling"] = chg, chg is not None and chg <= -OI_DROP
        if rules_pass(res["oi_falling"], res["btc_calm"]):
            res["go"] = True
            res["rules"] = (["A"] if res["oi_falling"] else []) + (["B"] if res["btc_calm"] else [])
    return res


def simulate(b15, i, side, atr1h):
    """Research trade: enter at the next open, stop = target = 1 ATR(1h), 48 bars, stop wins ties.
    Returns (R net of 12 bps, bars used) or None near the end of data."""
    if i + 1 >= len(b15):
        return None
    ep = b15[i + 1]["o"]
    stop, tgt = ep - side * atr1h, ep + side * atr1h
    last = min(i + WIN_BARS, len(b15) - 1)
    gross, k = None, None
    for j in range(i + 1, last + 1):
        hi, lo = b15[j]["h"], b15[j]["l"]
        hit_sl = (lo <= stop) if side == 1 else (hi >= stop)
        hit_tp = (hi >= tgt) if side == 1 else (lo <= tgt)
        if hit_sl:
            gross, k = -1.0, j
            break
        if hit_tp:
            gross, k = 1.0, j
            break
    if gross is None:
        if i + WIN_BARS >= len(b15):
            return None                          # window not finished yet
        gross, k = max(-1.0, min(1.0, side * (b15[last]["c"] - ep) / atr1h)), last
    return gross - COST * ep / atr1h, k - i


def replay_events(b15, htf, btc_calm, oi_lookup):
    """Every signal the live rules would have produced over a history (cooldown included)."""
    a15, down, up = trigger_series(b15)
    last_kept = {}
    out = []
    for i in range(1, len(b15)):
        side = fresh_side(down, up, i)
        if not side:
            continue
        t = b15[i]["t"]
        if t - last_kept.get(side, -10 ** 15) < COOL_BARS * M15:
            continue
        last_kept[side] = t
        r = evaluate(b15, i, side, htf, btc_calm, oi_lookup)
        if r and r["go"]:
            out.append((i, r))
    return out


# ─── live scanner ────────────────────────────────────────────────────────────
def fetch_bars(sym, interval, ms, now, limit):
    rows = pub_get("/v5/market/kline", category="linear", symbol=sym, interval=interval, limit=limit).get("result", {}).get("list", [])
    bars = [{"t": int(r[0]), "o": float(r[1]), "h": float(r[2]), "l": float(r[3]), "c": float(r[4])} for r in rows]
    return sorted([b for b in bars if b["t"] + ms <= now], key=lambda b: b["t"])


def live_oi_lookup(sym):
    def f(stamp):
        rows = pub_get("/v5/market/open-interest", category="linear", symbol=sym, intervalTime="1h", endTime=int(stamp), limit=1).get("result", {}).get("list", [])
        return float(rows[0]["openInterest"]) if rows else None
    return f


class Snapback:
    name = NAME

    def __init__(self, ctx, universe=None):
        self.ctx = ctx
        self.universe = universe or coins(live=ctx.mode == "live")
        self._btc = {}

    def due(self, now):
        boundary = now // M15 * M15
        return now >= boundary + GRACE_MS and self.ctx.state["snap"]["cycle"] < boundary, boundary

    def _kept(self, sym, side, t):
        """The 2h cooldown, counted from the previous KEPT trigger (as in the research)."""
        lk = self.ctx.state["snap"]["last_kept"]
        key = f"{sym}:{side}"
        if t - lk.get(key, -10 ** 15) < COOL_BARS * M15:
            return False
        lk[key] = t
        return True

    def _btc_calm(self, now, boundary):
        if self._btc.get("boundary") != boundary:
            b = fetch_bars("BTCUSDT", 60, H1, now, 300)
            self._btc = {"boundary": boundary, "calm": BtcCalm(b) if len(b) > 30 else None}
        return self._btc["calm"]

    def process(self, now, boundary):
        ctx = self.ctx
        want = boundary - M15                       # start of the candle that just closed
        pending = 0
        for sym in self.universe:
            try:
                last = ctx.state["snap"]["last_bar"].get(sym, 0)
                if last >= want:
                    continue
                b15 = fetch_bars(sym, 15, M15, now, 400)
                if len(b15) < 60:
                    continue
                if b15[-1]["t"] < want:
                    pending += 1                    # the exchange has not published the candle yet; retry next loop
                    continue
                a15, down, up = trigger_series(b15)
                first = max(1, next((k for k, b in enumerate(b15) if b["t"] > last), len(b15)) if last else len(b15) - 12)
                for i in range(first, len(b15)):
                    side = fresh_side(down, up, i)
                    if not side or not self._kept(sym, side, b15[i]["t"]):
                        continue
                    if i != len(b15) - 1:
                        continue                    # an older trigger: only the cooldown bookkeeping matters
                    self._consider(sym, side, b15, i, now)
                with ctx.state.lock:
                    ctx.state["snap"]["last_bar"][sym] = b15[-1]["t"]
            except Exception as e:
                ctx.log("error", strategy=NAME, symbol=sym, what=type(e).__name__, msg=str(e))
        with ctx.state.lock:
            if pending == 0 or now > boundary + 5 * M1:
                ctx.state["snap"]["cycle"] = boundary
            ctx.state.save()

    def _consider(self, sym, side, b15, i, now):
        ctx = self.ctx
        bar_t = b15[i]["t"]
        if now - (bar_t + M15) > FRESH_MS:
            ctx.log("skip", strategy=NAME, symbol=sym, side=side, reason="signal is stale", bar=bar_t)
            return
        # cheap local checks first so the extra requests only happen for real candidates
        calm = self._btc_calm(now, now // M15 * M15)
        b1, b4 = fetch_bars(sym, 60, H1, now, 1000), fetch_bars(sym, 240, H4, now, 400)
        if len(b1) < 760 or len(b4) < 40 or calm is None:
            ctx.log("skip", strategy=NAME, symbol=sym, side=side, reason="not enough higher-timeframe data", bar=bar_t)
            return
        r = evaluate(b15, i, side, Htf(b1, b4), calm, live_oi_lookup(sym))
        if r is None:
            return
        info = {"stretch": round((b15[i]["c"] - ema([b["c"] for b in b15], 20)[i]) / atr_list(b15)[i], 2), "at_4h": r["at_4h_ema20"], "high_vol": r["high_vol"],
                "oi_chg4h": None if r["oi_chg4h"] is None else round(r["oi_chg4h"], 4), "btc_calm": r["btc_calm"]}
        if not r["go"]:
            ctx.log("setup_rejected", strategy=NAME, symbol=sym, side=side, bar=bar_t, **info)
            return
        ctx.log("signal", strategy=NAME, symbol=sym, side=side, bar=bar_t, rules=r["rules"], **info)
        trading.open_position(ctx, NAME, sym, side, r["atr1h"], r["atr1h"], bar_t,
                              meta={"rules": r["rules"], "atr1h": r["atr1h"], "oi_chg4h": info["oi_chg4h"], "group": group_of(sym)})

    def manage(self, now):
        """12-hour time exit."""
        for sym, p in list(self.ctx.pos.items()):
            if p["strategy"] == NAME and now >= p["opened"] + HOLD_MS and not p.get("exit_how"):
                trading.exit_position(self.ctx, sym, "time exit (12h)")
