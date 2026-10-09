"""ONE-LOOK run of PREREG_trend_2020.md: the frozen trend-following setups on 2020-01-01 .. 2021-05-31 (never used before).
Engine and rules unchanged (tl_core.py, tl_setups.py); the d6 bars are prepended to the joined series so features warm up causally."""
import os, sys, pickle, collections, time, calendar
os.environ["TL_WITH_D6"] = "1"
import numpy as np
from multiprocessing import Pool
import tl_core as T, tl_study as ST, tl_setups as SU
ms = lambda d: calendar.timegm(time.strptime(d, "%Y-%m-%d")) * 1000
A0, A1 = ms("2020-01-01"), ms("2021-06-01")
TESTS = [("KALMAN1", "kal", "any"), ("STRONG", 2, "any"), ("OPTBRK", 3, "with1D"), ("CHOCH", 3, "any")]
FULL = {"BTC", "ETH", "LTC", "XRP", "BCH", "ETC", "TRX"}

def pick(R, cfg):
    st, x, flt = cfg; out = [r for r in R if r["setup"] == st and r["x"] == x and A0 <= r["t"] < A1 and (flt != "with1D" or r["tr"] == 1)]
    out.sort(key=lambda r: r["te"]); last = {}; keep = []
    for r in out:
        k = (r["sym"], r["side"])
        if r["te"] < last.get(k, 0): continue
        last[k] = r["tx"]; keep.append(r)
    return keep

if __name__ == "__main__":
    assert T.WITH_D6
    syms = sorted(f.split("_")[0] for f in os.listdir("d6") if f.endswith("_1H.json"))
    bL, bS = ST.btc_map(False), ST.btc_map(True)
    with Pool(4) as p: S_all = [s for s in p.map(ST.build, [(s, inv, bS if inv else bL) for s in syms for inv in (False, True)]) if s]
    with Pool(4) as p: parts = p.map(SU.run_series, S_all)
    R = [r for P_ in parts for r in P_]
    print(f"series {len(S_all)}; first 4H bar {time.strftime('%Y-%m-%d', time.gmtime(min(s['t'][0] for s in S_all) / 1000))}; "
          f"trades in 2020-01 .. 2021-05: {sum(1 for r in R if A0 <= r['t'] < A1)}")
    print("\n=== PRE-REGISTERED, one look: 2020-01-01 .. 2021-05-31 (pass: avg > 0 and t >= 2.0; strict t >= 2.33) ===")
    ens = []
    for cfg in TESTS:
        x = pick(R, cfg); ens += x; n, m, t = SU.st_(x); v = "PASS strict" if m > 0 and t >= 2.33 else "PASS" if m > 0 and t >= 2.0 else "FAIL"
        L = [r["R"] for r in x if r["side"] == "L"]; S = [r["R"] for r in x if r["side"] == "S"]
        fu = [r["R"] for r in x if r["sym"] in FULL]
        print(f"  {cfg[0]:8s} {str(cfg[1]):5s} {cfg[2]:7s} n={n:5d} avg {m:+.3f}R t={t:+.1f} | {SU.winpf(x)} | long {np.mean(L) if L else 0:+.3f} ({len(L)}) short {np.mean(S) if S else 0:+.3f} ({len(S)})"
              f" | 7 full-history coins {np.mean(fu) if fu else 0:+.3f} ({len(fu)}) | {v}")
        print(f"        $10 at 1% -> {SU.eqs(x, 0.01)} | at 2% -> {SU.eqs(x, 0.02)}")
    n, m, t = SU.st_(ens); v = "PASS strict" if m > 0 and t >= 2.33 else "PASS" if m > 0 and t >= 2.0 else "FAIL"
    print(f"  ENSEMBLE (all four in one portfolio) n={n} avg {m:+.3f}R t={t:+.1f} | {SU.winpf(ens)} | {v}")
    print(f"        $10 at 1% -> {SU.eqs(ens, 0.01)} | at 2% -> {SU.eqs(ens, 0.02)}")
    print("\n--- descriptive only: the same four on every period, all coins (2020-21 is the only never-seen one) ---")
    for cfg in TESTS + [("ENSEMBLE", None, None)]:
        line = f"  {cfg[0]:8s}"
        for a, b, lab in ((ms("2020-01-01"), ms("2021-06-01"), "2020-21"), (ms("2021-06-01"), ms("2024-01-01"), "2021-23"),
                          (ms("2024-01-01"), ms("2025-01-01"), "2024"), (ms("2025-01-01"), ms("2026-10-01"), "2025-26")):
            xs = []
            for c_ in (TESTS if cfg[0] == "ENSEMBLE" else [cfg]):
                st_, x_, f_ = c_
                xs += [r for r in R if r["setup"] == st_ and r["x"] == x_ and a <= r["t"] < b and (f_ != "with1D" or r["tr"] == 1)]
            line += f" | {lab}: {np.mean([r['R'] for r in xs]) if xs else 0:+.3f} ({len(xs)})"
        print(line)
    pickle.dump(R, open("tl_trades_2020.pkl", "wb"))
