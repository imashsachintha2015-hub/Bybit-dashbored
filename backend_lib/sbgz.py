"""Strong-Break Golden Zone (SBGZ) + trend map for the dashboard chart (served by /api/sbgz).

Same rules as tradingview/strong_break_golden_zone.pine and its research twin (scratch/sbgz_reference.py,
which reproduced the backtest: +0.23R per trade, 22% winners on 15m, 43 coins, 2023-2026, after fees):
  * VU (volatility unit) = EMA-100 of true range
  * big trend = close-confirmed zigzag of 8 VU, swing (push / pullback) = 3 VU
  * setup = break of structure (a close beyond the previous swing) whose impulse is >= 13 VU,
    limit at 50% of the impulse, stop at 66%, target 27.2% beyond the impulse extreme; one trade per side
  * volume confirmation: the break candle's volume >= 2x its previous-100-candle average. In the research this
    split strong breaks into +0.33R per trade (confirmed) and ~0R (not confirmed) on 15m; trades and stats are
    reported both for all strong breaks and for a confirmed-only run (which skips unconfirmed setups)
Only closed candles are used, so a confirmed swing or trade never changes afterwards.
Standard library only: the deployment has no numpy.
"""
import bisect
import json
import os
import threading
import time
import urllib.parse
import urllib.request

P = dict(vu_len=100, swing_k=3.0, trend_k=8.0, min_break=13.0, entry=0.5, stop=0.66, target=0.272,
         fee_limit=0.0002, fee_stop=0.0008, warmup=150, vol_mult=2.0)
TF_MS = {"1": 60_000, "3": 180_000, "5": 300_000, "15": 900_000, "30": 1_800_000, "60": 3_600_000,
         "120": 7_200_000, "240": 14_400_000, "D": 86_400_000}
# research odds (pullbacks inside a confirmed big trend): chance the trend still makes a new extreme
# once the pullback has reached at least each depth of the last push; and target odds from the pullback start
DEPTHS = (0.236, 0.382, 0.5, 0.618, 0.786, 1.0, 1.272, 1.618)
ODDS = {"15": dict(base=63.0, cont=(62.8, 59.1, 53.8, 48.0, 39.2, 27.5, 16.5, 6.9), t618=37, t100=28),
        "60": dict(base=61.0, cont=(61.0, 57.2, 51.4, 45.3, 36.4, 25.1, 14.9, 6.2), t618=35, t100=26)}
# Pullback ladder ('a more accurate Fibonacci'): pullback size does not scale with the push, it is set by volatility.
# Per level below the top (in VU at the top's confirmation): % of continuing pullbacks that had ended before it, and
# the chance the big trend still makes a new extreme if price reaches it (43 coins 2023-2026, same in research and
# unseen coins and in all three periods). 8 VU is where the big trend flips by definition.
LADDER = {"15": ((4.0, 27), (4.7, 51), (5.7, 74), (6.9, 90)), "60": ((4.0, 24), (4.7, 48), (5.7, 72), (6.9, 89))}
# chance the trend continues once price reached a depth (rows: the LADDER levels in VU) for the size of the last push
# (columns: <5, 5-8, 8-12, 12+ VU); 'base' = the pullback just confirmed. Depth in VU predicts far better than depth as
# a share of the push (at 61.8% of the push the odds run from 83% for small pushes to 4% for big ones).
ODDS_VU = {"15": dict(base=(83.2, 63.9, 57.5, 56.9), table=((77.8, 56.5, 50.1, 49.8), (69.5, 46.6, 40.4, 40.7), (53.1, 30.8, 26.9, 27.6), (29.1, 15.2, 13.4, 14.7))),
           "60": dict(base=(81.4, 63.3, 55.2, 56.8), table=((76.4, 56.6, 48.7, 50.6), (67.4, 46.5, 39.3, 42.9), (50.0, 31.1, 25.9, 30.9), (26.6, 14.9, 11.9, 15.8)))}
# trend continues after the pullback passed 100% of the push, by the size of that push (<5, 5-8, 8-12, 12+ VU);
# a 100% retracement of a big push is past the 8-VU flip, which is why those odds are ~0
PUSH_START_CONT = {"15": (75.6, 23.7, 1.0, 0.0), "60": (73.4, 23.3, 1.1, 0.3)}
# backtest average R per trade (compare_strategies.py): (interval, volume-confirmed) -> R
EXP_R = {("15", True): 0.36, ("15", False): 0.04, ("60", True): 0.50, ("60", False): 0.17}
RADAR_COINS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "LINKUSDT", "SEIUSDT", "AVAXUSDT", "DOGEUSDT", "BNBUSDT",
               "ADAUSDT", "DOTUSDT", "LTCUSDT", "NEARUSDT", "APTUSDT", "SUIUSDT", "ARBUSDT", "OPUSDT", "INJUSDT", "FILUSDT",
               "UNIUSDT", "1000PEPEUSDT", "AAVEUSDT", "ALGOUSDT", "ATOMUSDT", "BCHUSDT", "CRVUSDT", "ENAUSDT", "ETCUSDT",
               "GALAUSDT", "HBARUSDT", "ICPUSDT", "IMXUSDT", "MANAUSDT", "RENDERUSDT", "RUNEUSDT", "SANDUSDT", "STXUSDT",
               "TAOUSDT", "TIAUSDT", "TRXUSDT", "WLDUSDT", "XLMUSDT"]      # the runner's 42 coins
NAN = float("nan")


def _isn(x):
    return x != x


def _ema(xs, n):
    k = 2 / (n + 1); out = []; prev = None
    for x in xs:
        prev = x if prev is None else x * k + prev * (1 - k)
        out.append(prev)
    return out


class _ZZ:
    """Close-confirmed zigzag, bar by bar (identical pivots to the research zigzag)."""

    def __init__(self, k, H, L, C):
        self.k, self.H, self.L, self.C = k, H, L, C
        self.dir = 0; self.ext = self.opp = self.hi = self.lo = self.lah = self.hal = NAN
        self.extBar = self.oppBar = self.hiBar = self.loBar = self.lahBar = self.halBar = None
        self.p = [NAN] * 4; self.b = [None] * 4; self.k1 = 0; self.isNew = False; self.piv = []

    def _push(self, price, bar, kind, conf):
        self.p = [price] + self.p[:3]; self.b = [bar] + self.b[:3]
        self.k1, self.isNew = kind, True; self.piv.append((bar, price, kind, conf))

    def _after(self, frm, i, kind):            # extreme of bars frm+1..i, first occurrence
        if frm is None or frm >= i: return NAN, None
        A = self.H if kind == 1 else self.L; best, bb = A[frm + 1], frm + 1
        for j in range(frm + 2, i + 1):
            if (A[j] > best) if kind == 1 else (A[j] < best): best, bb = A[j], j
        return best, bb

    def update(self, i, u):
        h, l, c = self.H[i], self.L[i], self.C[i]
        self.isNew = False
        if self.dir == 0:
            if _isn(self.hi):
                self.hi, self.hiBar, self.lo, self.loBar = h, i, l, i
                return
            if h > self.hi: self.hi, self.hiBar, self.lah = h, i, NAN
            elif _isn(self.lah) or l < self.lah: self.lah, self.lahBar = l, i
            if l < self.lo: self.lo, self.loBar, self.hal = l, i, NAN
            elif _isn(self.hal) or h > self.hal: self.hal, self.halBar = h, i
            if self.hiBar < i and c <= self.hi - self.k * u:
                self._push(self.hi, self.hiBar, 1, i); self.dir = -1; self.ext, self.extBar = self.lah, self.lahBar
                self.opp, self.oppBar = self._after(self.extBar, i, 1)
            elif self.loBar < i and c >= self.lo + self.k * u:
                self._push(self.lo, self.loBar, -1, i); self.dir = 1; self.ext, self.extBar = self.hal, self.halBar
                self.opp, self.oppBar = self._after(self.extBar, i, -1)
        elif self.dir == 1:
            if h > self.ext: self.ext, self.extBar, self.opp = h, i, NAN
            elif _isn(self.opp) or l < self.opp: self.opp, self.oppBar = l, i
            if c <= self.ext - self.k * u:
                self._push(self.ext, self.extBar, 1, i); self.dir = -1
                self.ext, self.extBar = (c, i) if _isn(self.opp) else (self.opp, self.oppBar)
                self.opp, self.oppBar = self._after(self.extBar, i, 1)
        else:
            if l < self.ext: self.ext, self.extBar, self.opp = l, i, NAN
            elif _isn(self.opp) or h > self.opp: self.opp, self.oppBar = h, i
            if c >= self.ext + self.k * u:
                self._push(self.ext, self.extBar, -1, i); self.dir = 1
                self.ext, self.extBar = (c, i) if _isn(self.opp) else (self.opp, self.oppBar)
                self.opp, self.oppBar = self._after(self.extBar, i, -1)


class _Setup:
    """One side of the strong-break golden zone; prices kept in 'long space' (x -1 for shorts)."""

    def __init__(self, side, prm, need_vol=False):
        self.side, self.prm, self.need_vol = side, prm, need_vol      # need_vol: trade only volume-confirmed breaks
        self.pending = self.armed = self.inTrade = False
        self.L = self.Hp = self.M = self.E = self.S = self.T = self.bvol = NAN
        self.armBar = self.fillBar = None; self.lastExit = -1; self.trades = []; self.armedSince = None
        # bookkeeping for the radar history (no effect on the trades): the armed setup's prices at the last bar it was a
        # strong break, i.e. what the radar showed; and the strong setups that ended without a fill
        self.seen = None; self.missed = []

    def _arm(self, i, bos, V, VA):
        """Armed at bar i; bos = the bar whose close broke the previous swing (its volume confirms the break)."""
        self.pending, self.armed, self.armBar = False, True, i
        self.bvol = V[bos] / VA[bos] if VA[bos] > 0 else NAN
        self.seen = None

    def _unfilled(self, i, why):
        """The armed setup ends at bar i without a trade (why: a MISSED_WHY code): kept for the radar history if the
        radar ever showed it as a strong break."""
        if not self.armed or self.seen is None: return
        sd, s = self.side, self.seen
        self.missed.append(dict(side="LONG" if sd == 1 else "SHORT", arm_bar=self.armBar, seen_bar=s["bar"], end_bar=i,
                                entry=sd * s["E"], stop=sd * s["S"], target=sd * s["T"], break_vu=s["bvu"],
                                rr=(s["T"] - s["E"]) / (s["E"] - s["S"]), bvol=self.bvol, vol_ok=self.vol_ok(), why=why))

    def vol_ok(self):
        return not _isn(self.bvol) and self.bvol >= self.prm["vol_mult"]

    def _close(self, i, xp, win):
        risk = self.E - self.S; cost = self.prm["fee_limit"] + (self.prm["fee_limit"] if win else self.prm["fee_stop"])
        tr = self.trades[-1]
        tr.update(exit_bar=i, exit=self.side * xp, result="TP" if win else "SL",
                  R=(xp - self.E) / risk - cost * abs(self.E) / risk)
        self.inTrade = False; self.lastExit = i

    def step(self, i, O, H, L, C, V, VA, z, u):
        sd, p = self.side, self.prm
        hS, lS = (H[i], L[i]) if sd == 1 else (-L[i], -H[i])
        cS, oS = sd * C[i], sd * O[i]
        if self.inTrade and i > self.fillBar:
            if lS <= self.S: self._close(i, min(oS, self.S), False)
            elif hS >= self.T: self._close(i, max(oS, self.T), True)
        if z.isNew and z.k1 == -sd:
            self._unfilled(i, "swing")
            self.armed = False
            self.pending = not _isn(z.p[1])
            self.L, self.Hp, self.M = sd * z.p[0], sd * z.p[1], sd * z.ext
            if self.pending:                   # did a close already break the previous swing since the swing low?
                for j in range(max(z.b[0] + 1, i - 2899), i + 1):
                    if sd * C[j] > self.Hp:
                        self._arm(i, j, V, VA); break
        else:
            mPrev = self.M
            if self.pending or self.armed: self.M = max(self.M, hS)
            if self.pending:
                if sd * z.dir == 1 and cS > self.Hp: self._arm(i, i, V, VA)
                elif sd * z.dir == -1: self.pending = False
            elif self.armed and i > self.armBar:
                imp = self.M - self.L; ent = self.M - p["entry"] * imp
                if lS < ent:
                    vok = self.vol_ok()
                    if imp >= p["min_break"] * u and not self.inTrade and i > self.lastExit and (vok or not self.need_vol):
                        self.E = ent if hS > mPrev else min(oS, ent)
                        self.S, self.T = self.M - p["stop"] * imp, self.M + p["target"] * imp
                        self.inTrade, self.fillBar = True, i
                        self.trades.append(dict(side="LONG" if sd == 1 else "SHORT", fill_bar=i, entry=sd * self.E,
                                                stop=sd * self.S, target=sd * self.T, break_vu=imp / u,
                                                rr=(self.T - self.E) / (self.E - self.S), bvol=self.bvol, vol_ok=vok,
                                                since_bar=self.armBar))
                        if lS <= self.S: self._close(i, self.S, False)
                    else:                      # reasons: MISSED_WHY
                        self._unfilled(i, "busy" if self.inTrade or i <= self.lastExit else "weak" if imp < p["min_break"] * u else "vol")
                    self.armed = False
        if self.armed:                         # what the radar shows after this bar (zone() with this bar's VU)
            imp = self.M - self.L
            if imp >= p["min_break"] * u:
                self.seen = dict(bar=i, E=self.M - p["entry"] * imp, S=self.M - p["stop"] * imp, T=self.M + p["target"] * imp, bvu=imp / u)

    def zone(self, u):
        """The waiting setup in real prices (None when nothing is armed)."""
        if not self.armed: return None
        sd, p = self.side, self.prm; imp = self.M - self.L
        a, b = sd * (self.M - p["entry"] * imp), sd * (self.M - 0.618 * imp)
        return dict(side="LONG" if sd == 1 else "SHORT", entry=a, zone_top=max(a, b), zone_bottom=min(a, b),
                    stop=sd * (self.M - p["stop"] * imp), target=sd * (self.M + p["target"] * imp),
                    break_vu=imp / u, strong=imp >= p["min_break"] * u, since_bar=self.armBar,
                    bvol=self.bvol, vol_ok=self.vol_ok())


def compute(candles, interval="15", prm=None):
    """candles: ascending closed bars [{start, open, high, low, close, volume}]. Returns plain JSON-able data;
    bar indices refer to positions in `candles`."""
    p = dict(P, **(prm or {}))
    n = len(candles)
    out = dict(params=p, bars=n, trend=[], swings=[], trades=[], setups=[], open_trades=[], trades_v=[], open_trades_v=[], missed=[], panel={})
    if n <= p["warmup"] + 5:
        out["panel"]["note"] = "not enough candles"
        return out
    O = [float(c["open"]) for c in candles]; H = [float(c["high"]) for c in candles]
    L = [float(c["low"]) for c in candles]; C = [float(c["close"]) for c in candles]
    V = [float(c.get("volume") or 0) for c in candles]
    tr = [H[0] - L[0]] + [max(H[i] - L[i], abs(H[i] - C[i - 1]), abs(L[i] - C[i - 1])) for i in range(1, n)]
    vu = _ema(tr, p["vu_len"]); e50 = _ema(C, 50)
    ch = [0.0] + [C[i] - C[i - 1] for i in range(1, n)]
    up, dn = _ema([max(x, 0) for x in ch], 27), _ema([max(-x, 0) for x in ch], 27)
    rsi = [100 - 100 / (1 + a / max(b, 1e-12)) for a, b in zip(up, dn)]
    cs = [0.0]
    for x in V: cs.append(cs[-1] + x)
    vavg = [V[0]] + [(cs[i] - cs[max(0, i - 100)]) / (i - max(0, i - 100)) for i in range(1, n)]
    zs, zt = _ZZ(p["swing_k"], H, L, C), _ZZ(p["trend_k"], H, L, C)
    lg, sh = _Setup(1, p), _Setup(-1, p)                                   # all strong breaks
    lgv, shv = _Setup(1, p, need_vol=True), _Setup(-1, p, need_vol=True)   # volume-confirmed breaks only
    warn = dict(n=0, items=[], bar=None)
    for i in range(p["warmup"], n):
        u = vu[i]
        zs.update(i, u); zt.update(i, u)
        for st in (lg, sh, lgv, shv): st.step(i, O, H, L, C, V, vavg, zs, u)
        if zs.isNew and zt.dir != 0 and zs.k1 == zt.dir and zs.b[1] is not None and zt.b[0] is not None:
            d = zt.dir; hb = zs.b[0]; lb = zs.b[1]
            push = d * (zs.p[0] - zs.p[1])
            items = []
            if i - hb <= 2: items.append("fast drop" if d == 1 else "fast bounce")
            if (rsi[hb] >= 80) if d == 1 else (rsi[hb] <= 20): items.append("RSI 80+" if d == 1 else "RSI 20-")
            a0 = max(hb - 2, 0)
            if vavg[a0] > 0 and max(V[a0:hb + 1]) / vavg[a0] > 4: items.append("volume spike 4x")
            if hb > lb and vavg[lb] > 0 and (sum(V[lb + 1:hb + 1]) / (hb - lb)) / vavg[lb] > 2.5: items.append("push volume 2.5x")
            prev_in = zs.b[2] is not None and zs.b[2] > zt.b[0]
            if prev_in and zs.b[3] is not None and zs.b[3] >= zt.b[0] and push > 1.6 * d * (zs.p[2] - zs.p[3]):
                items.append("push 1.6x bigger")
            if vu[hb] > 0 and d * (zs.p[0] - e50[hb]) / vu[hb] > 6: items.append("6 VU from EMA50")
            if not prev_in: items.append("1st pullback")
            warn = dict(n=len(items), items=items, bar=i)

    last = n - 1; u = vu[last]
    out["trend"] = [dict(bar=b, price=pr, kind=k) for b, pr, k, cf in zt.piv]
    out["trend_live"] = dict(bar=zt.extBar, price=zt.ext) if zt.dir != 0 and zt.extBar is not None else None
    out["swings"] = [dict(bar=b, price=pr, kind=k) for b, pr, k, cf in zs.piv]
    for s in (lg, sh):
        for t in s.trades:
            (out["trades"] if "result" in t else out["open_trades"]).append(t)
        z = s.zone(u)
        if z: out["setups"].append(z)
        out["missed"] += s.missed                                         # strong setups that never filled
    out["missed"].sort(key=lambda m: m["arm_bar"])
    for s in (lgv, shv):
        for t in s.trades:
            (out["trades_v"] if "result" in t else out["open_trades_v"]).append(t)
    out["trades"].sort(key=lambda t: t["fill_bar"]); out["trades_v"].sort(key=lambda t: t["fill_bar"])
    odds = ODDS.get(str(interval)); od = odds or ODDS["15"]
    panel = dict(tested=odds is not None, vu=u, min_break_price=p["min_break"] * u, warnings=warn)
    if zt.dir != 0 and zt.b[0] is not None:
        panel["trend"] = dict(dir="UP" if zt.dir == 1 else "DOWN", start=zt.p[0], extreme=zt.ext,
                              pct=(zt.ext / zt.p[0] - 1) * 100 if zt.p[0] else 0.0)
        if zs.p[1] == zs.p[1] and zs.dir == -zt.dir:
            top, bot = zs.p[0], zs.p[1]; push = abs(top - bot)
            depth = abs(top - zs.ext) / push if push > 0 else NAN
            d = zt.dir
            uc = vu[zs.piv[-1][3]] if zs.piv else u                 # VU when the top was confirmed (as in the research)
            depth_vu = abs(top - zs.ext) / uc if uc > 0 else NAN; push_vu = push / uc if uc > 0 else NAN
            ov = ODDS_VU.get(str(interval), ODDS_VU["15"])
            col = 0 if push_vu < 5 else 1 if push_vu < 8 else 2 if push_vu < 12 else 3
            cont = ov["base"][col]
            for (lv, _), row in zip(LADDER.get(str(interval), LADDER["15"]), ov["table"]):
                if depth_vu >= lv: cont = row[col]
            panel["pullback"] = dict(depth=depth, depth_vu=depth_vu, push_vu=push_vu, continue_pct=cont, base_pct=ov["base"][col],
                                     t618=top + d * 0.618 * push, t618_pct=od["t618"],
                                     t100=top + d * push, t100_pct=od["t100"])
            lad = LADDER.get(str(interval), LADDER["15"])
            over = zt.ext - d * p["trend_k"] * u                  # the big trend flips on a close beyond this (today's VU)
            before = lambda x: d * (x - over) > 0                    # a level only matters if it comes before the flip
            ladder = [dict(vu=m, price=top - d * m * uc, ended_pct=e, cont_pct=row[col]) for (m, e), row in zip(lad, ov["table"])]
            panel["pmap"] = dict(top=top, depth_vu=depth_vu, ladder=[x for x in ladder if before(x["price"])],
                                 cut_levels=sum(1 for x in ladder if not before(x["price"])),
                                 push_start=bot if before(bot) else None, push_start_cont=PUSH_START_CONT.get(str(interval), PUSH_START_CONT["15"])[col],
                                 trend_over=over)
        else:
            panel["pmap"] = dict(trend_over=zt.ext - zt.dir * p["trend_k"] * u)   # push in progress: only the trend-over line
    for key, done in (("stats", out["trades"]), ("stats_v", out["trades_v"])):
        if done:
            Rs = [t["R"] for t in done]
            panel[key] = dict(n=len(Rs), wins=sum(1 for r in Rs if r > 0), avg_r=sum(Rs) / len(Rs), total_r=sum(Rs))
    out["panel"] = panel
    return out


def fetch_candles(symbol, interval="15", bars=1000, timeout=6):
    """Bybit linear klines, ascending, paginated (1000 per request). [] on failure; never raises."""
    rows = {}; end = None
    try:
        for _ in range(max(1, (bars + 999) // 1000)):
            q = dict(category="linear", symbol=symbol, interval=interval, limit=1000)
            if end: q["end"] = end
            req = urllib.request.Request("https://api.bybit.com/v5/market/kline?" + urllib.parse.urlencode(q),
                                         headers={"User-Agent": "MASIS/3.0"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                lst = (json.loads(r.read().decode()).get("result") or {}).get("list") or []
            if not lst: break
            for x in lst:
                rows[int(x[0])] = dict(start=int(x[0]), open=float(x[1]), high=float(x[2]), low=float(x[3]),
                                       close=float(x[4]), volume=float(x[5]))
            end = min(int(x[0]) for x in lst) - 1
            if len(rows) >= bars: break
    except Exception:
        pass
    out = [rows[k] for k in sorted(rows)]
    return out[-bars:]


def _clean(x):
    """NaN / inf -> None, so the JSON stays parseable by the browser."""
    if isinstance(x, float):
        return None if x != x or x in (float("inf"), float("-inf")) else x
    if isinstance(x, dict):
        return {k: _clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_clean(v) for v in x]
    return x


_cache = {}
_lock = threading.Lock()


def get(symbol, interval="15", bars=1000, ttl=30):
    """Cached fetch + compute for the dashboard. The newest (still forming) candle is shown but not used."""
    key = (symbol, interval, bars); now = time.time()
    with _lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < ttl: return hit[1]
    candles = fetch_candles(symbol, interval, bars + 1)
    if not candles:
        return dict(ok=False, error="no candles from Bybit", symbol=symbol, interval=interval)
    step = TF_MS.get(str(interval), 900_000)
    closed = candles if candles[-1]["start"] + step <= now * 1000 else candles[:-1]
    res = compute(closed, interval)
    res.update(ok=True, symbol=symbol, interval=interval, candles=candles,
               times=[c["start"] // 1000 for c in closed], generated=int(now))
    res = _clean(res)
    with _lock:
        _cache[key] = (now, res)
        if len(_cache) > 64:
            for k in sorted(_cache, key=lambda k: _cache[k][0])[:16]: _cache.pop(k, None)
    return res


_scan_cache = {}
_scan_lock = threading.Lock()


def _brief(r):
    """The parts of a get() result the radar and its history use (the candles are not kept)."""
    if not r or not r.get("ok"): return None
    times = r.get("times") or []
    return dict(price=r["candles"][-1]["close"], times=times, closes=[c["close"] for c in r["candles"][:len(times)]], panel=r.get("panel") or {},
                setups=r.get("setups") or [], open_trades=r.get("open_trades") or [], trades=r.get("trades") or [],
                missed=r.get("missed") or [], warmup=(r.get("params") or P)["warmup"])


def _scan(intervals, coins, ttl):
    """get() for every coin x interval: one scan at a time, cached for ttl seconds, shared by the radar and the radar
    history. Returns (generated, [((symbol, interval), brief or None), ...])."""
    from concurrent.futures import ThreadPoolExecutor
    key = (tuple(intervals), tuple(coins))
    with _scan_lock:
        hit = _scan_cache.get(key)
        if hit and time.time() - hit[0] < ttl: return hit
        jobs = [(s, iv) for s in coins for iv in intervals]
        with ThreadPoolExecutor(16) as ex:                      # the time is the Bybit downloads, not the maths
            results = list(ex.map(lambda j: _brief(get(j[0], j[1], 1000)), jobs))
        hit = _scan_cache[key] = (int(time.time()), list(zip(jobs, results)))
        return hit


def radar(intervals=("15", "60"), coins=None, ttl=150):
    """Every strong-break setup (waiting) and open SBGZ trade across the coins and intervals, for the dashboard radar.
    Reuses get() (and its cache), so the chart shows the same numbers."""
    generated, results = _scan(intervals, list(coins or RADAR_COINS), ttl)
    rows = []; errors = []
    for (sym, iv), r in results:
        if not r:
            errors.append(sym + " " + iv); continue
        pn = r["panel"]; times = r["times"]; price = r["price"]; vu_now = pn.get("vu") or 0
        base = dict(symbol=sym, interval=iv, price=price, trend=pn.get("trend"), warnings=(pn.get("warnings") or {}).get("n", 0))
        for z in r["setups"]:
            if not z.get("strong"): continue
            sd = 1 if z["side"] == "LONG" else -1
            dist = sd * (price - z["entry"])
            rows.append(dict(base, kind="setup", side=z["side"], entry=z["entry"], zone_top=z["zone_top"], zone_bottom=z["zone_bottom"],
                             stop=z["stop"], target=z["target"], rr=abs(z["target"] - z["entry"]) / abs(z["entry"] - z["stop"]),
                             break_vu=z["break_vu"], bvol=z.get("bvol"), vol_ok=bool(z.get("vol_ok")),
                             dist_pct=dist / price * 100 if price else None, dist_vu=dist / vu_now if vu_now else None,
                             since=times[z["since_bar"]] if z.get("since_bar") is not None and z["since_bar"] < len(times) else None,
                             exp_r=EXP_R.get((iv, bool(z.get("vol_ok")))) ))
        for t in r["open_trades"]:
            rows.append(dict(base, kind="open", side=t["side"], entry=t["entry"], stop=t["stop"], target=t["target"], rr=t["rr"],
                             break_vu=t["break_vu"], bvol=t.get("bvol"), vol_ok=bool(t.get("vol_ok")),
                             since=times[t["fill_bar"]] if t["fill_bar"] < len(times) else None,
                             exp_r=EXP_R.get((iv, bool(t.get("vol_ok")))) ))
    return _clean(dict(ok=True, generated=generated, scanned=len(results), rows=rows, errors=errors))


# ── Radar history: every setup the radar showed and what happened to it ──────────────────────────────────────────────
# WIN = take-profit before stop, LOSS = stop before take-profit, OPEN = filled and running, NO FILL = the setup ended
# before the price came back to the entry (a resting limit order there would just have been cancelled). Worked out
# from the radar's own candles and rules, and kept in the shared store (Supabase app_state, like the runner's state;
# SBGZ_HISTORY_BACKEND=file keeps it in a local file instead), so the list keeps growing after the candles leave the
# 1000-bar window. Only setups armed >= 100 bars after the warm-up are added: by then the swings behind them are the
# same in every window, so the result is the one the radar showed live.
HIST_KEY = "sbgz_radar_history"
HIST_MAX = 1200
HIST_SETTLE = 100
MISSED_WHY = {"swing": "a new swing formed before the price came back to the entry",
              "busy": "the price came back while the last trade on this side was still open",
              "weak": "the break was under 13 VU when the price came back",
              "vol": "break volume under 2x (confirmed-only mode)"}
_hist_lock = threading.Lock()
_hist_mem = None                       # rows once loaded from the store (this process writes all changes)


def _hist_file():
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scratch", HIST_KEY + ".json")


def _hist_file_backend():
    return os.environ.get("SBGZ_HISTORY_BACKEND", "").strip().lower() == "file"


def _hist_load():
    """(rows, ok): ok is False when the store could not be read, so nothing is written over it."""
    if _hist_file_backend():
        try:
            with open(_hist_file(), "r", encoding="utf-8") as f:
                return json.load(f), True
        except FileNotFoundError:
            return [], True
        except Exception:
            return [], False
    from .supabase_client import supabase_get
    got = supabase_get("app_state", {"key": f"eq.{HIST_KEY}", "select": "value"})
    if got is None: return [], False
    v = got[0].get("value") if got else []
    return (v if isinstance(v, list) else []), True


def _hist_save(rows):
    if _hist_file_backend():
        os.makedirs(os.path.dirname(_hist_file()), exist_ok=True)
        with open(_hist_file() + ".tmp", "w", encoding="utf-8") as f:
            json.dump(rows, f)
        os.replace(_hist_file() + ".tmp", _hist_file())
        return True
    from .supabase_client import supabase_kv_set
    return supabase_kv_set(HIST_KEY, rows) is not None


def _num(x, sig=8):
    return float(f"{x:.{sig}g}") if isinstance(x, (int, float)) and x == x and abs(x) != float("inf") else None


def _hist_rows(sym, iv, r, settle=True):
    """History rows of one chart. settle: only setups armed >= HIST_SETTLE bars after the warm-up."""
    times = r["times"]; first = r["warmup"] + (HIST_SETTLE if settle else 0)
    t = lambda b: times[b] if b is not None and 0 <= b < len(times) else None
    out = []
    for tr in r["trades"] + r["open_trades"]:
        if tr.get("since_bar", tr["fill_bar"]) < first: continue
        st = "OPEN" if "result" not in tr else "WIN" if tr["result"] == "TP" else "LOSS"
        out.append(dict(k=f"{sym}|{iv}|{tr['side']}|{t(tr['fill_bar'])}", symbol=sym, interval=iv, side=tr["side"], status=st,
                        t_arm=t(tr.get("since_bar")), t_fill=t(tr["fill_bar"]), t_end=t(tr.get("exit_bar")),
                        entry=_num(tr["entry"]), stop=_num(tr["stop"]), target=_num(tr["target"]), exit=_num(tr.get("exit")),
                        R=_num(tr.get("R"), 4), rr=_num(tr["rr"], 3), break_vu=_num(tr["break_vu"], 3), bvol=_num(tr.get("bvol"), 3),
                        vol_ok=bool(tr.get("vol_ok"))))
    for m in r["missed"]:
        if m["arm_bar"] < first: continue
        out.append(dict(k=f"{sym}|{iv}|{m['side']}|a{t(m['arm_bar'])}", symbol=sym, interval=iv, side=m["side"], status="NO FILL",
                        t_arm=t(m["arm_bar"]), t_fill=None, t_end=t(m["end_bar"]), entry=_num(m["entry"]), stop=_num(m["stop"]),
                        target=_num(m["target"]), exit=None, R=None, rr=_num(m["rr"], 3), break_vu=_num(m["break_vu"], 3),
                        bvol=_num(m.get("bvol"), 3), vol_ok=bool(m.get("vol_ok")), why=m["why"]))
    return out


def _hist_stats(rows):
    def agg(rs):
        closed = [x for x in rs if x["status"] in ("WIN", "LOSS")]
        wins = sum(1 for x in closed if x["status"] == "WIN"); Rs = [x["R"] for x in closed if x.get("R") is not None]
        return dict(signals=len(rs), filled=sum(1 for x in rs if x["status"] != "NO FILL"), wins=wins, losses=len(closed) - wins,
                    open=sum(1 for x in rs if x["status"] == "OPEN"), no_fill=sum(1 for x in rs if x["status"] == "NO FILL"),
                    win_rate=wins / len(closed) * 100 if closed else None, total_r=sum(Rs), avg_r=sum(Rs) / len(Rs) if Rs else None)
    out = {}
    for mode, sel in (("all", rows), ("vol", [x for x in rows if x.get("vol_ok")])):
        out[mode] = dict(total=agg(sel), **{iv: agg([x for x in sel if x["interval"] == iv]) for iv in ("15", "60")})
    return out


BX_R = 0.5                             # the BTC rule: close the trade once BTC has moved this many risk-units against it


def btc_exit(row, times, closes, btc_close, step):
    """The BTC rule on one filled history row: the trade is closed at the market, at the close of the first candle in which BTC's close has
    moved BX_R risk-units (the trade's risk in percent) against it since the candle before the fill. The candle that ends the trade by itself is
    not eligible (its stop / target came first). times / closes: this coin's closed candles (start in seconds, ascending); btc_close: BTC's
    close by candle start; step: candle length in seconds. Returns dict(fired=True, t=close time of that candle, px, R after fees like the
    other rows), dict(fired=False) when it would not have fired (or for BTC itself), None when it cannot be judged (candles missing)."""
    if row["symbol"] == "BTCUSDT": return dict(fired=False)
    t0, E, S = row.get("t_fill"), row.get("entry"), row.get("stop")
    if not t0 or not E or E == S: return None
    b0 = btc_close.get(t0 - step); j = bisect.bisect_left(times, t0)
    if not b0 or j >= len(times) or times[j] != t0: return None
    sd = 1 if row["side"] == "LONG" else -1; risk = abs(E - S); scale = risk / abs(E); end = row.get("t_end")
    for k in range(j, len(times)):
        if end is not None and times[k] >= end: break
        b = btc_close.get(times[k])
        if b is not None and sd * (b / b0 - 1) / scale <= -BX_R:
            fee = P["fee_limit"] + P["fee_stop"]
            return dict(fired=True, t=times[k] + step, px=_num(closes[k]), R=_num(sd * (closes[k] - E) / risk - fee * abs(E) / risk, 4))
    return dict(fired=False)


_bx_tried = {}                         # row key -> when its BTC result was last attempted (rows that cannot be judged are not retried every call)


def _bx_backfill(rows, budget=15.0):
    """BTC-rule results for stored filled rows that have none yet (rows from before the rule existed, or older than the scan window): one
    longer candle download per coin and interval and one for BTC, extended until it reaches back past the oldest fill (at most 3000 bars =
    31 days of 15m). No new download starts after `budget` seconds; the rest follows on the next call. Returns {row key: bx}."""
    from concurrent.futures import ThreadPoolExecutor
    t_stop = time.time() + budget; by = {}
    for x in rows: by.setdefault((x["symbol"], x["interval"]), []).append(x)
    ivs = sorted({iv for _, iv in by}); need = {}; first = {}
    for (sym, iv), xs in by.items():
        step = TF_MS.get(iv, 900_000) // 1000
        need[(sym, iv)] = min(x["t_fill"] for x in xs) - 2 * step                      # the candle before the fill is needed too
    for iv in ivs: need[("BTCUSDT", iv)] = min(n for (_, i2), n in need.items() if i2 == iv)
    jobs = [("BTCUSDT", iv) for iv in ivs] + [k for k in by if k[0] != "BTCUSDT"]
    def dl(key):
        n = 300
        while time.time() < t_stop:
            cs = fetch_candles(key[0], key[1], n)
            if not cs or cs[0]["start"] // 1000 <= need[key] or n >= 3000 or len(cs) < n: return key, cs
            n = min(3000, n * 2)
        return key, None
    with ThreadPoolExecutor(8) as ex: got = dict(ex.map(dl, jobs))
    out = {}
    for iv in ivs:
        step = TF_MS.get(iv, 900_000) // 1000
        def closed(cs): return cs[:-1]                                         # the newest candle is the one still forming
        bc = got.get(("BTCUSDT", iv))
        if not bc: continue
        btc = {c["start"] // 1000: c["close"] for c in closed(bc)}
        for (sym, i2), xs in by.items():
            if i2 != iv: continue
            cs = got.get((sym, iv))
            if sym != "BTCUSDT" and not cs: continue
            cl = closed(cs or []); times = [c["start"] // 1000 for c in cl]; closes = [c["close"] for c in cl]
            for x in xs:
                bx = btc_exit(x, times, closes, btc, step)
                if bx is not None: out[x["k"]] = bx
    return out


def _eff(x):
    """(status, R) of a history row under the BTC rule: a filled row the rule closed is 'EXIT' (R after fees); everything else as planned."""
    b = x.get("bx")
    if x["status"] != "NO FILL" and b and b.get("fired"): return "EXIT", b.get("R")
    return x["status"], x.get("R")


def _hist_stats_bx(rows):
    """Like _hist_stats, but every trade the BTC rule would have closed counts as an EXIT with its own R (still-open trades the rule has
    already closed too). pending = filled rows the rule has not been worked out for yet (counted as planned)."""
    def agg(rs):
        e = [(x,) + _eff(x) for x in rs]
        closed = [t for t in e if t[1] in ("WIN", "LOSS", "EXIT")]; Rs = [t[2] for t in closed if t[2] is not None]
        ex = [t for t in closed if t[1] == "EXIT"]
        return dict(signals=len(rs), filled=sum(1 for t in e if t[1] != "NO FILL"), wins=sum(1 for t in closed if t[1] == "WIN"),
                    losses=sum(1 for t in closed if t[1] == "LOSS"), exits=len(ex), exits_up=sum(1 for t in ex if (t[2] or 0) > 0),
                    open=sum(1 for t in e if t[1] == "OPEN"), no_fill=sum(1 for t in e if t[1] == "NO FILL"), full_losses=sum(1 for v in Rs if v <= -0.9),
                    pending=sum(1 for x in rs if x["status"] != "NO FILL" and "bx" not in x),
                    total_r=sum(Rs), avg_r=sum(Rs) / len(Rs) if Rs else None)
    out = {}
    for mode, sel in (("all", rows), ("vol", [x for x in rows if x.get("vol_ok")])):
        out[mode] = dict(total=agg(sel), **{iv: agg([x for x in sel if x["interval"] == iv]) for iv in ("15", "60")})
    return out


def history(intervals=("15", "60"), coins=None, ttl=150, vonly=False, limit=400):
    """The radar history: new rows from the current scan are added to the stored list (an OPEN row is updated when its
    trade closes, or when the BTC rule closes it; a closed or NO FILL row never changes). Every filled row also carries bx, what the
    BTC rule would have done with it (btc_exit); stored rows without one are worked out in a longer download (_bx_backfill).
    Rows newest signal first; stats (as planned) and stats_bx (with the BTC rule) over every stored row."""
    global _hist_mem
    generated, results = _scan(intervals, list(coins or RADAR_COINS), ttl)
    btc = {iv: dict(zip(r["times"], r["closes"])) for (sym, iv), r in results if sym == "BTCUSDT" and r}   # BTC closes by candle start
    with _hist_lock:
        if _hist_mem is None:
            rows, ok = _hist_load()
            if not ok: return dict(ok=False, error="the radar history store could not be read; try again in a minute")
            _hist_mem = {x["k"]: x for x in rows if isinstance(x, dict) and x.get("k")}
        changed = False
        for (sym, iv), r in results:
            if not r: continue
            settled = {x["k"] for x in _hist_rows(sym, iv, r)}
            for row in _hist_rows(sym, iv, r, settle=False):
                if row["status"] != "NO FILL" and iv in btc:
                    bx = btc_exit(row, r["times"], r["closes"], btc[iv], TF_MS[iv] // 1000)
                    if bx is not None: row["bx"] = bx
                old = _hist_mem.get(row["k"])
                if (old is None and row["k"] in settled) or (old is not None and old["status"] == "OPEN" and (row["status"] != "OPEN" or row.get("bx") != old.get("bx"))):
                    _hist_mem[row["k"]] = row; changed = True
        tnow = time.time()
        todo = [dict(x) for x in _hist_mem.values() if x["status"] != "NO FILL" and x.get("t_fill") and "bx" not in x
                and tnow - _bx_tried.get(x["k"], 0) > 1800] if btc else []
    if todo:                                                            # stored rows the BTC rule has not been worked out for (downloads: outside the lock)
        got = _bx_backfill(todo)
        for x in todo: _bx_tried[x["k"]] = time.time()                   # rows still without a result wait 30 min before the next try
        with _hist_lock:
            for k, bx in got.items():
                if k in _hist_mem and "bx" not in _hist_mem[k]: _hist_mem[k] = dict(_hist_mem[k], bx=bx); changed = True
    with _hist_lock:
        if changed:
            keep = sorted(_hist_mem.values(), key=lambda x: x.get("t_arm") or x.get("t_fill") or 0, reverse=True)[:HIST_MAX]
            if _hist_save(keep): _hist_mem = {x["k"]: x for x in keep}
            else: print("[sbgz history] could not save to the store (kept in memory, retried on the next change)")
        rows = sorted(_hist_mem.values(), key=lambda x: x.get("t_arm") or x.get("t_fill") or 0, reverse=True)
    shown = [x for x in rows if x.get("vol_ok")] if vonly else rows
    stamps = [x.get("t_arm") or x.get("t_fill") for x in rows if x.get("t_arm") or x.get("t_fill")]
    return _clean(dict(ok=True, generated=generated, since=min(stamps) if stamps else None, total=len(shown),
                       rows=[dict(x, why_text=MISSED_WHY.get(x.get("why"))) if x.get("why") else x for x in shown[:limit]],
                       stats=_hist_stats(rows), stats_bx=_hist_stats_bx(rows), bx_rule=dict(move_r=BX_R)))
