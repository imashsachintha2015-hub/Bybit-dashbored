"""Stage 2 of PREREG_scalp_hf.md: stops/targets/costs and the $10 portfolio for the best cell of each family.
usage:  python3 hf_stage2.py select SELECT.json PKL_DEV FROZEN.json        (DEV only: pick the exit rule per cell)
        python3 hf_stage2.py evaluate FROZEN.json PKL SEG [design|unseen]   (run the frozen rules on a segment, once)"""
import sys, json, pickle, itertools
import numpy as np
import hf_core as C, hf_fam as F

GRID = [(sm, tp, mk) for sm in (1.0, 2.0) for tp in (0.0, 1.5) for mk in (False, True)]
WAIT, PEN, MINSTOP = 3, 0.05, 0.0008


def key_of(fam, var):
    return (fam, tuple(var) if isinstance(var, list) else var)


def simulate_cell(coins_data, fam, var, store, cfg, h, extra_slip=0.0, rand=False, seg=None):
    """trades of one cell on all coins -> arrays (ent, ext, ret, sf, coin)"""
    d = store[key_of(fam, var)]; tm = h + 2 if fam == "fund" else h
    ent, ext, ret, sf, cid = [], [], [], [], []
    for c, (co, pr, ft, fr) in coins_data.items():
        m = d["coin"] == c
        if not m.any(): continue
        idx = (d["t"][m] - co.t0) // C.MIN - 1; side = d["side"][m].astype(np.int64)
        if rand:                                                              # control: same count, random times, random side
            rng = np.random.default_rng(100 + c); a, b = idx.min(), max(idx.max(), idx.min() + 1000)
            idx = np.sort(rng.integers(a, b, len(idx))); side = rng.choice(np.array([-1, 1]), len(idx))
        o = np.argsort(idx, kind="stable"); idx = idx[o]; side = side[o]
        e, x, r, s, rs = C.simulate_events(co.o, co.h, co.l, co.c, pr.atr1, idx, side, cfg["stop"], cfg["tp"], tm, cfg["maker"], WAIT, PEN, MINSTOP, co.t0, ft, fr, extra_slip)
        ok = e > 0
        ent.append(e[ok]); ext.append(x[ok]); ret.append(r[ok]); sf.append(s[ok]); cid.append(np.full(ok.sum(), c))
    if not ent: return None
    return [np.concatenate(a) for a in (ent, ext, ret, sf, cid)]


def summarize(tr, risk=0.005):
    if tr is None or len(tr[0]) == 0: return dict(n=0, eq=10.0, taken=0, mdd=0.0, win=0.0, bps=float("nan"), t=float("nan"), R=float("nan"))
    ent, ext, ret, sf, cid = tr
    eq, taken, mdd, wins, _ = C.portfolio(ent, ext, ret, sf, cid, 10.0, risk, 5, 3.0, 6.0)
    st = C.cluster_stats(ret * 1e4, ent // C.DAY)
    return dict(n=len(ret), eq=eq, taken=taken, mdd=mdd, win=wins / max(taken, 1), bps=st["mean"], t=st["t"], R=float(np.mean(ret / sf)))


def load_all(names):
    out = {}
    for c, nm in enumerate(names):
        co = C.load_coin(nm); pr = F.Prep(co); ft, fr = C.load_funding(nm); out[c] = (co, pr, ft, fr)
    return out


def fmt(s):
    return f"n {s['n']:7d} | net {s['bps']:7.2f} bps/trade t {s['t']:6.1f} R {s['R']:+.3f} | $10 -> ${s['eq']:8.2f} ({s['taken']} trades, DD {s['mdd'] * 100:3.0f}%, win {s['win'] * 100:3.0f}%)"


def cfg_name(cfg): return f"stop {cfg['stop']:.0f}xATR5, " + (f"target {cfg['tp']}R" if cfg["tp"] else "no target") + (", maker" if cfg["maker"] else ", taker")


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "select":
        sel = json.load(open(sys.argv[2])); store = pickle.load(open(sys.argv[3], "rb")); names = C.DESIGN
        data = load_all(names); frozen = {}
        for fam, b in sel["best"].items():
            var = tuple(b["var"]) if isinstance(b["var"], list) else b["var"]; h = b["h"]
            print(f"\n## {fam} {var} h={h}  (DEV, design coins)")
            best = None
            for sm, tp, mk in GRID:
                cfg = dict(stop=sm, tp=tp, maker=mk)
                tr = simulate_cell(data, fam, var, store, cfg, h); s = summarize(tr)
                print(f"   {cfg_name(cfg):38s} {fmt(s)}")
                if s["n"] >= 300 and (best is None or s["eq"] > best[1]["eq"]): best = (cfg, s)
            if best: frozen[fam] = dict(var=list(var) if isinstance(var, tuple) else var, h=h, cfg=best[0], dev=best[1])
            else: print("   no exit rule with >= 300 trades")
        json.dump(frozen, open(sys.argv[4], "w"), indent=1, default=str)
    elif cmd == "evaluate":
        frozen = json.load(open(sys.argv[2])); store = pickle.load(open(sys.argv[3], "rb")); seg = sys.argv[4]
        which = sys.argv[5] if len(sys.argv) > 5 else "design"; names = C.DESIGN if which == "design" else C.UNSEEN
        data = load_all(names)
        print(f"# frozen rules on {which} coins, segment {seg}; $10, 0.5% risk, max 5 open")
        for fam, f in frozen.items():
            var = tuple(f["var"]) if isinstance(f["var"], list) else f["var"]
            if key_of(fam, var) not in store: print(f"{fam}: no events"); continue
            cfg = f["cfg"]; tr = simulate_cell(data, fam, var, store, cfg, f["h"])
            s = summarize(tr); s1 = summarize(tr, 0.01); ctrl = summarize(simulate_cell(data, fam, var, store, cfg, f["h"], rand=True))
            s2 = summarize(simulate_cell(data, fam, var, store, cfg, f["h"], extra_slip=0.0002))
            cfgT = dict(cfg, maker=False); sT = summarize(simulate_cell(data, fam, var, store, cfgT, f["h"]))
            print(f"\n## {fam} {var} h={f['h']}  [{cfg_name(cfg)}]\n   same rule with taker execution (the pass test): {fmt(sT)}\n   0.5% risk  {fmt(s)}\n   1%   risk  $10 -> ${s1['eq']:.2f} ({s1['taken']} trades, DD {s1['mdd'] * 100:.0f}%)\n   +2 bps slippage per side: {fmt(s2)}\n   random-entry control: net {ctrl['bps']:.2f} bps/trade (n {ctrl['n']})")
