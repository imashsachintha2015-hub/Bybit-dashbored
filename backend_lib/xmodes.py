"""CME-X5 (Model B) and Championship Dual-Regime as chart indicators (served by /api/ind/cmex5 and /api/ind/champ).

Nothing is re-implemented here: the signals come from the engines' own code, called on the same windows the engine uses
  CME-X5   daemons/cme_x5_pure_engine.py  detect_s2 / detect_s7 on the last 100 15m candles, then the S/R, POC and FVG
           agents and the ConfluenceScorer (daemons/agents) with 60 one-hour and 50 four-hour candles; REJECT = no trade,
           PASS / ADJUST = trade (the agents may pull the target in). AMD-FVG is research-only in the engine: left out.
  Champion backend_lib/championship_engine.py  scan_candidate on the last 250 candles of the coin and of BTCUSDT
           (BTC 15m close vs its 200 EMA picks the regime: BULL = 21 EMA springboard, BEAR = four exhaustion setups)
so a signal on the chart is a signal the engine would have taken. Each candle is evaluated at its close (the engine polls
every 10 s on the forming candle, so it can also fire intrabar); the forming candle is evaluated too and flagged LIVE.

History trades replay what the engine's position manager does (research_archive/historical_replay/replay.py, `sim`):
market entry at the signal candle's close, stop, target (CME-X5: after the agents' adjustment; Championship: TP2, the
engine does not take partials at TP1), stop to break-even +0.10% at +0.70R (or +0.8%), stop to +0.5R at +1.20R (or
+1.5%), 12 h (48 candle) timeout, 14 bps friction; one open trade per coin; a candle that touches both stop and target
counts as a stop. Not replayed: the engine's order-book spread gate and slippage.
Standard library only (the deployment has no numpy).
"""
import threading
import time

from . import sbgz

MODES = ("cmex5", "champ")
FRIC = 0.0014                     # engine FRICTION_PCT: 14 bps round trip
TIMEOUT_BARS = 48                 # 12 h of 15m candles
LEAD = 820                        # candles before the window: 50 four-hour candles of context for the S/R agent
MAX_BARS = 2000
WIN_X5, WIN_CH = 100, 250         # candles the engine hands its detectors
H1_MS, H4_MS, M15_MS = 3_600_000, 14_400_000, 900_000
NAN = float("nan")

# what replaying the engines' own code over Jan 2023 - Sep 2026 found (Binance 15m, 43 coins, this module's pipeline, 14 bps
# fees; scratch/research_xmodes_long.py). The earlier 120-day replay (research_archive/historical_replay) agrees:
# Model B -0.17R (n=1,663; -0.25R with the agents, n=41), Championship -0.12R (n=381).
REFERENCE = {
    "cmex5": "Replay of this engine's own code on 43 coins, Jan 2023 - Sep 2026: its agents passed 836 of 127,179 setups (0.7%). "
             "Those trades averaged -0.19R after fees (35% win; negative in all 3 periods, t -6); the rejected setups "
             "-0.13R. Before fees both are about 0R: the 14 bps trading cost is the whole loss. The engine runs record-only.",
    "champ": "Replay of this engine's own code on 41 coins, Jan 2023 - Sep 2026: 10,570 trades, 32% win, -0.17R per trade "
             "after fees (bull setups -0.19R, bear -0.09R; negative in every year, t -11). Before fees about 0R: the 14 bps "
             "trading cost is the whole loss. The engine runs record-only.",
}
_eng = None
_eng_err = None


def _engine():
    """The engines' code, imported once. (cme_x5_pure_engine only reads its settings at import: no network, no thread.)"""
    global _eng, _eng_err
    if _eng is not None: return _eng
    if _eng_err: raise RuntimeError(_eng_err)
    try:
        import importlib
        X = importlib.import_module("daemons.cme_x5_pure_engine")
        from daemons.agents import SRAgent, POCPathfinderAgent, FVGImpactAgent, ConfluenceScorer
        from backend_lib import championship_engine as CE
        _eng = dict(X=X, CE=CE, sr=SRAgent(), poc=POCPathfinderAgent(), fvg=FVGImpactAgent(), scorer=ConfluenceScorer(),
                    champ=CE.ChampionshipDualRegimeEngine())
    except Exception as e:
        _eng_err = f"the engine code could not be loaded: {type(e).__name__}: {e}"
        raise RuntimeError(_eng_err)
    return _eng


def _isn(x):
    return x != x


def _p(x):
    """Price text, like the page's sbgzFmt."""
    if x is None or _isn(x): return "--"
    a = abs(x)
    return f"{x:,.1f}" if a >= 1000 else f"{x:.2f}" if a >= 10 else f"{x:.3f}" if a >= 1 else f"{x:.4g}"


def _r(x):
    return "--" if x is None or _isn(x) else f"{x:+.2f}R"


def _x(x, nd=2):
    return "--" if x is None or _isn(x) else f"{x:.{nd}f}"


# ── executor replay ──────────────────────────────────────────────────────────────────────────────────────────────────
def simulate(bars, i, side, stop, tp, fric=FRIC):
    """What the engine's position manager does with a market entry at the close of bars[i]. Returns
    dict(kind, exit_i, exit, R, bars) with kind TP / TRAIL (stopped at +0.5R) / BE (stopped at break-even) / SL / TIMEOUT,
    dict(kind='OPEN', R_now) while the 48 candles have not run out, dict(kind='INVALID') for a stop or target on the wrong side."""
    d = 1 if side == "LONG" else -1
    ep = bars[i]["close"]; rp = abs(ep - stop) / ep if ep else 0.0
    if rp <= 0 or d * (ep - stop) <= 0 or d * (tp - ep) <= 0: return dict(kind="INVALID")
    fr = fric / rp; cur = stop; be = tr = False; j0 = i + 1
    done = lambda kind, x, j: dict(kind=kind, exit_i=j, exit=x, R=d * (x - ep) / (rp * ep) - fr, bars=j - j0 + 1)
    for j in range(j0, min(len(bars), j0 + TIMEOUT_BARS)):
        b = bars[j]; hi, lo, op = b["high"], b["low"], b["open"]
        if (lo <= cur) if d == 1 else (hi >= cur):                       # stop first when a candle touches both
            return done("TRAIL" if tr else "BE" if be else "SL", min(op, cur) if d == 1 else max(op, cur), j)
        if (hi >= tp) if d == 1 else (lo <= tp):
            return done("TP", tp, j)
        fav = (hi - ep) / ep if d == 1 else (ep - lo) / ep               # favourable extreme of this candle
        rm = fav / rp
        if (rm >= 0.70 or fav >= 0.0080) and not be:                      # protection applies from the next candle
            bp = ep * 1.0010 if d == 1 else ep * 0.9990
            if (d == 1 and bp > cur) or (d == -1 and bp < cur): cur, be = bp, True
        if (rm >= 1.20 or fav >= 0.0150) and not tr:
            tp5 = ep + d * rp * ep * 0.5
            if (d == 1 and tp5 > cur) or (d == -1 and tp5 < cur): cur, tr = tp5, True
    if j0 + TIMEOUT_BARS <= len(bars):
        return done("TIMEOUT", bars[j0 + TIMEOUT_BARS - 1]["close"], j0 + TIMEOUT_BARS - 1)
    return dict(kind="OPEN", R_now=d * (bars[-1]["close"] - ep) / (rp * ep))


# ── higher-timeframe candles as the engine sees them ────────────────────────────────────────────────────────────────
def _buckets(cs, ms):
    """([(bucket start, first candle, last candle)], bucket index of every candle) for ms-wide buckets of 15m candles."""
    bk, idx = [], []
    for i, c in enumerate(cs):
        s = c["start"] // ms * ms
        if bk and bk[-1][0] == s: bk[-1][2] = i
        else: bk.append([s, i, i])
        idx.append(len(bk) - 1)
    return bk, idx


def _htf(cs, bk, idx, i, n):
    """The last n candles of a higher timeframe at 15m candle i: the finished ones and the one still forming (through i)."""
    b = idx[i]; out = []
    for s, f, l in bk[max(0, b - n + 1):b + 1]:
        seg = cs[f:(l if s != bk[b][0] else i) + 1]
        out.append(dict(start=s, open=seg[0]["open"], high=max(x["high"] for x in seg), low=min(x["low"] for x in seg),
                        close=seg[-1]["close"], volume=sum(x["volume"] for x in seg)))
    return out


def _verdict(E, sym, sig, w, k1h, k4h):
    """The engine's agent layer (cme_x5_pure_engine._scan): three agents, a neutral 0.7 when one fails, then the scorer."""
    X = E["X"]
    vp = X.volume_profile(w[-X.VP_LOOKBACK:]) if len(w) >= X.VP_LOOKBACK else None
    res = {}
    for key, fn in (("sr", lambda: E["sr"].analyze(sym, sig, k1h, k4h, w)), ("poc", lambda: E["poc"].analyze(sym, sig, vp)),
                    ("fvg", lambda: E["fvg"].analyze(sym, sig, w))):
        try: res[key] = fn()
        except Exception as e: res[key] = {"score": 0.7, "adjustment": None, "reason": f"{key} agent error: {e}"}
    v = E["scorer"].evaluate(res)
    agents = [dict(name=nm, score=res[k].get("score"), reason=str(res[k].get("reason") or "")[:200])
              for k, nm in (("sr", "S/R"), ("poc", "POC"), ("fvg", "FVG"))]
    return v, agents


def _stat(rows):
    """Count / wins / average R / total R of simulated trades (a win = R above zero after fees) and the exit kinds."""
    rs = [x["R"] for x in rows]
    wins = sum(1 for r in rs if r > 0)
    kinds = {}
    for x in rows: kinds[x["status"]] = kinds.get(x["status"], 0) + 1
    return dict(n=len(rs), wins=wins, win_rate=wins / len(rs) * 100 if rs else None, avg_r=sum(rs) / len(rs) if rs else None,
                total_r=sum(rs), kinds=kinds)


def _finish(sg, res, closed):
    """Copy a simulation result onto a signal dict (times in seconds)."""
    sg["status"] = res["kind"]
    if res["kind"] == "OPEN": sg["R_now"] = res["R_now"]
    elif res["kind"] != "INVALID":
        sg.update(R=res["R"], exit=res["exit"], t_exit=closed[res["exit_i"]]["start"] // 1000, bars=res["bars"])


# ── CME-X5 ───────────────────────────────────────────────────────────────────────────────────────────────────────────
def _run_cmex5(E, sym, cs, d0, n_closed):
    X = E["X"]; n = len(cs); closed = cs[:n_closed]
    bk1, ix1 = _buckets(cs, H1_MS); bk4, ix4 = _buckets(cs, H4_MS)
    raw = []
    for i in range(max(d0, WIN_X5 - 1), n):
        w = cs[i - WIN_X5 + 1:i + 1]
        s2, s7 = X.detect_s2(sym, w, None), X.detect_s7(sym, w, None)
        sig = s2 or s7
        if sig: raw.append((i, sig, "S2" if s2 else "S7", w))
    out, skipped, busy = [], 0, -1
    for i, sig, kind, w in raw:
        sig = dict(sig)
        v, agents = _verdict(E, sym, sig, w, _htf(cs, bk1, ix1, i, 60), _htf(cs, bk4, ix4, i, 50))
        if v["adjustments"] and "target_p" in v["adjustments"]: sig["target_p"] = v["adjustments"]["target_p"]
        sg = dict(t=cs[i]["start"] // 1000, side=sig["direction"], kind=kind, label=sig["situation"], entry=sig["entry_p"],
                  stop=sig["stop_p"], target=sig["target_p"], risk_pct=sig["risk_pct"], verdict=v["decision"], score=v["final_score"],
                  agents=agents, live=i >= n_closed)
        if v["decision"] == "REJECT":                              # the engine never trades it: replayed only as a what-if
            sg["status"] = "REJECTED"
            if not sg["live"]:
                r = simulate(closed, i, sg["side"], sg["stop"], sg["target"])
                if r["kind"] not in ("OPEN", "INVALID"): sg.update(what_if=r["kind"], what_if_R=r["R"])
        elif sg["live"]: sg["status"] = "LIVE"
        elif i <= busy: skipped += 1; continue                    # one position per coin: the engine skips it
        else:
            r = simulate(closed, i, sg["side"], sg["stop"], sg["target"])
            if r["kind"] == "INVALID": continue
            _finish(sg, r, closed)
            busy = 10 ** 9 if r["kind"] == "OPEN" else r["exit_i"]
        out.append(sg)
    return out, skipped


def _state_cmex5(E, cs):
    """The readings the two setups wait on, on the newest candle."""
    X = E["X"]; w = cs[-WIN_X5:]; closes = [b["close"] for b in w]; last = w[-1]
    vp = X.volume_profile(w[-X.VP_LOOKBACK:]); avg5 = sum(b["volume"] for b in w[-6:-1]) / 5.0
    e21, e50 = X.ema_last(closes, 21), X.ema_last(closes, 50)
    return dict(poc=vp["poc"] if vp else None, val=vp["val"] if vp else None, vah=vp["vah"] if vp else None,
                vol_x=last["volume"] / avg5 if avg5 > 0 else None, vol_need=X.VOL_SURGE_RAT, me14=X.me14(closes), me_need=X.ME14_MIN_S7,
                ema_gap=abs(e21 - e50) / closes[-1] * 100, gap_need=X.EMA_SEP_MIN_S7 * 100,
                stack="up" if closes[-1] > e21 > e50 else "down" if closes[-1] < e21 < e50 else "none",
                swept_val=min(b["low"] for b in w[-5:-1]) < vp["val"] if vp else None,
                swept_vah=max(b["high"] for b in w[-5:-1]) > vp["vah"] if vp else None)


# ── Championship ─────────────────────────────────────────────────────────────────────────────────────────────────────
_CH_KIND = (("Springboard", "SPRING"), ("POC/PIC", "EMA200"), ("50 EMA", "EMA50"), ("FVG", "FVG"), ("SFP", "SFP"))


def _kind_champ(archetype):
    return next((code for key, code in _CH_KIND if key in archetype), "CH")


def _run_champ(E, sym, cs, btc, d0, n_closed):
    n = len(cs); closed = cs[:n_closed]; eng = E["champ"]
    bt = {c["start"]: j for j, c in enumerate(btc)}
    out, skipped, busy = [], 0, -1
    for i in range(max(d0, WIN_CH - 1), n):
        j = bt.get(cs[i]["start"])
        if j is None or j < WIN_CH - 1: continue
        c = eng.scan_candidate(sym, cs[i - WIN_CH + 1:i + 1], btc[j - WIN_CH + 1:j + 1])
        if not c: continue
        sg = dict(t=cs[i]["start"] // 1000, side=c["direction"], kind=_kind_champ(c["archetype"]), label=c["archetype"],
                  regime=c["regime"], entry=c["entry_price"], stop=c["stop_loss"], target=c["tp2"], tp1=c["tp1"],
                  risk_pct=abs(c["entry_price"] - c["stop_loss"]) / c["entry_price"], rsi=c.get("rsi"), rvol=c.get("rvol"),
                  live=i >= n_closed)
        if sg["live"]: sg["status"] = "LIVE"
        elif i <= busy: skipped += 1; continue
        else:
            r = simulate(closed, i, sg["side"], sg["stop"], sg["target"])
            if r["kind"] == "INVALID": continue
            _finish(sg, r, closed)
            busy = 10 ** 9 if r["kind"] == "OPEN" else r["exit_i"]
        out.append(sg)
    return out, skipped


def _state_champ(E, sym, cs, btc):
    CE = E["CE"]; eng = E["champ"]
    regime, btc_c, btc_200 = eng.evaluate_regime(btc[-WIN_CH:])
    w = cs[-WIN_CH:]; closes = [b["close"] for b in w]
    e21, e50, e200 = CE.calc_ema(closes, 21), CE.calc_ema(closes, 50), CE.calc_ema(closes, 200)
    rsi = CE.calc_rsi(closes, 14); b = w[-1]; rng = max(1e-8, b["high"] - b["low"])
    avg_v = sum(x["volume"] for x in w[-16:-1]) / 15.0
    return dict(regime=regime, btc_close=btc_c, btc_ema200=btc_200, btc_dist=(btc_c - btc_200) / btc_200 * 100 if btc_200 else None,
                dist200=(b["close"] - e200[-1]) / e200[-1] * 100, rsi=rsi[-1], rvol=b["volume"] / avg_v if avg_v > 0 else None,
                lower_wick=(min(b["open"], b["close"]) - b["low"]) / rng, upper_wick=(b["high"] - max(b["open"], b["close"])) / rng,
                bull_stack=b["close"] > e200[-1] and e21[-1] > e50[-1], bear_stack=b["close"] < e200[-1] and e21[-1] < e50[-1],
                banned=sym in (eng.bull_blacklist if regime == "BULL" else eng.bear_blacklist))


# ── panels ───────────────────────────────────────────────────────────────────────────────────────────────────────────
def _row(l, v, c=""):
    return dict(l=l, v=v, c=c)


def _sig_text(s):
    t = (f"{s['side']} {s['label']} · entry {_p(s['entry'])} · SL {_p(s['stop'])} · TP {_p(s['target'])}"
         + (f" · TP1 {_p(s['tp1'])}" if s.get("tp1") else "") + f" · risk {s['risk_pct'] * 100:.2f}%")
    return t + (f" · agents {s['verdict']} {s['score']:.2f}" if s.get("verdict") else "")


def _stats_rows(trades, days, sub=None):
    st = _stat(trades)
    if not st["n"]: return [_row(f"{days} days", "no closed engine trade yet", "mut")]
    kinds = " · ".join(f"{k} {v}" for k, v in sorted(st["kinds"].items(), key=lambda kv: -kv[1]))
    return [_row(f"{days} days", f"{st['n']} trades · {st['wins']} wins ({st['win_rate']:.0f}%) · avg {_r(st['avg_r'])} · total {_r(st['total_r'])}",
                 "up" if st["total_r"] >= 0 else "dn"), _row("Exits", kinds, "mut")]


def _panel_cmex5(sym, sigs, st, skipped, days):
    """(rows, headline, headline class). Labels stay under 15 characters: the page gives them one narrow column."""
    traded = [s for s in sigs if s["status"] in ("TP", "TRAIL", "BE", "SL", "TIMEOUT")]
    rej = [s for s in sigs if s["status"] == "REJECTED"]
    X = _engine()["X"]; rows = []
    live = next((s for s in reversed(sigs) if s.get("live")), None)
    total = len(sigs) + skipped
    if live and live["verdict"] == "REJECT":
        why = next((a["reason"] for a in live["agents"] if a["score"] == 0.0), f"agent score {live['score']:.2f}")
        rows.append(_row("Signal now", f"{live['side']} {live['label']} rejected by the agents: {why}", "warn"))
        head, hc = f"{live['kind']} {live['side']} rejected by agents", "warn"
    elif live:
        rows.append(_row("Signal now", _sig_text(live) + " (candle still forming)", "up" if live["side"] == "LONG" else "dn"))
        head, hc = f"{live['side']} {live['kind']} · agents {live['verdict']}", "up" if live["side"] == "LONG" else "dn"
    else:
        rows.append(_row("Signal now", "none: waiting for S2 or S7", "mut"))
        head, hc = (f"no signal · {total - len(rej)}/{total} setups passed" if total else "no signal"), "mut"
    rows.append(_row("S2 reclaim", (f"POC {_p(st['poc'])} · VAL {_p(st['val'])} · VAH {_p(st['vah'])} · volume {_x(st['vol_x'])}x (needs >{st['vol_need']:.2f}x) · "
                                    f"swept VAL {'yes' if st['swept_val'] else 'no'} / VAH {'yes' if st['swept_vah'] else 'no'}") if st["poc"] else "no volume profile yet", "mut"))
    rows.append(_row("S7 EMA", f"EMA stack {st['stack']} · efficiency {_x(st['me14'])} (needs ≥{st['me_need']:.2f}) · "
                     f"EMA21/50 gap {_x(st['ema_gap'])}% (needs ≥{st['gap_need']:.2f}%)", "mut"))
    if sym not in X.SYMBOLS:
        rows.append(_row("Engine list", "this coin is outside the engine's 15-coin list; the rules are shown anyway", "warn"))
    if total:
        rows.append(_row("Agents", f"{total - len(rej)} of {total} setups passed; {len(rej)} rejected (grey dots)"
                         + (f" · {skipped} repeats skipped while a trade was open" if skipped else ""), "mut"))
    rows += _stats_rows(traded, days)
    wi = [dict(R=s["what_if_R"], status=s["what_if"]) for s in rej if "what_if_R" in s]
    if wi:
        w = _stat(wi)
        rows.append(_row("If traded", f"the {w['n']} rejected setups: {w['wins']} wins ({w['win_rate']:.0f}%) · avg {_r(w['avg_r'])} (the engine skips them)", "mut"))
    return rows, head, hc


def _panel_champ(sym, sigs, st, skipped, days):
    traded = [s for s in sigs if s["status"] in ("TP", "TRAIL", "BE", "SL", "TIMEOUT")]
    live = next((s for s in reversed(sigs) if s.get("live")), None)
    reg = st["regime"]; rows = []
    rows.append(_row("BTC regime", (f"{reg} · BTC {_p(st['btc_close'])} is {st['btc_dist']:+.1f}% vs its 200 EMA {_p(st['btc_ema200'])} (15m)"
                                     if st["btc_dist"] is not None else reg), "up" if reg == "BULL" else "dn" if reg == "BEAR" else "mut"))
    if live:
        rows.append(_row("Signal now", _sig_text(live) + " (candle still forming)", "up" if live["side"] == "LONG" else "dn"))
        head, hc = f"{reg} · {live['side']} {live['kind']}", "up" if live["side"] == "LONG" else "dn"
    else:
        rows.append(_row("Signal now", "none: " + ("waiting for a pullback to the 21 EMA with a long lower wick" if reg == "BULL"
                                                  else "waiting for an exhaustion rejection (200 EMA / 50 EMA / FVG / SFP)"), "mut"))
        head, hc = f"{reg} · no signal", "up" if reg == "BULL" else "dn" if reg == "BEAR" else "mut"
    stack = st["bull_stack"] if reg == "BULL" else st["bear_stack"]
    rows.append(_row("This coin", f"{'blacklisted for ' + reg.lower() + ' setups' if st['banned'] else 'allowed'} · "
                     f"{'EMA stack ok' if stack else 'EMA stack not aligned'} · {st['dist200']:+.1f}% vs 200 EMA · RSI {_x(st['rsi'], 0)} · volume {_x(st['rvol'])}x · "
                     f"{'lower' if reg == 'BULL' else 'upper'} wick {(st['lower_wick'] if reg == 'BULL' else st['upper_wick']) * 100:.0f}%", "warn" if st["banned"] else "mut"))
    rows += _stats_rows(traded, days)
    if skipped: rows.append(_row("Repeats", f"{skipped} signals skipped while a trade was open", "mut"))
    return rows, head, hc


# ── entry point ──────────────────────────────────────────────────────────────────────────────────────────────────────
_cache = {}
_candle_cache = {}
_lock = threading.Lock()


def _candles(symbol, n, ttl=30):
    key = (symbol, n); now = time.time()
    with _lock:
        hit = _candle_cache.get(key)
        if hit and now - hit[0] < ttl: return hit[1]
    cs = sbgz.fetch_candles(symbol, "15", n)
    with _lock:
        _candle_cache[key] = (now, cs)
        for k in [k for k, v in _candle_cache.items() if now - v[0] > 300]: _candle_cache.pop(k, None)
    return cs


def get(mode, symbol, bars=1000, ttl=45):
    """Indicator data for the chart: the last bars+1 candles (the newest may still be forming), every signal the engine would
    have taken or rejected in them with its replayed outcome, and the panel rows."""
    if mode not in MODES: return dict(ok=False, error="unknown mode")
    bars = max(300, min(MAX_BARS, int(bars))); key = (mode, symbol, bars); now = time.time()
    with _lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < ttl: return hit[1]
    try:
        E = _engine()
        cs = _candles(symbol, bars + 1 + LEAD)
        if len(cs) < WIN_X5 + 10: return dict(ok=False, error="no candles from Bybit", symbol=symbol)
        n = len(cs); forming = cs[-1]["start"] + M15_MS > now * 1000; n_closed = n - 1 if forming else n
        d0 = max(0, n - (bars + 1))
        if mode == "cmex5":
            sigs, skipped = _run_cmex5(E, symbol, cs, d0, n_closed); st = _state_cmex5(E, cs)
        else:
            btc = cs if symbol == "BTCUSDT" else _candles("BTCUSDT", bars + 1 + LEAD)
            if len(btc) < WIN_CH: return dict(ok=False, error="no BTCUSDT candles (the Championship regime needs them)", symbol=symbol)
            sigs, skipped = _run_champ(E, symbol, cs, btc, d0, n_closed); st = _state_champ(E, symbol, cs, btc)
        shown = cs[d0:]; days = round(len(shown) * 15 / 1440, 1)
        traded = [s for s in sigs if s["status"] in ("TP", "TRAIL", "BE", "SL", "TIMEOUT")]
        rows, head, head_c = (_panel_cmex5 if mode == "cmex5" else _panel_champ)(symbol, sigs, st, skipped, days)
        panel = dict(rows=rows, headline=head, headline_c=head_c, reference=REFERENCE[mode], stats=_stat(traded), skipped=skipped,
                     days=days, state=st)
        res = sbgz._clean(dict(ok=True, mode=mode, symbol=symbol, interval="15", bars=len(shown), candles=shown, signals=sigs,
                               panel=panel, generated=int(now)))
    except Exception as e:
        return dict(ok=False, error=f"{type(e).__name__}: {e}", symbol=symbol)
    with _lock:
        _cache[key] = (now, res)
        if len(_cache) > 48:
            for k in sorted(_cache, key=lambda k: _cache[k][0])[:12]: _cache.pop(k, None)
    return res
