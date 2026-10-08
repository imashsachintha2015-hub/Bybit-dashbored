import pickle, random, math, json, sys, itertools, collections, time
sys.path.insert(0, "/home/user/Bybit-dashbored")
from backend_lib import amd_fvg
P = pickle.load(open("scen_ev.pkl", "rb")); EV = P["ev"]; COINS = P["coins"]
ARR = pickle.load(open("scen_arr.pkl", "rb"))
DAY = 86400000; HR = 3600000
FEE = {"mkt": 14e-4, "lmt": 8e-4}; FUND_8H = 0.5e-4
rnd = random.Random(7); others = [c for c in COINS if c != "BTC"]; rnd.shuffle(others)
n_design = round(len(COINS) * 0.6) - 1
DESIGN = set(["BTC"] + others[:n_design]); UNSEEN = set(others[n_design:])
tmin = min(e["t"] for e in EV); tmax = max(e["t"] for e in EV)
T1 = tmin + (tmax - tmin) * 0.5; T2 = tmin + (tmax - tmin) * 0.75
print(f"design coins {len(DESIGN)}: {sorted(DESIGN)}\nunseen coins {len(UNSEEN)}: {sorted(UNSEEN)}")
print(f"dev < {time.strftime('%Y-%m-%d', time.gmtime(T1/1000))} <= val < {time.strftime('%Y-%m-%d', time.gmtime(T2/1000))} <= final")

# ---- add frozen AMD-FVG (backend_lib/amd_fvg.py) as the manipulation scenario
def add_amd():
    out = []
    btc = {}
    for inv in (False, True):
        rows = json.load(open("d4/BTC_1H.json"))
        import scen
        t, o, h, l, c, v = scen.load("BTC", inv); _, tr = scen.daily_trend(t, o, h, l, c); btc[inv] = tr
    import scen
    for sym in COINS:
        rows = json.load(open(f"d4/{sym}_1H.json"))
        for inv in (False, True):
            t, o, h, l, c, v = scen.load(sym, inv); tr1d, _ = scen.daily_trend(t, o, h, l, c); idx = {x: k for k, x in enumerate(t)}
            A = amd_fvg.prep([tuple(r) for r in rows], invert=inv)
            for P_ in (amd_fvg.PRIMARY, amd_fvg.NO_GATE):
                for r in amd_fvg.scan(A, P_, side="S" if inv else "L"):
                    k = idx.get(r["signal_t"]); day = (r["signal_t"] // DAY) if (r["signal_t"] + HR) % DAY == 0 else (r["signal_t"] // DAY - 1)
                    e = dict(fam=P_["name"], sym=sym, side="S" if inv else "L", t=r["signal_t"], kind="lmt", tr1d=tr1d[k] if k is not None else 0,
                             btc=btc[inv].get(day, 0), tr1h=0, res=None)
                    if r["status"] == "CLOSED":
                        rp = r["risk_pct"] / 100; Rg = r["R"] + amd_fvg.FEE_LIMIT / rp
                        e["res"] = {"2R": (Rg, rp, (r["exit_t"] - r["fill_t"]) / HR + 1, r["fill_t"], r["exit_t"] + HR)}
                    out.append(e)
    return out
EV += add_amd()

def netR(e, tg, fee_scale=1.0):
    Rg, rp, hours, te, tx = e["res"][tg]
    return Rg - (FEE[e["kind"]] * fee_scale + FUND_8H * hours / 8) / rp

def tstat(pairs):
    d = collections.defaultdict(list)
    for t, r in pairs: d[t // DAY].append(r)
    n = sum(len(v) for v in d.values())
    if n < 2: return n, 0.0, 0.0
    m = sum(x for v in d.values() for x in v) / n
    se = math.sqrt(sum((sum(v) - m * len(v)) ** 2 for v in d.values())) / n
    return n, m, (m / se if se > 0 else 0.0)

def seg(e, which):
    if which == "dev": return e["sym"] in DESIGN and e["t"] < T1
    if which == "val": return e["sym"] in DESIGN and T1 <= e["t"] < T2
    if which == "final": return e["sym"] in DESIGN and e["t"] >= T2
    if which == "unseen": return e["sym"] in UNSEEN
    return True

# ================= PHASE A: scenario baselines (no setup logic), DEV only =================
print("\n=== PHASE A: what price does after each scenario event (DEV, design coins; gross, long-orientation, next-open entry) ===")
months = (T1 - tmin) / DAY / 30.4
print(f"{'scenario event':24s} {'per coin/mo':>11s} | {'fwd 4h bps (t)':>16s} {'fwd 24h bps (t)':>17s} {'fwd 72h bps (t)':>17s} | 24h with-1D-trend / against")
fams = sorted(set(e["fam"] for e in EV if "fwd" in e))
for f in fams:
    E = [e for e in EV if e["fam"] == f and seg(e, "dev") and "fwd" in e]
    if not E: continue
    row = []
    for k in (4, 24, 72):
        n, m, t = tstat([(e["t"], e["fwd"][k]) for e in E]); row.append(f"{m*1e4:+7.1f} ({t:+4.1f})")
    w = tstat([(e["t"], e["fwd"][24]) for e in E if e["tr1d"] == 1]); a = tstat([(e["t"], e["fwd"][24]) for e in E if e["tr1d"] == -1])
    print(f"{f:24s} {len(E)/len(DESIGN)/2/months:11.1f} | {row[0]:>16s} {row[1]:>17s} {row[2]:>17s} | {w[1]*1e4:+6.1f} ({w[2]:+.1f}) / {a[1]*1e4:+6.1f} ({a[2]:+.1f})")
S = [e for e in EV if e["fam"] == "STATE_SAMPLE" and seg(e, "dev") and "fwd" in e]
for lab, val in (("1D UPTREND (with-trend side)", 1), ("1D DOWNTREND (counter side)", -1), ("1D NO TREND", 0)):
    x = [(e["t"], e["fwd"][24]) for e in S if e["tr1d"] == val]; n, m, t = tstat(x)
    print(f"  trend state {lab:32s} n={n:6d} fwd 24h {m*1e4:+6.1f} bps (t={t:+.1f})")

# ================= PHASE B: setups x variants, selection on DEV only =================
BIAS = {"any": lambda e: True, "with1D": lambda e: e["tr1d"] == 1, "against1D": lambda e: e["tr1d"] == -1}
BTC = {"any": lambda e: True, "btcAligned": lambda e: e["btc"] == 1}
trade_fams = [f for f in sorted(set(e["fam"] for e in EV)) if f != "STATE_SAMPLE"]
by_fam = collections.defaultdict(list)
for e in EV:
    if e["res"]: by_fam[e["fam"]].append(e)
configs = []
for f in trade_fams:
    tgs = sorted(set(k for e in by_fam[f] for k in e["res"]))
    for b, bt, tg in itertools.product(BIAS, BTC, tgs):
        configs.append((f, b, bt, tg))
print(f"\n=== PHASE B: {len(configs)} configurations ({len(trade_fams)} setups x bias x BTC x target), net of costs ===")
def trades(cfg, which, fee_scale=1.0):
    f, b, bt, tg = cfg
    return [(e["t"], netR(e, tg, fee_scale), e) for e in by_fam[f] if tg in e["res"] and BIAS[b](e) and BTC[bt](e) and seg(e, which)]
res = []
for cfg in configs:
    d = trades(cfg, "dev"); n, m, t = tstat([(x[0], x[1]) for x in d])
    res.append((cfg, n, m, t))
best = {}
for cfg, n, m, t in res:
    if n >= 60 and (cfg[0] not in best or t > best[cfg[0]][3]): best[cfg[0]] = (cfg, n, m, t)
print(f"chance check: configs with DEV t>=3 and n>=60: {sum(1 for r in res if r[1]>=60 and r[3]>=3)} (expected by luck ~{0.00135*len(configs):.1f})")
print(f"\n{'setup (best variant on DEV)':58s} {'DEV n':>6s} {'avgR':>6s} {'t':>5s} | {'VAL n':>6s} {'avgR':>6s} {'t':>5s}")
order = sorted(best.values(), key=lambda r: -r[3])
for cfg, n, m, t in order:
    v = trades(cfg, "val"); vn, vm, vt = tstat([(x[0], x[1]) for x in v])
    print(f"{cfg[0]+' | '+cfg[1]+' | '+cfg[2]+' | tgt '+cfg[3]:58s} {n:6d} {m:+6.3f} {t:+5.1f} | {vn:6d} {vm:+6.3f} {vt:+5.1f}")
pickle.dump(dict(best=best, design=DESIGN, unseen=UNSEEN, T=(tmin, T1, T2, tmax), configs=len(configs)), open("scen_sel.pkl", "wb"))
