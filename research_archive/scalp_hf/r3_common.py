"""Round 3 (PREREG_round3.md): shared trade walk, trade tables, DEV screen / VAL confirmation / frozen runs.
A generator saves {cell_name: dict(ent, ext, net, net2, sf, slot)} for design coins and unseen coins to r3_<idea>_<design|unseen>.pkl.
  python3 r3_common.py screen IDEA[,IDEA...]   -> DEV of every cell, VAL only for screen survivors, r3_frozen_<IDEA>.json
  python3 r3_common.py final IDEA[,IDEA...]    -> frozen cells once on VAL / FINAL / UNSEEN / OLD, $10 portfolios"""
import os, sys, json, pickle
import numpy as np
import numba as nb
import hf_core as C
import hf2_setups as S2

OUT = os.path.join(C.DATA, "r3")
HERE = os.path.dirname(os.path.abspath(__file__))
COST = 0.0014
QUARTERS = [(C.ms(2025, 7), C.ms(2025, 10)), (C.ms(2025, 10), C.ms(2026, 1)), (C.ms(2026, 1), C.ms(2026, 4)), (C.ms(2026, 4), C.ms(2026, 7)),
            (C.ms(2026, 7), C.ms(2026, 10))]


@nb.njit(cache=True)
def sim_list(o, h, l, c, i1s, sides, stops, tgts, tmaxs, t0, ft, fr):
    """walk each trade on 1m bars (stop first; tgt 0 = none). returns ent_ms, ext_ms, gross, funding, ok"""
    n = len(i1s); N = len(o)
    ent = np.zeros(n, np.int64); ext = np.zeros(n, np.int64); gr = np.zeros(n); fu = np.zeros(n); ok = np.zeros(n, np.bool_)
    for e in range(n):
        i1 = i1s[e]
        if i1 >= N or np.isnan(o[i1]): continue
        last = min(i1 + tmaxs[e] - 1, N - 1)
        xb, px, r = S2.walk(o, h, l, c, i1, last, sides[e], stops[e], tgts[e], 0.0)
        if r < 0: continue
        pe = o[i1]
        ent[e] = t0 + i1 * 60000; ext[e] = t0 + (xb + 1) * 60000
        gr[e] = sides[e] * (px / pe - 1.0); fu[e] = C.funding_paid(ft, fr, ent[e], ext[e], sides[e]); ok[e] = True
    return ent, ext, gr, fu, ok


@nb.njit(cache=True)
def nonoverlap(slot, ent, ext):
    """input sorted by (slot, ent)"""
    n = len(ent); keep = np.zeros(n, np.bool_); last = -1; busy = -1
    for i in range(n):
        if slot[i] != last: last = slot[i]; busy = -1
        if ent[i] >= busy: keep[i] = True; busy = ext[i]
    return keep


class Table:
    """collects trades per cell; finish() applies one position per slot per cell"""
    def __init__(self): self.parts = {}

    def add(self, cell, ent, ext, net, sf, slot):
        p = self.parts.setdefault(cell, [])
        p.append((np.asarray(ent, np.int64), np.asarray(ext, np.int64), np.asarray(net, float), np.asarray(sf, float), np.asarray(slot, np.int64)))

    def finish(self):
        out = {}
        for cell, ps in self.parts.items():
            ent, ext, net, sf, slot = (np.concatenate([p[k] for p in ps]) for k in range(5))
            o = np.lexsort((ent, slot)); ent, ext, net, sf, slot = ent[o], ext[o], net[o], sf[o], slot[o]
            keep = nonoverlap(slot, ent, ext)
            out[cell] = dict(ent=ent[keep], ext=ext[keep], net=net[keep], net2=net[keep] - 0.0004, sf=sf[keep], slot=slot[keep])
        return out


def save(idea, which, cells):
    os.makedirs(OUT, exist_ok=True)
    pickle.dump(cells, open(os.path.join(OUT, f"r3_{idea}_{which}.pkl"), "wb"))


def load(idea, which):
    return pickle.load(open(os.path.join(OUT, f"r3_{idea}_{which}.pkl"), "rb"))


def seg_of(d, seg):
    sg = "FINAL" if seg == "UNSEEN" else seg
    return C.seg_mask(d["ent"], sg)


def stats(d, seg, stress=False):
    m = seg_of(d, seg)
    if m.sum() < 2: return dict(n=int(m.sum()), mean=float("nan"), t=float("nan"), win=float("nan"))
    R = (d["net2"] if stress else d["net"])[m] / d["sf"][m]
    st = C.cluster_stats(R, d["ent"][m] // C.DAY)
    return dict(n=st["n"], mean=st["mean"], t=st["t"], win=float(np.mean(R > 0)))


def money(d, seg, risk=0.005):
    m = seg_of(d, seg)
    if m.sum() < 1: return 10.0, 0, 0.0
    eq, taken, mdd, _, _ = C.portfolio(d["ent"][m], d["ext"][m], d["net"][m], d["sf"][m], d["slot"][m], 10.0, risk, 5, 3.0, 6.0)
    return eq, taken, mdd


def side_of(cell):
    return cell.split("|")[1]


def screen(idea):
    cells = load(idea, "design")
    rows = [(c, stats(d, "DEV")) for c, d in cells.items()]
    print(f"\n# {idea}: DEV, all {len(rows)} cells (n, net R, t, win%)")
    for c, st in sorted(rows):
        print(f"  {c:50s} n {st['n']:6d} R {st['mean']:+.3f} t {st['t']:+.2f} win {st['win'] * 100 if st['n'] > 1 else 0:.0f}%")
    elig = [(c, st) for c, st in rows if st["n"] >= 60 and np.isfinite(st["t"])]
    surv = [(c, st) for c, st in elig if st["mean"] > 0 and st["t"] >= 2.0]
    print(f"# {idea}: DEV screen survivors {len(surv)} of {len(rows)}")
    conf = []
    for c, st in surv:
        sv = stats(cells[c], "VAL")
        ok = sv["n"] >= 30 and sv["mean"] > 0 and sv["t"] >= 1.5
        print(f"  {c:50s} DEV R {st['mean']:+.3f} t {st['t']:+.2f} | VAL n {sv['n']} R {sv['mean']:+.3f} t {sv['t']:+.2f} {'CONFIRMED' if ok else ''}")
        if ok: conf.append(c)
    best = {}
    for c, st in elig:
        k = side_of(c)
        if k not in best or st["t"] > best[k][1]["t"]: best[k] = (c, st)
    json.dump(dict(confirmed=conf, report=[v[0] for v in best.values()]), open(os.path.join(HERE, f"r3_frozen_{idea}.json"), "w"), indent=1)


def evaluate(idea, cell, d, du, label):
    print(f"\n## {idea} {label}: {cell}")
    print(f"  {'period':8s} {'trades':>6s} {'win%':>5s} {'net R':>7s} {'t':>6s} {'$10@0.5%':>9s} {'taken':>6s} {'maxDD':>6s} {'$10@1%':>8s} {'+2bps R':>8s}")
    res = {}
    for seg in ("DEV", "VAL", "FINAL", "OLD", "UNSEEN"):
        dd = du if seg == "UNSEEN" else d
        if dd is None: continue
        st = stats(dd, seg); st2 = stats(dd, seg, True); eq, taken, mdd = money(dd, seg); eq1, _, _ = money(dd, seg, 0.01)
        res[seg] = dict(st=st, st2=st2, eq=eq, taken=taken, mdd=mdd, eq1=eq1)
        print(f"  {seg:8s} {st['n']:6d} {st['win'] * 100 if st['n'] > 1 else 0:5.0f} {st['mean']:+7.3f} {st['t']:+6.2f} {eq:9.2f} {taken:6d} {mdd * 100:5.0f}% {eq1:8.2f} {st2['mean']:+8.3f}")
    e = d["ent"]; R = d["net"] / d["sf"]
    q = [float(np.mean(R[(e >= a) & (e < b)])) if ((e >= a) & (e < b)).sum() else float("nan") for a, b in QUARTERS]
    print("  FINAL quarters net R: " + "  ".join(f"{v:+.3f}" for v in q))
    f = res["FINAL"]; u = res.get("UNSEEN")
    passed = (f["st"]["n"] >= 60 and f["st"]["mean"] > 0 and f["st"]["t"] >= 1.5 and u is not None and u["st"]["n"] >= 30 and u["st"]["mean"] > 0
              and sum(v > 0 for v in q if np.isfinite(v)) >= 3 and f["st2"]["mean"] > 0 and f["eq"] > 10 and f["mdd"] < 0.4)
    print(f"  PASS: {'YES' if passed else 'no'}")
    res["pass"] = passed; res["quarters"] = q
    return res


def final(idea):
    fz = json.load(open(os.path.join(HERE, f"r3_frozen_{idea}.json")))
    cells = load(idea, "design")
    try: cu = load(idea, "unseen")
    except FileNotFoundError: cu = {}
    out = {}
    for c in fz["confirmed"]: out[c] = evaluate(idea, c, cells[c], cu.get(c), "CONFIRMED")
    for c in fz["report"]:
        if c not in out: out[c] = evaluate(idea, c, cells[c], cu.get(c), "best DEV cell of its side (reporting rule)")
    pickle.dump(out, open(os.path.join(OUT, f"final_{idea}.pkl"), "wb"))


if __name__ == "__main__":
    for idea in sys.argv[2].split(","):
        {"screen": screen, "final": final}[sys.argv[1]](idea)
