"""Replays the paper-trader state machine of daemons/forward_wvbr.py over research data and compares it with the research ledger (scratch/wide_trades_f.pkl).
Needs scratch/binance_15m and the ledger (gitignored research files) - skipped when they are absent."""
import os, sys
try:
    import numpy as np
except ImportError:    # CI installs only requirements.txt; this test needs the research stack
    np = None
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, "daemons"))
import forward_wvbr as W

def main():
    if np is None: print("skipped (numpy not installed)"); return
    led = os.path.join(ROOT, "scratch", "wide_trades_f.pkl")
    if not os.path.exists(led): print("skipped (no research files)"); return
    import pandas as pd
    L = pd.read_pickle(led); L = L[(L.variant == "week|vp|accT|accT|n4|s1.0|t4.0|m0.01") & (L.set == "real")]
    for sym in ("SOLUSDT", "LINKUSDT", "ETHUSDT"):
        z = np.load(os.path.join(ROOT, "scratch", "binance_15m", sym + ".npz")); t, o, h, l, c, v = (z[k] for k in ("t", "o", "h", "l", "c", "v"))
        bars = [dict(t=int(t[i]), o=float(o[i]), h=float(h[i]), l=float(l[i]), c=float(c[i]), v=float(v[i])) for i in range(len(t))]
        cs = {"symbol": sym, "pending": [], "pos": None}; ev = []; ws = None
        def emit(e):
            if e["type"] == "_armed": e["order"]["gate_ok"] = True
            else: ev.append(e)
        by_week = {}
        for b in bars: by_week.setdefault(W.week_start(b["t"]), []).append(b)
        for k in sorted(by_week):
            prev = by_week.get(k - W.WEEK)
            if not prev or len(prev) != 672: cs.pop("levels", None); continue
            vah, val = W.profile_va(prev); cs["levels"] = dict(ws=k, vah=vah, val=val); cs["up"] = cs["dn"] = 0
            for b in by_week[k]: W.advance(cs, b, emit)
        mine = {e["fill_t"]: e for e in ev if e["type"] == "exit"}
        ref = L[L.coin == sym]; n_ok = 0; n_all = 0
        for _, r in ref.iterrows():
            ft = int(r.t_fill); n_all += 1
            if ft in mine and abs(mine[ft]["R"] - r.R) < 5e-3: n_ok += 1
        miss = [r for _, r in ref.iterrows() if int(r.t_fill) not in mine or abs(mine[int(r.t_fill)]["R"] - r.R) >= 5e-3]
        assert all(int(r.kind) == 3 for r in miss), "an unmatched ledger trade is not an end-of-data open trade"   # kind 3 = still open at the end of the data (marked at the last close)
        assert not (set(mine) - set(int(x) for x in ref.t_fill)), "engine trade missing from the ledger"
        print(f"{sym}: ledger {n_all} trades, paper engine {len(mine)}; identical fill time and R: {n_ok}; unmatched = {len(miss)} still open at the end of the data")
        assert n_ok >= 0.9 * n_all, f"{sym}: only {n_ok}/{n_all} match"
    # profile maths vs the research profile
    from importlib import util
    sp = util.spec_from_file_location("frvp", os.path.join(ROOT, "scratch", "frvp.py")); fr = util.module_from_spec(sp); sp.loader.exec_module(fr)
    wk = by_week[sorted(by_week)[40]]; p = fr.build([b["h"] for b in wk], [b["l"] for b in wk], [b["v"] for b in wk])
    vah, val = W.profile_va(wk); assert abs(vah - p["vah"]) < 1e-9 * vah and abs(val - p["val"]) < 1e-9 * val; print("profile VAH/VAL identical to scratch/frvp.build")
    print("OK")

if __name__ == "__main__": main()
