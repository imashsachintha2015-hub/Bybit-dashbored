"""Round 2, Addendum 2: HTF-aligned scalps. Setup events taken only while the production 4H Kalman rules hold a trade in the same
direction (hf_ladder.coin_rung(..., 240)). Same cells (regime = KALMAN), same DEV screen / VAL confirmation / pass rule as hf2_eval."""
import os, sys, itertools, json
import numpy as np
import hf_core as C
import hf_ladder as LD
import hf2_setups as S
import hf2_eval as E

HERE = os.path.dirname(os.path.abspath(__file__))


def tag(ev, names, btc):
    trend_days = LD.btc_daily_trend(btc)
    kal = np.zeros(len(ev["ent"]), bool)
    for cid, nm in enumerate(names):
        co = btc if nm == "BTC" else C.load_coin(nm); ft, fr = C.load_funding(nm)
        e, x, r, s, rs, sd = LD.coin_rung(co, ft, fr, trend_days, 240, with_side=True)
        m = ev["coin"] == cid
        te = ev["ent"][m]
        k = np.searchsorted(e, te, side="right") - 1                         # the newest Kalman trade entered at or before the event
        ok = k >= 0
        kk = np.clip(k, 0, max(len(e) - 1, 0))
        inside = ok & (len(e) > 0) & (te < x[kk]) & (sd[kk] == ev["side"][m]) if len(e) else np.zeros(m.sum(), bool)
        kal[np.flatnonzero(m)] = inside
    return kal


class CellsK(E.Cells):
    def trades(self, sid, side, tf, regime, filt, x, extra=0.0):
        idx = self.groups.get(sid * 1000 + (side + 1) * 100 + tf)
        if idx is None: return np.zeros(0, np.int64), np.zeros(0), np.zeros(0)
        ev = self.ev
        m = ev["kal"][idx].copy()
        if filt == "VOL": m &= ev["relvol"][idx] >= 1.5
        idx = idx[m]
        keep = E.nonoverlap(ev["coin"][idx], ev["ent"][idx], ev["xms"][idx, x]); idx = idx[keep]
        net = ev["gr"][idx, x] - E.COST - 2 * extra - ev["fu"][idx, x]
        return idx, net / ev["sf"][idx], net


def main():
    btc = C.load_coin("BTC")
    evd = E.load(C.DESIGN); evd["kal"] = tag(evd, C.DESIGN, btc)
    evu = E.load(C.UNSEEN); evu["kal"] = tag(evu, C.UNSEEN, btc)
    cd = CellsK(evd); cu = CellsK(evu)
    dev = C.seg_mask(evd["ent"], "DEV")
    print(f"# share of DEV setup events inside an aligned 4H Kalman trade: {evd['kal'][dev & (evd['sid'] < 25)].mean() * 100:.1f}%")
    rows = []
    for sid, side, tf, filt, x in itertools.product(range(26), (1, -1), (5, 15), E.FILTERS, range(4)):
        c = (sid, side, tf, "KALMAN", filt, x)
        idx, R, net = cd.trades(*c)
        rows.append((c, E.stats(cd, idx, R, "DEV")))
    real = [(c, st) for c, st in rows if c[0] < 25]
    elig = [(c, st) for c, st in real if st["n"] >= 150 and np.isfinite(st["t"])]
    print(f"# DEV: {len(real)} cells, {len(elig)} with n >= 150, net R > 0 in {sum(st['mean'] > 0 for _, st in elig)}, "
          f"screen (t >= 3.0, R > 0): {sum(st['mean'] > 0 and st['t'] >= 3.0 for _, st in elig)}")
    print("# RANDOM entries inside aligned Kalman trades (DEV), the baseline these cells must beat:")
    for c, st in rows:
        if c[0] == 25 and c[4] == "NONE":
            idx, R, net = cd.trades(*c); m = C.seg_mask(evd["ent"][idx], "DEV")
            g = evd["gr"][idx[m], c[5]] / evd["sf"][idx[m]]
            print(f"  RANDOM {'L' if c[1] == 1 else 'S'} {c[2]:2d}m {S.EXITS[c[5]]:5s} n {st['n']:5d} net R {st['mean']:+.3f} gross R {g.mean():+.3f} t {st['t']:+.2f}")
    print("# DEV top 15 cells by t")
    for c, st in sorted(elig, key=lambda r: -r[1]["t"])[:15]:
        print(f"  {E.cell_name(c):50s} n {st['n']:5d} net R {st['mean']:+.3f} t {st['t']:+.2f} win {st['win'] * 100:.0f}%")
    with open(os.path.join(HERE, "r2B_dev_table.tsv"), "w") as f:
        f.write("setup\tside\ttf\tfilter\texit\tn\tnet_R\tt\n")
        for c, st in real:
            f.write(f"{S.SETUPS[c[0]]}\t{'L' if c[1] == 1 else 'S'}\t{c[2]}\t{c[4]}\t{S.EXITS[c[5]]}\t{st['n']}\t{st['mean']:.4f}\t{st['t']:.2f}\n")
    # pooled: all aligned setup events, one position per coin, per tf x exit (DEV only)
    print("# DEV pooled over all setups (one position per coin), aligned events only")
    for tf in (5, 15):
        for x in range(4):
            sel = (evd["sid"] < 25) & evd["kal"] & (evd["tf"] == tf) & dev
            idx = np.flatnonzero(sel); o = np.lexsort((evd["ent"][idx], evd["coin"][idx])); idx = idx[o]
            idx = idx[E.nonoverlap(evd["coin"][idx], evd["ent"][idx], evd["xms"][idx, x])]
            net = evd["gr"][idx, x] - E.COST - evd["fu"][idx, x]; R = net / evd["sf"][idx]
            st = C.cluster_stats(R, evd["ent"][idx] // C.DAY)
            print(f"  {tf:2d}m {S.EXITS[x]:5s} n {st['n']:6d} net R {st['mean']:+.3f} t {st['t']:+.2f} gross R {(evd['gr'][idx, x] / evd['sf'][idx]).mean():+.3f} "
                  f"gross bps {evd['gr'][idx, x].mean() * 1e4:+.1f}")
    surv = [(c, st) for c, st in elig if st["mean"] > 0 and st["t"] >= 3.0]
    confirmed = []
    for c, st in surv:
        idx, R, net = cd.trades(*c); sv = E.stats(cd, idx, R, "VAL")
        okv = sv["n"] >= 50 and sv["mean"] > 0 and sv["t"] >= 2.0
        print(f"  survivor {E.cell_name(c)} DEV t {st['t']:+.2f} | VAL n {sv['n']} R {sv['mean']:+.3f} t {sv['t']:+.2f} {'CONFIRMED' if okv else ''}")
        if okv: confirmed.append(c)
    best = {}
    for c, st in elig:
        key = (S.SCENARIO[S.SETUPS[c[0]]], c[1])
        if key not in best or st["t"] > best[key][1]["t"]: best[key] = (c, st)
    print("\n# Frozen cells, run once")
    for c in confirmed: E.evaluate_cell(cd, cu, c, "CONFIRMED")
    for (sc, side), (c, st) in sorted(best.items()):
        E.evaluate_cell(cd, cu, c, f"{sc} / KALMAN-aligned / {'long' if side == 1 else 'short'}")


if __name__ == "__main__":
    main()
