"""Samudaya check on AMD-FVG's own backtest: is part of its edge a fill-model artifact?
backend_lib/amd_fvg.scan checks 'close below the gap bottom -> CANCELLED' BEFORE 'low traded through the limit -> FILLED' on the
same bar. A resting limit order fills the moment price trades through it, before that bar's close exists; and any bar that closes
below the gap bottom has necessarily traded through the entry (the 50% level). So those setups are real fills that the backtest drops.
'fixed' order: fill first; cancel only after a bar that did NOT fill. Everything else is identical (parity checked for 'orig')."""
import os, sys, json, math, collections, time
sys.path.insert(0, "/home/user/Bybit-dashbored")
from multiprocessing import Pool
from backend_lib import amd_fvg
from backend_lib.amd_fvg import _drift_ok, _outcome
REPO = os.environ.get("REPO", "/home/user/Bybit-dashbored")
for _d in ("resonance_test", "scenario_research"): sys.path.insert(1, f"{REPO}/research_archive/{_d}")
from prereg_run import tstat, equity
HR = 3600000; FUND_8H = 0.5e-4

def scan2(A, P, side="L", order="orig", fee=amd_fvg.FEE_LIMIT, fill_pen=amd_fvg.FILL_PEN):
    out = []; n = A["n"]; N = P["N"]; last = -99
    h, l, c, o, atr, vr, t = A["h"], A["l"], A["c"], A["o"], A["atr"], A["vrel"], A["t"]
    for i in range(110, n):
        if i - last < 6: continue
        at = atr[i]
        if at <= 0 or not _drift_ok(A, i, P["gate"]): continue
        if not (l[i] > h[i - 2] and (c[i - 1] - o[i - 1]) >= P["D"] * atr[i - 1] and vr[i - 1] >= P["v"]): continue
        hit = None
        for j in range(i - 2 - P["W"], i - 1):
            RL = A["rmin"][N][j]; RH0 = A["rmax"][N][j]
            if RL is None or (RH0 - RL) / atr[j] > P["comp"]: continue
            if l[j] < RL - P["s"] * atr[j] and c[i - 1] > RL: hit = (j, RL, RH0); break
        if not hit: continue
        j, RL, RH = hit
        sl = min(l[j:i]) - P["b"] * at; zb, zt = h[i - 2], l[i]; ce = (zb + zt) / 2; risk = ce - sl
        if risk <= 0 or risk / abs(ce) < P["mr"]: continue
        tp = ce + P["tp"] * risk
        rec = dict(signal_t=t[i], risk_pct=risk / abs(ce) * 100, gap_closed_below=False)
        filled = None; state = None
        for w in range(1, P["w"] + 1):
            q = i + w
            if q >= n: state = "PENDING"; break
            touch = l[q] <= ce - fill_pen * atr[i]
            if order == "orig":
                if c[q] < zb: state = "CANCELLED"; break
                if touch: filled = q; break
            else:
                if touch:
                    filled = q; rec["gap_closed_below"] = c[q] < zb; break
                if c[q] < zb: state = "CANCELLED"; break
        else:
            state = "EXPIRED"
        if filled is not None:
            st, R, xj, why = _outcome(A, filled, ce, sl, tp, P["tmax"], fee)
            rec.update(status=st, fill_t=t[filled], exit_t=t[xj], exit_reason=why, R=round(R, 4)); last = i
        else:
            rec.update(status=state, fill_t=None, exit_t=None, exit_reason=None, R=None)
        out.append(rec)
    return out

def job(args):
    folder, sym, inv = args
    rows = json.load(open(f"{folder}/{sym}_1H.json"))
    if len(rows) < 300: return [], 0
    A = amd_fvg.prep([tuple(r) for r in rows], invert=inv); res = []; parity_bad = 0
    for P in (amd_fvg.NO_GATE, amd_fvg.PRIMARY):
        ref = amd_fvg.scan(A, P, side="S" if inv else "L")
        for order in ("orig", "fixed"):
            got = scan2(A, P, "S" if inv else "L", order)
            if order == "fixed":   # backend_lib/amd_fvg.scan carries the fixed order since 2026-10-08 ("orig" = the old rule)
                parity_bad += sum(1 for a, b in zip(ref, got) if (a["signal_t"], a["status"], a["R"]) != (b["signal_t"], b["status"], b["R"])) + abs(len(ref) - len(got))
            for r in got:
                if r["status"] != "CLOSED": continue
                rp = r["risk_pct"] / 100; hours = (r["exit_t"] - r["fill_t"]) / HR + 1
                res.append(dict(folder=folder, cfg=P["name"], order=order, sym=sym, side="S" if inv else "L", t=r["signal_t"], te=r["fill_t"],
                                tx=r["exit_t"] + HR, R=r["R"] - FUND_8H * hours / 8 / rp, why=r["exit_reason"], gcb=r["gap_closed_below"]))
    return res, parity_bad

if __name__ == "__main__":
    jobs = [(f, s, inv) for f in ("d5", "d4") for s in sorted(x.split("_")[0] for x in os.listdir(f) if x.endswith("_1H.json")) for inv in (False, True)]
    with Pool(4) as p: parts = p.map(job, jobs)
    rows = [r for P_, _ in parts for r in P_]; pb = sum(b for _, b in parts)
    print(f"parity of the 'fixed' re-implementation with backend_lib/amd_fvg.scan (fixed 2026-10-08): {pb} mismatches")
    for cfg in ("AMD_NO_GATE", "AMD_PRIMARY"):
        print(f"\n=== {cfg}: backtest fill rule as coded (cancel checked first) vs corrected (a touched limit is a fill) ===")
        for folder, lab in (("d5", "2021-24 (never used for design)"), ("d4", "2024-26 (design period)"), (None, "2021-26 all")):
            for order in ("orig", "fixed"):
                x = [r for r in rows if r["cfg"] == cfg and r["order"] == order and (folder is None or r["folder"] == folder)]
                n, m, t = tstat([(r["t"], r["R"]) for r in x])
                eq1, tk1, dd1 = equity([(r["te"], r["sym"], r["R"], r["tx"]) for r in x], risk=0.01)
                eq2, tk2, dd2 = equity([(r["te"], r["sym"], r["R"], r["tx"]) for r in x], risk=0.02)
                win = sum(r["R"] > 0 for r in x) / max(1, len(x)) * 100
                print(f"  {lab:32s} {order:5s}: n={n:5d} avg {m:+.3f}R t={t:+.1f} win {win:3.0f}% | $10 at 1% -> ${eq1:8.2f} ({tk1} trades, DD {dd1:.0f}%) | at 2% -> ${eq2:9.2f} ({tk2} trades, DD {dd2:.0f}%)")
            extra = [r for r in rows if r["cfg"] == cfg and r["order"] == "fixed" and r["gcb"] and (folder is None or r["folder"] == folder)]
            if extra:
                print(f"      trades the coded rule drops (touch bar closed below the gap): n={len(extra)} avg {sum(r['R'] for r in extra)/len(extra):+.3f}R, "
                      f"stopped {sum(r['why'] == 'STOP' for r in extra)/len(extra)*100:.0f}%")
        yrs = collections.defaultdict(list)
        for r in rows:
            if r["cfg"] == cfg and r["order"] == "fixed": yrs[time.gmtime(r["t"] / 1000).tm_year].append(r["R"])
        print("  corrected rule by year: " + "  ".join(f"{y}: {sum(v)/len(v):+.3f} ({len(v)})" for y, v in sorted(yrs.items())))
