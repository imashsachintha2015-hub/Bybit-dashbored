"""Round 2 evaluation (PREREG_round2.md).
  python3 hf2_eval.py screen   -> DEV table of all 3200 cells (+ RANDOM control), VAL only for cells passing the DEV screen,
                                  frozen.json = confirmed cells + the best DEV cell of every scenario x regime x side (reporting rule)
  python3 hf2_eval.py final    -> frozen cells once on VAL, FINAL, OLD (design coins) and FINAL (unseen coins), $10 portfolios"""
import os, sys, json, itertools
import numpy as np
import numba as nb
import hf_core as C
import hf2_setups as S

R2 = S.OUT
HERE = os.path.dirname(os.path.abspath(__file__))
COST = 0.0014
REGIMES = {"ALL": None, "UP": 0, "DOWN": 1, "RANGE": 2}
FILTERS = ("NONE", "VOL")
QUARTERS = [(C.ms(2025, 7), C.ms(2025, 10)), (C.ms(2025, 10), C.ms(2026, 1)), (C.ms(2026, 1), C.ms(2026, 4)), (C.ms(2026, 4), C.ms(2026, 7)),
            (C.ms(2026, 7), C.ms(2026, 10))]


def load(names):
    parts = []
    for cid, nm in enumerate(names):
        for k in (5, 15):
            d = dict(np.load(os.path.join(R2, f"ev_{nm}_{k}.npz")))
            d["coin"] = np.full(len(d["ent"]), cid); d["tf"] = np.full(len(d["ent"]), k)
            parts.append(d)
    ev = {key: np.concatenate([p[key] for p in parts]) for key in parts[0]}
    return ev


@nb.njit(cache=True)
def nonoverlap(coin, ent, ext):
    """input sorted by (coin, ent): keep an event only if it enters at or after the exit of the previous kept event of that coin"""
    n = len(ent); keep = np.zeros(n, np.bool_); last_c = -1; busy = -1
    for i in range(n):
        if coin[i] != last_c: last_c = coin[i]; busy = -1
        if ent[i] >= busy: keep[i] = True; busy = ext[i]
    return keep


class Cells:
    def __init__(self, ev):
        self.ev = ev
        self.groups = {}
        key = ev["sid"] * 1000 + (ev["side"] + 1) * 100 + ev["tf"]
        order = np.lexsort((ev["ent"], ev["coin"], key))
        ks = key[order]
        bounds = np.flatnonzero(np.diff(ks)) + 1
        for a, b in zip(np.r_[0, bounds], np.r_[bounds, len(ks)]):
            self.groups[int(ks[a])] = order[a:b]                              # sorted by coin, then entry

    def trades(self, sid, side, tf, regime, filt, x, extra=0.0):
        """indices of the trades of one cell (after the one-position-per-coin rule) and their net R / net return"""
        idx = self.groups.get(sid * 1000 + (side + 1) * 100 + tf)
        if idx is None: return np.zeros(0, np.int64), np.zeros(0), np.zeros(0)
        ev = self.ev
        m = np.ones(len(idx), bool)
        if REGIMES[regime] is not None: m &= ev["regime"][idx] == REGIMES[regime]
        if filt == "VOL": m &= ev["relvol"][idx] >= 1.5
        idx = idx[m]
        keep = nonoverlap(ev["coin"][idx], ev["ent"][idx], ev["xms"][idx, x])
        idx = idx[keep]
        net = ev["gr"][idx, x] - COST - 2 * extra - ev["fu"][idx, x]
        return idx, net / ev["sf"][idx], net


def stats(cells, idx, R, seg):
    ev = cells.ev
    m = C.seg_mask(ev["ent"][idx], seg)
    if m.sum() < 2: return dict(n=int(m.sum()), mean=float("nan"), t=float("nan"), win=float("nan"))
    st = C.cluster_stats(R[m], ev["ent"][idx][m] // C.DAY)
    return dict(n=st["n"], mean=st["mean"], t=st["t"], win=float(np.mean(R[m] > 0)))


def all_cells():
    for sid in range(25):
        for side, tf, reg, filt, x in itertools.product((1, -1), (5, 15), REGIMES, FILTERS, range(4)):
            yield sid, side, tf, reg, filt, x


def cell_name(c):
    sid, side, tf, reg, filt, x = c
    return f"{S.SETUPS[sid]}|{'L' if side == 1 else 'S'}|{tf}m|{reg}|{filt}|{S.EXITS[x]}"


def money(cells, idx, net, seg, x, risk=0.005):
    ev = cells.ev
    m = C.seg_mask(ev["ent"][idx], seg)
    ii = idx[m]
    if len(ii) < 2: return 10.0, 0, 0.0
    eq, taken, mdd, wins, _ = C.portfolio(ev["ent"][ii], ev["xms"][ii, x], net[m], ev["sf"][ii], ev["coin"][ii], 10.0, risk, 5, 3.0, 6.0)
    return eq, taken, mdd



def screen():
    ev = load(C.DESIGN); cells = Cells(ev)
    rows = []
    for c in all_cells():
        idx, R, net = cells.trades(*c)
        st = stats(cells, idx, R, "DEV")
        rows.append((c, st))
    # control
    print("# RANDOM control (DEV, regime ALL, no filter): net R should be about minus the cost in R")
    for side in (1, -1):
        for tf in (5, 15):
            for x in range(4):
                idx, R, net = cells.trades(25, side, tf, "ALL", "NONE", x)
                st = stats(cells, idx, R, "DEV"); m = C.seg_mask(ev["ent"][idx], "DEV")
                costR = np.mean(COST / ev["sf"][idx][m])
                print(f"  RANDOM {'L' if side == 1 else 'S'} {tf}m {S.EXITS[x]:5s} n {st['n']:6d} net R {st['mean']:+.3f} (cost {-costR:.3f}, gross {st['mean'] + costR:+.3f}) t {st['t']:+.1f}")
    with open(os.path.join(HERE, "r2_dev_table.tsv"), "w") as f:
        f.write("setup\tscenario\tside\ttf\tregime\tfilter\texit\tn\tnet_R\tt\twin\n")
        for c, st in rows:
            sid, side, tf, reg, filt, x = c
            f.write(f"{S.SETUPS[sid]}\t{S.SCENARIO[S.SETUPS[sid]]}\t{'L' if side == 1 else 'S'}\t{tf}\t{reg}\t{filt}\t{S.EXITS[x]}\t{st['n']}\t{st['mean']:.4f}\t{st['t']:.2f}\t{st['win']:.3f}\n")
    elig = [(c, st) for c, st in rows if st["n"] >= 150 and np.isfinite(st["t"])]
    print(f"\n# DEV: {len(rows)} cells, {len(elig)} with n >= 150; net R > 0 in {sum(st['mean'] > 0 for _, st in elig)}; t >= 3.0 and net R > 0: "
          f"{sum(st['mean'] > 0 and st['t'] >= 3.0 for _, st in elig)}")
    tv = np.array([st["t"] for _, st in elig])
    print(f"  distribution of DEV t (n >= 150): " + "  ".join(f"p{q}={np.percentile(tv, q):+.1f}" for q in (1, 10, 50, 90, 99)) + f"  max={tv.max():+.1f}")
    print("\n# DEV top 25 cells by t (n >= 150)")
    for c, st in sorted(elig, key=lambda r: -r[1]["t"])[:25]:
        print(f"  {cell_name(c):48s} n {st['n']:5d} net R {st['mean']:+.3f} t {st['t']:+.2f} win {st['win'] * 100:.0f}%")
    # per scenario x regime summary
    print("\n# DEV per scenario x regime: cells with n >= 150, share with net R > 0, best net R and its t")
    scen = sorted(set(S.SCENARIO[s] for s in S.SETUPS[:25]))
    for sc in scen:
        for reg in REGIMES:
            sub = [(c, st) for c, st in elig if S.SCENARIO[S.SETUPS[c[0]]] == sc and c[3] == reg]
            if not sub: print(f"  {sc:13s} {reg:5s} none"); continue
            best = max(sub, key=lambda r: r[1]["t"])
            print(f"  {sc:13s} {reg:5s} cells {len(sub):4d} positive {sum(st['mean'] > 0 for _, st in sub) / len(sub) * 100:4.0f}%  best t {best[1]['t']:+.2f} "
                  f"(net R {best[1]['mean']:+.3f}, n {best[1]['n']}) {cell_name(best[0])}")
    # screen and VAL confirmation
    surv = [(c, st) for c, st in elig if st["mean"] > 0 and st["t"] >= 3.0]
    print(f"\n# DEV screen survivors: {len(surv)}; VAL confirmation (n >= 50, net R > 0, t >= 2.0)")
    confirmed = []
    for c, st in surv:
        idx, R, net = cells.trades(*c)
        sv = stats(cells, idx, R, "VAL")
        okv = sv["n"] >= 50 and sv["mean"] > 0 and sv["t"] >= 2.0
        print(f"  {cell_name(c):48s} DEV n {st['n']} R {st['mean']:+.3f} t {st['t']:+.2f} | VAL n {sv['n']} R {sv['mean']:+.3f} t {sv['t']:+.2f} {'CONFIRMED' if okv else ''}")
        if okv: confirmed.append((c, sv))
    frozen_conf = {}
    for c, sv in confirmed:
        key = (c[0], c[1])
        if key not in frozen_conf or sv["t"] > frozen_conf[key][1]["t"]: frozen_conf[key] = (c, sv)
    # reporting rule: best DEV cell per scenario x regime x side
    best = {}
    for c, st in elig:
        key = (S.SCENARIO[S.SETUPS[c[0]]], c[3], c[1])
        if key not in best or st["t"] > best[key][1]["t"]: best[key] = (c, st)
    out = dict(confirmed=[list(v[0]) for v in frozen_conf.values()],
               report=[dict(scenario=k[0], regime=k[1], side=k[2], cell=list(v[0]), dev_t=v[1]["t"], dev_R=v[1]["mean"], dev_n=v[1]["n"]) for k, v in sorted(best.items())])
    json.dump(out, open(os.path.join(HERE, "r2_frozen.json"), "w"), indent=1)
    print(f"\nfrozen: {len(out['confirmed'])} confirmed cells, {len(out['report'])} reporting cells -> r2_frozen.json")


def evaluate_cell(cells_d, cells_u, c, label):
    sid, side, tf, reg, filt, x = c
    print(f"\n## {label}: {cell_name(c)}")
    print(f"  {'period':12s} {'trades':>6s} {'win%':>5s} {'net R':>7s} {'t':>6s} {'$10@0.5%':>9s} {'taken':>6s} {'maxDD':>6s} {'$10@1%':>8s} {'+2bps R':>8s}")
    res = {}
    for seg, cl in (("DEV", cells_d), ("VAL", cells_d), ("FINAL", cells_d), ("OLD", cells_d), ("UNSEEN", cells_u)):
        sg = "FINAL" if seg == "UNSEEN" else seg
        idx, R, net = cl.trades(*c)
        st = stats(cl, idx, R, sg)
        idx2, R2_, net2 = cl.trades(*c, extra=0.0002)
        st2 = stats(cl, idx2, R2_, sg)
        eq, taken, mdd = money(cl, idx, net, sg, x, 0.005)
        eq1, _, _ = money(cl, idx, net, sg, x, 0.01)
        res[seg] = dict(st=st, st2=st2, eq=eq, taken=taken, mdd=mdd, eq1=eq1)
        print(f"  {seg:12s} {st['n']:6d} {st['win'] * 100 if st['n'] > 1 else 0:5.0f} {st['mean']:+7.3f} {st['t']:+6.2f} {eq:9.2f} {taken:6d} {mdd * 100:5.0f}% {eq1:8.2f} {st2['mean']:+8.3f}")
    # FINAL quarters
    idx, R, net = cells_d.trades(*c)
    e = cells_d.ev["ent"][idx]
    q = [float(np.mean(R[(e >= a) & (e < b)])) if ((e >= a) & (e < b)).sum() else float("nan") for a, b in QUARTERS]
    res["quarters"] = q
    print("  FINAL quarters net R: " + "  ".join(f"{v:+.3f}" for v in q))
    f = res["FINAL"]; u = res["UNSEEN"]
    passed = (f["st"]["n"] >= 100 and f["st"]["mean"] > 0 and f["st"]["t"] >= 2.0 and u["st"]["n"] >= 60 and u["st"]["mean"] > 0
              and sum(v > 0 for v in q if np.isfinite(v)) >= 3 and f["st2"]["mean"] > 0 and f["eq"] > 10 and f["mdd"] < 0.4)
    print(f"  PASS: {'YES' if passed else 'no'}")
    res["pass"] = passed
    return res


def final():
    fz = json.load(open(os.path.join(HERE, "r2_frozen.json")))
    cells_d = Cells(load(C.DESIGN)); cells_u = Cells(load(C.UNSEEN))
    out = {"confirmed": [], "report": []}
    print("# Frozen cells that passed DEV screen + VAL confirmation")
    if not fz["confirmed"]: print("  none")
    for c in fz["confirmed"]:
        out["confirmed"].append((c, evaluate_cell(cells_d, cells_u, tuple(c), "CONFIRMED")))
    print("\n# Reporting rule: best DEV cell per scenario x regime x side (frozen on DEV, whether or not it passed)")
    for r in fz["report"]:
        res = evaluate_cell(cells_d, cells_u, tuple(r["cell"]), f"{r['scenario']} / {r['regime']} / {'long' if r['side'] == 1 else 'short'}")
        out["report"].append((r, res))
    import pickle
    pickle.dump(out, open(os.path.join(R2, "final_results.pkl"), "wb"))


if __name__ == "__main__":
    {"screen": screen, "final": final}[sys.argv[1]]()
