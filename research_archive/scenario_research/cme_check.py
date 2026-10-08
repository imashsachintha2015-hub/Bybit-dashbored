"""CME weekend-gap fill: exploratory breakdown (all segments are labelled) + PLACEBO control.
Weekend 'gap': price at Friday CME close (21:00 UTC summer / 22:00 winter) vs Sunday CME open (22:00 / 23:00).
Placebo: the same 49-hour window shape mid-week (Tuesday close-hour -> Thursday open-hour), where no exchange closes.
Trade: at the window end, go toward the window-start price (the 'fill'); stop = s x gap beyond entry; target = fill; 120h max;
14 bps + funding. Long logic on both price orientations (shorts = inverted series)."""
import io, contextlib, os, sys, datetime, collections, math
os.environ.setdefault("NO_AMD", "1")
with contextlib.redirect_stdout(io.StringIO()):
    exec(open("scen_an.py").read())
import scen
from scen2 import us_dst
FOLDER = os.environ.get("CME_DATA", "d4")

def trades_for(sym, inv, kind):
    scen.D = FOLDER
    t, o, h, l, c, v = scen.load(sym, inv); n = len(t); idx = {x: k for k, x in enumerate(t)}
    if n < 400: return []                                   # coin not listed yet in this period
    tr1d, _ = scen.daily_trend(t, o, h, l, c)
    out = []
    for i in range(300, n - 2):
        ts = t[i] + 3600000; dt = datetime.datetime.utcfromtimestamp(ts / 1000)
        if kind == "weekend": ok = dt.weekday() == 6
        else: ok = dt.weekday() == 3                       # Thursday "open" after a Tuesday "close"
        if not ok or dt.hour != (22 if us_dst(ts) else 23): continue
        k = idx.get(ts - 2 * 86400000 - 2 * 3600000)       # bar ending at the window start
        if k is None: continue
        cf = c[k]; ep = o[i + 1]; gap = (cf - ep) / abs(ep)
        if gap <= 0: continue                               # long orientation: price below the start -> long toward the fill
        for s in (0.5, 1.0, 2.0):
            stop = ep - s * (cf - ep); risk = ep - stop; rp = risk / abs(ep)
            if rp < 0.002: continue
            R = None
            for j in range(i + 1, min(n, i + 121)):
                if l[j] <= stop: R = -1.0; break
                if h[j] >= cf: R = (cf - ep) / risk; break
            if R is None: j = min(n - 1, i + 120); R = (c[j] - ep) / risk
            hours = j - i
            net = R - (14e-4 + 0.5e-4 * hours / 8) / rp
            fill24 = any(h[q] >= cf for q in range(i + 1, min(n, i + 25)))
            out.append(dict(sym=sym, side="S" if inv else "L", t=t[i], gap=gap, s=s, R=net, fill24=fill24, tr1d=tr1d[i],
                            Rg=R, rp=rp, hours=hours, te=t[i] + 3600000, tx=t[j] + 3600000))
    return out

def table(rows, title):
    print(f"\n{title}")
    print(f"{'gap >=':>7s} {'stop':>5s} | {'n':>5s} {'filled<24h':>10s} {'win%':>5s} {'avg net R':>9s} {'t':>5s}")
    for g in (0.005, 0.01, 0.02, 0.03):
        for s in (0.5, 1.0, 2.0):
            x = [r for r in rows if r["gap"] >= g and r["s"] == s]
            if len(x) < 10: continue
            n, m, t = tstat([(r["t"], r["R"]) for r in x])
            print(f"{g*100:6.1f}% {s:4.1f}x | {n:5d} {sum(r['fill24'] for r in x)/len(x)*100:9.0f}% {sum(r['R']>0 for r in x)/len(x)*100:4.0f}% {m:+9.3f} {t:+5.1f}")

if __name__ == "__main__":
    from multiprocessing import Pool
    coins = COINS
    with Pool(4) as p:
        W = [r for part in p.starmap(trades_for, [(s, inv, "weekend") for s in coins for inv in (False, True)]) for r in part]
        PL = [r for part in p.starmap(trades_for, [(s, inv, "placebo") for s in coins for inv in (False, True)]) for r in part]
    print(f"data folder: {FOLDER}")
    table(W, "WEEKEND (CME closed) gaps, all coins, all periods")
    table(PL, "PLACEBO mid-week windows (Tue close -> Thu open, same hours), all coins, all periods")
    table([r for r in W if r["sym"] in ("BTC", "ETH")], "WEEKEND gaps, BTC + ETH only (CME lists these)")
    table([r for r in PL if r["sym"] in ("BTC", "ETH")], "PLACEBO, BTC + ETH only")
    import pickle; pickle.dump(dict(W=W, PL=PL), open(f"cme_{FOLDER}.pkl", "wb"))
