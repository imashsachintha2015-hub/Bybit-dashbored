"""Self-monitoring for the runner.

  edge monitor   every 6h: lifetime statistics of the runner's own closed trades.  A LIVE strategy
                 whose results are clearly worse than break-even is paused (no new entries; open
                 positions keep being managed) until someone resumes it.  Paper strategies are never
                 paused: their purpose is to gather evidence.
  history replay weekly, on a background thread: the last months of candles are run through the SAME
                 signal functions the live scanner uses, so a decaying edge shows up in weeks instead
                 of after hundreds of live trades.  It reports per strategy and per coin group, and
                 pauses a live strategy only on strong evidence.

Thresholds are deliberately conservative: pausing is cheap, running a broken strategy is not.
"""
import bisect
import math
import threading
import time

from . import snapback, trend4h
from .core import CORE20, EXTRA22, H1, H4, M15, DAY, now_ms, pub_get

COST = 0.0012
BREAKEVEN_SNAP_WIN = 0.535          # 1:1 trade after 12 bps of fees on a ~0.7% stop
RESEARCH = {"trend4h": {"win": 0.34, "mean_R": 0.10}, "snapback": {"win": 0.60, "mean_R": 0.14}}
EDGE_EVERY_MS = 6 * H1
DRIFT_EVERY_MS = 7 * DAY


def wilson(k, n, z=1.645):
    """Wilson score interval for a win rate; z = 1.645 -> 90% two-sided."""
    if n <= 0:
        return 0.0, 1.0
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return c - h, c + h


def summarize(rs):
    n = len(rs)
    if not n:
        return {"n": 0}
    mean = sum(rs) / n
    var = sum((r - mean) ** 2 for r in rs) / (n - 1) if n > 1 else 0.0
    wins = sum(1 for r in rs if r > 0)
    lo, hi = wilson(wins, n)
    return {"n": n, "win": round(wins / n, 4), "win_lo": round(lo, 4), "win_hi": round(hi, 4), "mean_R": round(mean, 4),
            "se": round(math.sqrt(var / n), 4), "total_R": round(sum(rs), 2)}


# ─── live edge monitor ───────────────────────────────────────────────────────
def agg_stats(a):
    n = a["n"]
    if not n:
        return {"n": 0}
    mean = a["sum"] / n
    var = max(0.0, (a["sum2"] - n * mean * mean) / (n - 1)) if n > 1 else 0.0
    lo, hi = wilson(a["wins"], n)
    return {"n": n, "win": round(a["wins"] / n, 4), "win_lo": round(lo, 4), "win_hi": round(hi, 4), "mean_R": round(mean, 4),
            "se": round(math.sqrt(var / n), 4), "total_R": round(a["sum"], 2)}


def edge_check(state, modes, log):
    """Statistics per strategy / account / group from the lifetime aggregates; pauses live strategies that are failing."""
    out, pause = {}, []
    for key, a in state["agg"].items():
        out[key] = agg_stats(a)
    for strat in ("trend4h", "snapback"):
        if modes.get(strat) != "live" or strat in state["paused"]:
            continue
        rows = [agg_stats(a) for k, a in state["agg"].items() if k.startswith(f"{strat}|live|")]
        n = sum(r.get("n", 0) for r in rows)
        if not n:
            continue
        tot = {"n": n, "sum": sum(a["sum"] for k, a in state["agg"].items() if k.startswith(f"{strat}|live|")),
               "sum2": sum(a["sum2"] for k, a in state["agg"].items() if k.startswith(f"{strat}|live|")),
               "wins": sum(a["wins"] for k, a in state["agg"].items() if k.startswith(f"{strat}|live|"))}
        s = agg_stats(tot)
        if strat == "snapback" and n >= 40 and s["win_hi"] < BREAKEVEN_SNAP_WIN:
            pause.append((strat, f"live win rate {s['win']:.0%} (90% upper bound {s['win_hi']:.0%}) is below break-even {BREAKEVEN_SNAP_WIN:.1%} after {n} trades"))
        if strat == "trend4h" and n >= 80 and s["mean_R"] + 2 * s["se"] < 0:
            pause.append((strat, f"live average {s['mean_R']:+.3f}R (+2 SE = {s['mean_R'] + 2 * s['se']:+.3f}) is negative after {n} trades"))
    return out, pause


# ─── history download ────────────────────────────────────────────────────────
def fetch_range(sym, interval, ms, start_ms, end_ms, pause=0.12):
    """Closed candles in [start_ms, end_ms), oldest first, paging backwards."""
    got = {}
    end = int(end_ms)
    while end > start_ms:
        rows = pub_get("/v5/market/kline", category="linear", symbol=sym, interval=interval, limit=1000, end=end).get("result", {}).get("list", [])
        if not rows:
            break
        for r in rows:
            t = int(r[0])
            if start_ms <= t and t + ms <= end_ms:
                got[t] = {"t": t, "o": float(r[1]), "h": float(r[2]), "l": float(r[3]), "c": float(r[4])}
        new_end = min(int(r[0]) for r in rows) - 1
        if new_end >= end:
            break
        end = new_end
        time.sleep(pause)
    return [got[t] for t in sorted(got)]


def fetch_oi(sym, start_ms, end_ms, pause=0.12):
    """Hourly open interest [(stamp, value)] oldest first."""
    got, cursor = {}, None
    while True:
        q = dict(category="linear", symbol=sym, intervalTime="1h", startTime=int(start_ms), endTime=int(end_ms), limit=200)
        if cursor:
            q["cursor"] = cursor
        res = pub_get("/v5/market/open-interest", **q).get("result", {})
        rows = res.get("list", [])
        for r in rows:
            got[int(r["timestamp"])] = float(r["openInterest"])
        cursor = res.get("nextPageCursor")
        if not rows or not cursor:
            break
        time.sleep(pause)
    return sorted(got.items())


def oi_lookup_from(pairs):
    ts = [t for t, _ in pairs]

    def f(stamp):
        j = bisect.bisect_right(ts, stamp) - 1
        return pairs[j][1] if j >= 0 else None
    return f


# ─── replays (same functions as live) ────────────────────────────────────────
def trend_replay(bars):
    """[(entry_time, R net of fees)] for the trend system over the given 4h candles."""
    if len(bars) < 260:
        return []
    x = trend4h.indicators(bars)
    n = len(bars)
    out, i = [], 210
    while i < n - 2:
        side = trend4h.signal(x, i)
        if not side or not x["atr"][i] > 0:
            i += 1
            continue
        ei = i + 1
        ep = bars[ei]["o"]
        risk = trend4h.STOP_ATR * x["atr"][i]
        stop, best, xp, j = ep - side * risk, ep, None, ei
        while j < n:
            lo, hi, op = bars[j]["l"], bars[j]["h"], bars[j]["o"]
            if side == 1 and lo <= stop:
                xp = min(op, stop) if j > ei else stop
                break
            if side == -1 and hi >= stop:
                xp = max(op, stop) if j > ei else stop
                break
            c = bars[j]["c"]
            best = max(best, c) if side == 1 else min(best, c)
            stop = max(stop, best - trend4h.TRAIL_ATR * x["atr"][j]) if side == 1 else min(stop, best + trend4h.TRAIL_ATR * x["atr"][j])
            j += 1
        if xp is None:
            break                                   # still open at the end of the data
        out.append((bars[ei]["t"], side * (xp - ep) / risk - COST * ep / risk))
        i = j + 1
    return out


def snap_replay(b15, b1, b4, btc, oi_pairs, since_ms):
    """[(entry_time, R net of fees)] for snap-back signals from since_ms on."""
    htf, calm, look = snapback.Htf(b1, b4), snapback.BtcCalm(btc), oi_lookup_from(oi_pairs)
    out = []
    for i, r in snapback.replay_events(b15, htf, calm, look):
        if b15[i]["t"] < since_ms:
            continue
        sim = snapback.simulate(b15, i, r["side"], r["atr1h"])
        if sim:
            out.append((b15[i + 1]["t"], sim[0]))
    return out


def drift_scan(log, trend_coins, snap_coins, snap_days=120, trend_days=160, stop=lambda: False):
    """Replay recent history through the live signal code.  Slow (a few thousand requests): run it on a thread."""
    now = now_ms()
    res = {"ts": now, "snap_days": snap_days, "trend_days": trend_days, "trend4h": {}, "snapback": {}, "warnings": []}
    tr = {}
    for sym in trend_coins:
        if stop():
            return None
        bars = fetch_range(sym, 240, H4, now - 1000 * H4, now)
        tr.setdefault("core20" if sym in CORE20 else "extra22", []).extend(r for t, r in trend_replay(bars) if t >= now - trend_days * DAY)
    res["trend4h"] = {g: summarize(rs) for g, rs in tr.items()}
    res["trend4h"]["all"] = summarize([r for rs in tr.values() for r in rs])
    btc = fetch_range("BTCUSDT", 60, H1, now - (snap_days + 50) * DAY, now)
    sn = {}
    for sym in snap_coins:
        if stop():
            return None
        try:
            b15 = fetch_range(sym, 15, M15, now - (snap_days + 3) * DAY, now)
            b1 = fetch_range(sym, 60, H1, now - (snap_days + 50) * DAY, now)
            b4 = fetch_range(sym, 240, H4, now - (snap_days + 80) * DAY, now)
            oi = fetch_oi(sym, now - (snap_days + 3) * DAY, now)
            if min(len(b15), len(b1), len(b4)) < 100 or not oi:
                continue
            sn.setdefault("core20" if sym in CORE20 else "extra22", []).extend(r for _, r in snap_replay(b15, b1, b4, btc, oi, now - snap_days * DAY))
        except Exception as e:
            log("error", what="drift_scan", symbol=sym, msg=str(e))
    res["snapback"] = {g: summarize(rs) for g, rs in sn.items()}
    res["snapback"]["all"] = summarize([r for rs in sn.values() for r in rs])
    for g, s in res["snapback"].items():
        if g != "all" and s.get("n", 0) >= 25 and s["win_hi"] < BREAKEVEN_SNAP_WIN:
            res["warnings"].append(f"snapback/{g}: win rate {s['win']:.0%} over the last {snap_days} days (n={s['n']}, 90% upper bound {s['win_hi']:.0%}) is below break-even")
    for g, s in res["trend4h"].items():
        if g != "all" and s.get("n", 0) >= 150 and s["mean_R"] + 2 * s["se"] < 0:
            res["warnings"].append(f"trend4h/{g}: average {s['mean_R']:+.3f}R over the last {trend_days} days (n={s['n']}) is significantly negative")
    return res


# ─── scheduler used by the runner loop ───────────────────────────────────────
class Monitor:
    def __init__(self, state, log, modes, trend_coins, snap_coins):
        self.state, self.log, self.modes = state, log, modes
        self.trend_coins, self.snap_coins = trend_coins, snap_coins
        self._thread, self._result, self._lock = None, None, threading.Lock()
        self._stop = False

    def stop(self):
        self._stop = True

    def _pause(self, name, reason):
        with self.state.lock:
            if name not in self.state["paused"]:
                self.state["paused"][name] = {"reason": reason, "since": now_ms(), "auto": True}
                self.log("pause", strategy=name, reason=reason, auto=True)
                self.state.save()

    def run_drift_now(self):
        """Blocking version (CLI --scan)."""
        r = drift_scan(self.log, self.trend_coins, self.snap_coins)
        return r

    def _drift_worker(self):
        try:
            r = drift_scan(self.log, self.trend_coins, self.snap_coins, stop=lambda: self._stop)
        except Exception as e:
            self.log("error", what="drift_scan", msg=f"{type(e).__name__}: {e}")
            r = None
        with self._lock:
            self._result = r or {"ts": now_ms(), "failed": True}

    def tick(self, now):
        sc = self.state["scan"]
        if now - sc.get("edge_at", 0) >= EDGE_EVERY_MS:
            stats, pauses = edge_check(self.state, self.modes, self.log)
            with self.state.lock:
                sc["edge"], sc["edge_at"] = stats, now
            for name, why in pauses:
                self._pause(name, why)
            self.log("edge_check", stats=stats)
            self.state.save()
        with self._lock:
            res, self._result = self._result, None
        if res is not None:
            with self.state.lock:
                sc["drift"], sc["drift_at"] = res, now
            for w in res.get("warnings", []):
                self.log("edge_warning", warning=w)
            for w in res.get("warnings", []):
                strat = w.split("/")[0]
                if self.modes.get(strat) == "live" and "core20" in w:
                    self._pause(strat, "history replay: " + w)
            self.log("drift_scan", result=res)
            self.state.save()
        if (self._thread is None or not self._thread.is_alive()) and now - sc.get("drift_at", 0) >= DRIFT_EVERY_MS and now - sc.get("drift_try", 0) >= 6 * H1:
            sc["drift_try"] = now
            self._thread = threading.Thread(target=self._drift_worker, name="drift-scan", daemon=True)
            self._thread.start()
            self.log("drift_scan_started")
