"""Analysis of the resonance rebuild (events from res.py).
Design coins (12) / unseen coins (12); dev = first 50% of the year, val = next 25%, final = last 25% (same split as mtf_confluence).
Selection on DEV only; one look at FINAL + UNSEEN. Costs 14 bps market round trip + 0.5 bp funding per 8h.
One open trade per coin and side in every test; the $10 portfolio holds max 3 positions, one per coin."""
import pickle, json, math, collections, random, sys, os, statistics
REPO = os.environ.get("REPO", "/home/user/Bybit-dashbored"); sys.path.insert(1, f"{REPO}/research_archive/scenario_research")
from prereg_run import tstat, equity        # day-clustered t-stat and the $10 portfolio (scenario_research/prereg_run.py)
EV = pickle.load(open(os.environ.get("RES_EV", "res_ev.pkl"), "rb"))["ev"]
A = set("BTC ETH SOL XRP DOGE BNB ADA AVAX LINK DOT LTC NEAR".split())
b = json.load(open("s15/BTC_15m.json")); t0, t1 = b[0][0], b[-1][0]; T1 = t0 + (t1 - t0) * .5; T2 = t0 + (t1 - t0) * .75
FUND_8H = 0.5e-4; EXI = {"L": 0, "S1": 1, "S2": 2}
TRIG = ("choch", "ema"); THR = (3, 4, 5); ADXF = (0, 20, 25); EXITS = ("L", "S1", "S2")
XNAME = {"L": "ladder 1/1.5/2.25R+BE", "S1": "all at 1.5R", "S2": "all at 2.25R"}
BY = collections.defaultdict(list)
for e in EV: BY[e["kind"]].append(e)

def seg(e, w):
    if w == "dev": return e["sym"] in A and e["t"] < T1
    if w == "val": return e["sym"] in A and T1 <= e["t"] < T2
    if w == "final": return e["sym"] in A and e["t"] >= T2
    if w == "unseen": return e["sym"] not in A
    if w == "oos": return (e["sym"] in A and e["t"] >= T2) or e["sym"] not in A
    return True

def dedupe(rows):
    rows.sort(key=lambda r: r["te"]); last = {}; out = []
    for r in rows:
        k = (r["sym"], r["side"])
        if r["te"] < last.get(k, 0): continue
        last[k] = r["tx"]; out.append(r)
    return out

def trades(cfg, w, fee=14e-4, kind=None, flip=False):
    trig, thr, adxm, x = cfg; k = kind or trig; rows = []
    for e in BY[k]:
        if e["adx"] < adxm or not seg(e, w): continue
        if k == "resx":
            if not (e["res_prev"] < thr <= e["res"]): continue
        elif e["res"] < thr: continue
        R, hours, te, tx = (e["flip"] if flip else e["out"])[EXI[x]]
        rows.append(dict(t=e["t"], te=te, tx=tx, sym=e["sym"], side=e["side"], rp=e["rp"], Rg=R, hours=hours,
                         R=R - (fee + FUND_8H * hours / 8) / e["rp"]))
    return dedupe(rows)

def st(rows):
    n, m, t = tstat([(r["t"], r["R"]) for r in rows]) if len(rows) >= 2 else (len(rows), 0.0, 0.0)
    return n, m, t

def quarters(rows):
    qs = [t0 + (t1 - t0) * k / 4 for k in range(5)]; out = []
    for a, b_ in zip(qs, qs[1:]):
        v = [r["R"] for r in rows if a <= r["t"] < b_]; out.append(sum(v) / len(v) if v else float("nan"))
    return out

def describe(rows):
    if not rows: return "no trades"
    wins = sum(r["R"] > 0 for r in rows); gp = sum(r["R"] for r in rows if r["R"] > 0); gl = -sum(r["R"] for r in rows if r["R"] < 0)
    return f"win {wins / len(rows) * 100:3.0f}% PF {gp / max(gl, 1e-9):4.2f}"

def eq_str(rows, risk):
    eq, taken, dd = equity([(r["te"], r["sym"], r["R"], r["tx"]) for r in rows], risk=risk)
    return f"${eq:6.2f} ({taken} trades, max DD {dd:.0f}%)"

configs = [(tg, th, ad, x) for tg in TRIG for th in THR for ad in ADXF for x in EXITS]
print(f"events: {dict((k, len(v)) for k, v in BY.items())}")
import time as _tm; D_ = lambda x: _tm.strftime("%Y-%m-%d", _tm.gmtime(x / 1000))
print(f"design coins {sorted(A)}; dev < {D_(T1)} <= val < {D_(T2)} <= final; {len(configs)} configurations\n")

# ---------------- every configuration, before and after fees (all coins, full year) -- descriptive
print("=== ALL 54 CONFIGS, all 24 coins, full year: avg R per trade before fees -> after 14 bps (trades) ===")
allres = []
for cfg in configs:
    g = trades(cfg, "all", fee=0.0); nt = trades(cfg, "all")
    allres.append((cfg, st(g)[1], st(nt)))
for cfg, gm, (n, m, t) in allres:
    print(f"  {cfg[0]:5s} res>={cfg[1]} adx>={cfg[2]:2d} {XNAME[cfg[3]]:22s}: gross {gm:+.3f}R -> net {m:+.3f}R (t={t:+5.1f}, n={n}, {n/365:.1f}/day)")
print(f"positive after fees: {sum(1 for _, _, s in allres if s[1] > 0)} of {len(allres)}; positive before fees: {sum(1 for _, g, _ in allres if g > 0)}")
cost_r = [14e-4 / e["rp"] for e in BY["choch"] + BY["ema"]]
print(f"median fee cost per trade in R (14 bps / stop%): {statistics.median(cost_r):.2f}R  (median stop {statistics.median(e['rp'] for e in BY['choch'])*100:.2f}%)")

# ---------------- selection on DEV only
dev = {cfg: st(trades(cfg, "dev")) for cfg in configs}
print(f"\n=== SELECTION ON DEV (design coins, first half of the year) ===")
print(f"chance check: configs with DEV t>=3: {sum(1 for s in dev.values() if s[0] >= 60 and s[2] >= 3)} (luck ~{0.00135*len(configs):.2f}); "
      f"t>=2: {sum(1 for s in dev.values() if s[0] >= 60 and s[2] >= 2)} (luck ~{0.0228*len(configs):.1f})")
frozen = []
for tg in TRIG:
    for x in EXITS:
        cand = [(cfg, s) for cfg, s in dev.items() if cfg[0] == tg and cfg[3] == x and s[0] >= 60]
        if not cand: continue
        cfg, s = max(cand, key=lambda z: z[1][2]); v = st(trades(cfg, "val"))
        frozen.append(cfg)
        print(f"  frozen {tg:5s} {XNAME[x]:22s} -> res>={cfg[1]} adx>={cfg[2]:2d} | DEV n={s[0]:5d} {s[1]:+.3f}R t={s[2]:+.1f} | VAL n={v[0]:5d} {v[1]:+.3f}R t={v[2]:+.1f}")

# ---------------- one look: FINAL + UNSEEN
print(f"\n=== ONE LOOK: FINAL (design coins, last quarter) + UNSEEN coins (full year) ===")
print("pass = FINAL n>=100, FINAL avg >= +0.10R, UNSEEN avg > 0, UNSEEN positive in >= 3 of 4 quarters")
passed = []
for cfg in frozen:
    f = trades(cfg, "final"); u = trades(cfg, "unseen"); fs = st(f); us = st(u); q = quarters(u)
    ok = fs[0] >= 100 and fs[1] >= 0.10 and us[1] > 0 and sum(1 for v in q if v == v and v > 0) >= 3
    if ok: passed.append(cfg)
    oos = f + u
    print(f"  {cfg[0]:5s} res>={cfg[1]} adx>={cfg[2]:2d} {XNAME[cfg[3]]:22s} | FINAL n={fs[0]:4d} {fs[1]:+.3f}R t={fs[2]:+.1f} | UNSEEN n={us[0]:5d} {us[1]:+.3f}R t={us[2]:+.1f}"
          f" qtrs " + " ".join(f"{v:+.2f}" for v in q) + f" | {describe(oos)} | {'PASS' if ok else 'fail'}")
    print(f"        $10 at 1% risk -> {eq_str(oos, 0.01)} | at 2% risk -> {eq_str(oos, 0.02)}")

# ---------------- controls and cost sensitivity on the out-of-sample trades
print(f"\n=== CONTROLS (FINAL + UNSEEN): does the trigger beat resonance alone, random entries with the same filter, and the opposite direction? ===")
for cfg in frozen:
    real = trades(cfg, "oos"); ro = trades(cfg, "oos", kind="resx"); rd = trades(cfg, "oos", kind="rand"); fl = trades(cfg, "oos", flip=True)
    sens = " ".join(f"{st(trades(cfg, 'oos', fee=bps*1e-4))[1]:+.3f}" for bps in (4, 8, 14, 20))
    print(f"  {cfg[0]:5s} res>={cfg[1]} adx>={cfg[2]:2d} {XNAME[cfg[3]]:22s}: setup {st(real)[1]:+.3f}R (n={len(real)}) | resonance alone {st(ro)[1]:+.3f}R (n={len(ro)})"
          f" | random+filter {st(rd)[1]:+.3f}R (n={len(rd)}) | flipped {st(fl)[1]:+.3f}R | cost 4/8/14/20 bps: {sens}")

# ---------------- the configuration closest to the video (full resonance, strong ADX, CHoCH, TP ladder): shown for reference
vid = ("choch", 5, 25, "L")
for w, lab in (("all", "all coins, full year"), ("oos", "final + unseen")):
    r = trades(vid, w); s = st(r)
    print(f"\nvideo-like setup (CHoCH, all 5 timeframes agree, ADX>=25, TP1/TP2/TP3 ladder), {lab}: n={s[0]} {s[1]:+.3f}R t={s[2]:+.1f} | {describe(r)} | "
          f"$10 at 1% -> {eq_str(r, 0.01)} | at 2% -> {eq_str(r, 0.02)} | {len(r)/365:.1f} trades/day")
print(f"\nPASSED: {[c for c in passed] if passed else 'none'}")
pickle.dump(dict(frozen=frozen, passed=passed, dev=dev), open("res_sel.pkl", "wb"))
