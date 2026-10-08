import io, contextlib, random, collections, pickle, math, bisect
with contextlib.redirect_stdout(io.StringIO()):
    exec(open("scen_an.py").read())
SEL = pickle.load(open("scen_sel.pkl", "rb")); best = SEL["best"]
tidx = {k: {t: j for j, t in enumerate(a["t"])} for k, a in ARR.items()}

def sim_mkt(arr, i, rp, Rt, side_stop=True):
    """market long on arr at next open after bar i; stop = rp below entry; target Rt*risk. returns (grossR, hours, te, tx)"""
    o, h, l, c, t = arr["o"], arr["h"], arr["l"], arr["c"], arr["t"]; n = len(c); e = i + 1
    if e >= n - 1: return None
    ep = o[e]; risk = rp * abs(ep); stop = ep - risk; tp = ep + Rt * risk
    for j in range(e, min(n, e + 96)):
        if l[j] <= stop: return (-1.0, j - e + 1, t[e], t[j] + HR)
        if h[j] >= tp: return (Rt, j - e + 1, t[e], t[j] + HR)
    j = min(n - 1, e + 95); return ((c[j] - ep) / risk, j - e + 1, t[e], t[j] + HR)

def net(Rg, rp, hours, kind="mkt", fee_scale=1.0): return Rg - (FEE[kind] * fee_scale + FUND_8H * hours / 8) / rp

def equity(tr, risk=0.02, maxpos=3, start=10.0):
    """tr: list of (te, sym, R, tx). one position per coin, max 3 open, 2% risk, $10 start."""
    tr = sorted(tr); openp = []; eq = start; pk = start; dd = 0.0; taken = 0
    for te, sym, R, tx in tr:
        for p in sorted([p for p in openp if p[0] <= te]):
            openp.remove(p); eq += p[1]; pk = max(pk, eq); dd = min(dd, eq / pk - 1)
        if len(openp) >= maxpos or any(p[2] == sym for p in openp) or eq < 1: continue
        openp.append((tx, eq * risk * R, sym)); taken += 1
    for p in sorted(openp): eq += p[1]; pk = max(pk, eq); dd = min(dd, eq / pk - 1)
    return eq, taken, dd * 100

def quarters(pairs):
    qs = [tmin + (tmax - tmin) * k / 4 for k in range(5)]; out = []
    for a, b in zip(qs, qs[1:]):
        v = [r for t, r in pairs if a <= t < b]; out.append(sum(v) / len(v) if v else float("nan"))
    return out

def evaluate(cfg, label):
    f, b, bt, tg = cfg
    fin = trades(cfg, "final"); uns = trades(cfg, "unseen")
    nf, mf, tf = tstat([(x[0], x[1]) for x in fin]); nu, mu, tu = tstat([(x[0], x[1]) for x in uns])
    q = quarters([(x[0], x[1]) for x in uns])
    allx = fin + uns
    eq, taken, dd = equity([(x[2]["res"][tg][3], x[2]["sym"] + x[2]["side"], x[1], x[2]["res"][tg][4]) for x in allx])
    wr = sum(1 for x in allx if x[1] > 0) / max(1, len(allx)) * 100
    gp = sum(x[1] for x in allx if x[1] > 0); gl = -sum(x[1] for x in allx if x[1] < 0)
    passed = nf >= 100 and mf >= 0.10 and mu > 0 and sum(1 for v in q if v == v and v > 0) >= 3
    print(f"{label:44s} FINAL n={nf:4d} R={mf:+.3f} t={tf:+.1f} | UNSEEN n={nu:4d} R={mu:+.3f} t={tu:+.1f} | unseen qtrs "
          + " ".join(f"{v:+.2f}" for v in q) + f" | win {wr:3.0f}% PF {gp/max(gl,1e-9):.2f} | $10->${eq:7.2f} ({taken} taken, DD {dd:.0f}%) {'PASS' if passed else 'fail'}")
    return passed, allx

print(f"=== FINAL + UNSEEN, one look, per scenario's frozen DEV-best variant ({len(best)} frozen configs; total configs tried {SEL['configs']}) ===")
SCEN = {"Trend pullback (fib/golden pocket/OTE)": ["FIB0.382", "FIB0.5", "FIB0.635", "FIB0.705", "FIB0.786"],
        "Trend pullback (volume profile / VWAP)": ["POC_IMPULSE", "VAL_IMPULSE", "AVWAP_FROM_LOW"],
        "Range / accumulation / distribution": ["RANGE_FADE_LOW", "RANGE_BREAK_VOL", "ACCUM_SPRING", "RANGE_SWEEP_RECLAIM"],
        "Trend reversal": ["REVERSAL_CHOCH_MKT", "REVERSAL_CHOCH_RETEST", "RSI_DIVERGENCE"],
        "Liquidity / stop hunt": ["STOPHUNT_EQUAL_LOWS"], "Fakeout": ["FAKEOUT_BODY"], "Manipulation (AMD)": ["AMD_PRIMARY", "AMD_NO_GATE"]}
passed = {}
for sc, fl in SCEN.items():
    print(f"\n-- {sc}")
    for f in fl:
        if f in best:
            cfg = best[f][0]; ok, allx = evaluate(cfg, f"{f}|{cfg[1]}|{cfg[2]}|{cfg[3]}")
            if ok: passed[f] = (cfg, allx)

# ---------- diagnosis round: Phase A showed sweeps of lows in a 1D downtrend CONTINUE down -> trade the flip (new tests) ----------
print("\n=== ROUND 2 (new hypothesis from Phase A): sell the failed sweep/fade in a 1D downtrend (flipped direction), 2R / 1.5R targets ===")
def flip_trades(f, bias, Rt, which):
    out = []
    for e in by_fam[f]:
        if not seg(e, which) or not BIAS[bias](e) or "2R" not in e["res"]: continue
        rp = e["res"]["2R"][1]; other = (e["sym"], e["side"] == "L")      # opposite side arrays
        arr = ARR.get(other); j = tidx[other].get(e["t"]) if arr else None
        if j is None: continue
        r = sim_mkt(arr, j, rp, Rt)
        if r: out.append((e["t"], net(r[0], rp, r[1]), e, r))
    return out
round2 = []
for f in ("RANGE_SWEEP_RECLAIM", "RANGE_FADE_LOW", "STOPHUNT_EQUAL_LOWS", "FAKEOUT_BODY"):
    for bias in ("against1D", "any"):
        for Rt in (1.5, 2.0):
            d = flip_trades(f, bias, Rt, "dev"); n, m, t = tstat([(x[0], x[1]) for x in d])
            v = flip_trades(f, bias, Rt, "val"); vn, vm, vt = tstat([(x[0], x[1]) for x in v])
            round2.append(((f, bias, Rt), n, m, t, vn, vm, vt))
for k, n, m, t, vn, vm, vt in sorted(round2, key=lambda r: -r[3]):
    print(f"  FLIP {k[0]:22s} {k[1]:10s} {k[2]}R  DEV n={n:5d} R={m:+.3f} t={t:+.1f} | VAL n={vn:5d} R={vm:+.3f} t={vt:+.1f}")
r2best = max(round2, key=lambda r: r[3])
if r2best[2] > 0 and r2best[5] > 0:
    f, bias, Rt = r2best[0]
    fin = flip_trades(f, bias, Rt, "final"); uns = flip_trades(f, bias, Rt, "unseen")
    nf, mf, tf = tstat([(x[0], x[1]) for x in fin]); nu, mu, tu = tstat([(x[0], x[1]) for x in uns])
    eq, taken, dd = equity([(x[3][2], x[2]["sym"], x[1], x[3][3]) for x in fin + uns])
    print(f"  frozen {r2best[0]}: FINAL n={nf} R={mf:+.3f} t={tf:+.1f} | UNSEEN n={nu} R={mu:+.3f} t={tu:+.1f} | $10->${eq:.2f} ({taken} taken, DD {dd:.0f}%)")
else:
    print("  no flipped variant positive on both DEV and VAL -> not taken to the final test")

# ---------- controls + cost sensitivity for the strongest frozen setups ----------
print("\n=== CONTROLS and COST SENSITIVITY (final + unseen) for the top frozen setups by DEV t ===")
top = sorted(best.values(), key=lambda r: -r[3])[:4]
for cfg, n, m, t in top:
    f, b, bt, tg = cfg
    allx = trades(cfg, "final") + trades(cfg, "unseen")
    real = sum(x[1] for x in allx) / max(1, len(allx))
    sens = {fs: sum(netR(x[2], tg, fs / (FEE[x[2]["kind"]] * 1e4)) for x in allx) / max(1, len(allx)) for fs in (4, 8, 14, 20)}
    rng = random.Random(1); rc = []
    for x in allx:                                         # random entries: same coin/side, same risk %, 2R, same period, same 1D filter
        e = x[2]; key = (e["sym"], e["side"] == "S"); arr = ARR[key]; rp = e["res"][tg][1]
        lo = bisect.bisect_left(arr["t"], e["t"] - 30 * DAY); hi = bisect.bisect_left(arr["t"], e["t"] + 30 * DAY)
        for _ in range(5):
            j = rng.randrange(max(300, lo), max(301, min(hi, len(arr["t"]) - 2)))
            if not BIAS[b]({"tr1d": arr["tr1d"][j]}): continue
            r = sim_mkt(arr, j, rp, 2.0)
            if r: rc.append(net(r[0], rp, r[1])); break
    fl = []
    for x in allx:                                         # flipped direction, same time, same risk, 2R
        e = x[2]; other = (e["sym"], e["side"] == "L")
        # limit setups: flip at the FILL bar (flipping at the signal bar would only include trades where price later came back = look-ahead)
        j = tidx[other].get(e["res"][tg][3]) if e["kind"] == "lmt" else tidx[other].get(e["t"])
        if j is None: continue
        r = sim_mkt(ARR[other], j, e["res"][tg][1], 2.0)
        if r: fl.append(net(r[0], e["res"][tg][1], r[1]))
    print(f"  {f}|{b}|{bt}|{tg}: setup {real:+.3f}R (n={len(allx)}) | random entries (same filter) {sum(rc)/max(1,len(rc)):+.3f}R | flipped direction {sum(fl)/max(1,len(fl)):+.3f}R"
          f" | cost 4/8/14/20bps: " + " ".join(f"{sens[k]:+.3f}" for k in (4, 8, 14, 20)))
