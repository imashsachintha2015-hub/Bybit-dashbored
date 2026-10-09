"""Round 5 (PREREG_round5.md). usage: python3 r5_run.py select   -> situation map + frozen rules from SELECTION-A / B only (design coins)
                                    python3 r5_run.py judge    -> frozen rules once on J1 (design FINAL), J2 (unseen), J3 (HOLDOUT2)"""
import os, sys, json, pickle
import numpy as np
import hf_core as C
import r5_prep as P5

HERE = os.path.dirname(os.path.abspath(__file__))
T_A = (C.ms(2021), C.ms(2024, 7)); T_B = (C.ms(2024, 7), C.ms(2025, 7)); T_J1 = (C.ms(2025, 7), C.ms(2026, 10))
CATEG5 = {"BRK"}


def load(group): return pickle.load(open(os.path.join(P5.OUT, f"r5_{group}.pkl"), "rb"))


def inwin(ent, w): return (ent >= w[0]) & (ent < w[1])


def R_of(tr): return tr["ret"] / tr["sf"]


def cl(vals, ent):
    if len(vals) < 2: return dict(n=len(vals), mean=np.nan, t=np.nan, se=np.nan)
    return C.cluster_stats(vals, ent // C.DAY)


def money(ent, ext, ret, sf, slot, max_open=8, risk=0.005):
    if len(ent) < 1: return 10.0, 0, 0.0
    eq, taken, mdd, wins, _ = C.portfolio(ent, ext, ret, sf, slot, 10.0, risk, max_open, 3.0, 6.0)
    return eq, taken, mdd


def port(tr, mask=None, mult=None, max_open=8):
    m = np.ones(len(tr["ent"]), bool) if mask is None else mask
    sf = tr["sf"][m] / (1.0 if mult is None else mult[m])
    return money(tr["ent"][m], tr["ext"][m], tr["ret"][m], sf, tr["coin"][m], max_open)


def groups_of(tr, fn, cp):
    x = tr[fn]
    if fn in CATEG5: return x.astype(int)
    g = np.where(np.isnan(x), -1, np.digitize(x, cp))
    return g


def select():
    d = load("design"); tr = d["trades"]
    A = inwin(tr["ent"], T_A); Bm = inwin(tr["ent"], T_B)
    R = R_of(tr)
    print(f"# SELECTION: design coins; A (2021-01..2024-06) {A.sum()} trades, B (2024-07..2025-06) {Bm.sum()} trades")
    for nm, m in (("A", A), ("B", Bm)):
        eq, tk, dd = port(tr, m)
        st = cl(R[m], tr["ent"][m])
        print(f"  baseline {nm}: trades {st['n']}  win {np.mean(R[m] > 0) * 100:.0f}%  net R {st['mean']:+.3f} (t {st['t']:+.2f})  $10 -> {eq:.2f} ({tk} taken, DD {dd * 100:.0f}%)")
    cand = []; cuts = {}
    print("\n# Situation map (net R per group; A | B; $10 on A+B trades of that group alone, trades taken)")
    for fn in P5.FEATS5:
        cp = None if fn in CATEG5 else np.nanpercentile(tr[fn][A], [100 / 3, 200 / 3]).tolist()
        cuts[fn] = cp
        g = groups_of(tr, fn, cp)
        line = f"  {fn:8s}" + (f" cuts {cp[0]:+.2f}/{cp[1]:+.2f}" if cp else " categorical        ")
        print(line)
        for gv in sorted(set(g[A | Bm].tolist())):
            ma = A & (g == gv); mb = Bm & (g == gv)
            if ma.sum() < 15: continue
            sa = cl(R[ma], tr["ent"][ma]); rb = R[mb].mean() if mb.sum() else np.nan
            eq, tk, dd = port(tr, (A | Bm) & (g == gv))
            print(f"      group {gv:2d}: A n {ma.sum():4d} win {np.mean(R[ma] > 0) * 100:3.0f}% R {sa['mean']:+.3f} t {sa['t']:+5.2f} | B n {mb.sum():4d} R {rb:+.3f} | $10 -> {eq:7.2f} ({tk} tr, DD {dd * 100:.0f}%)")
            keepA = ~(g == gv)
            if sa["t"] <= -2.0 and rb < 0 and R[A & keepA].mean() > R[A].mean() and R[Bm & keepA].mean() > R[Bm].mean():
                cand.append(("danger", sa["t"], fn, int(gv)))
            if sa["t"] >= 2.0 and rb > 0: cand.append(("bonus", sa["t"], fn, int(gv)))
    danger = sorted([c for c in cand if c[0] == "danger"], key=lambda c: c[1])[:2]
    bonus = sorted([c for c in cand if c[0] == "bonus"], key=lambda c: -c[1])[:2]
    print(f"\n# accepted danger groups: {danger}\n# accepted bonus groups: {bonus}")
    # ---- add-ons
    print("\n# Add-ons (stand-alone, design coins): net R per add-on, A | B")
    chosen = {}
    for lv in P5.LEVELS:
        best = None
        for sname in P5.STOPS:
            ad = d["addons"][(lv, sname)]
            if ad is None: continue
            ra = ad["ret"] / ad["sf"]
            ma = inwin(ad["ent"], T_A); mb = inwin(ad["ent"], T_B)
            sa = cl(ra[ma], ad["ent"][ma]); sb = cl(ra[mb], ad["ent"][mb])
            eqa, tka, dda = money(ad["ent"][ma | mb], ad["ext"][ma | mb], ad["ret"][ma | mb], ad["sf"][ma | mb], ad["coin"][ma | mb] + 100, 12)
            print(f"  {lv:6s}/{sname:6s} A: n {sa['n']:4d} win {np.mean(ra[ma] > 0) * 100:3.0f}% R {sa['mean']:+.3f} (t {sa['t']:+.2f}) | B: n {sb['n']:4d} R {sb['mean']:+.3f} (t {sb['t']:+.2f}) | "
                  f"add-ons alone $10 -> {eqa:.2f} ({tka} tr, DD {dda * 100:.0f}%)")
            if sa["mean"] > 0 and sa["t"] >= 2.0 and sb["mean"] > 0 and (best is None or sa["t"] > best[1]): best = (sname, sa["t"])
        if best: chosen[lv] = best[0]
    print("# frozen add-on cells:", chosen)
    json.dump(dict(danger=danger, bonus=bonus, cuts=cuts, addons=chosen), open(os.path.join(HERE, "r5_frozen.json"), "w"), indent=1)


def masks(tr, fz):
    g = {fn: groups_of(tr, fn, fz["cuts"][fn]) for fn in P5.FEATS5}
    dang = np.zeros(len(tr["ent"]), bool); bon = np.zeros(len(tr["ent"]), bool)
    for _, _, fn, gv in fz["danger"]: dang |= g[fn] == gv
    for _, _, fn, gv in fz["bonus"]: bon |= g[fn] == gv
    mult = np.where(dang & bon, 1.0, np.where(dang, 0.5, np.where(bon, 1.5, 1.0)))
    return dang, bon, mult


def judge():
    fz = json.load(open(os.path.join(HERE, "r5_frozen.json")))
    sets = {"J1 design FINAL": None, "J2 UNSEEN": None, "J3 HOLDOUT2": None}
    d = load("design")["trades"]; u = load("unseen")["trades"]; h = load("holdout2")["trades"]
    data = {"J1 design FINAL": (d, inwin(d["ent"], T_J1), load("design")), "J2 UNSEEN": (u, np.ones(len(u["ent"]), bool), load("unseen")),
            "J3 HOLDOUT2": (h, np.ones(len(h["ent"]), bool), load("holdout2"))}
    do_rules = bool(fz["danger"] or fz["bonus"])
    print(f"# frozen: danger {fz['danger']}  bonus {fz['bonus']}  add-ons {fz['addons']}")
    res = {}
    for name, (tr, m, full) in data.items():
        R = R_of(tr); dang, bon, mult = masks(tr, fz) if do_rules else (np.zeros(len(R), bool),) * 2 + (np.ones(len(R)),)
        eq, tk, dd = port(tr, m)
        st = cl(R[m], tr["ent"][m])
        print(f"\n## {name}: baseline  trades {st['n']}  win {np.mean(R[m] > 0) * 100:.0f}%  net R {st['mean']:+.3f} (t {st['t']:+.2f})  $10 -> {eq:.2f} ({tk} taken, DD {dd * 100:.0f}%)")
        r_ = dict(base=(eq, tk, dd), n=st["n"])
        if do_rules:
            keep = m & ~dang
            sk = cl(R[keep], tr["ent"][keep]); sr = cl(R[m & dang], tr["ent"][m & dang])
            eqs, tks, dds = port(tr, keep)
            print(f"   SKIP : kept {sk['n']} R {sk['mean']:+.3f} | removed {sr['n']} R {sr['mean']:+.3f} | $10 -> {eqs:.2f} ({tks} taken, DD {dds * 100:.0f}%)  [baseline {eq:.2f}]")
            eqz, tkz, ddz = port(tr, m, mult)
            print(f"   SIZE : mean multiplier {mult[m].mean():.2f} | $10 -> {eqz:.2f} ({tkz} taken, DD {ddz * 100:.0f}%)  [baseline {eq:.2f}]")
            r_.update(skip=(eqs, tks, dds, sk, sr), size=(eqz, tkz, ddz))
        # add-ons
        ad = full["addons"]
        if fz["addons"]:
            eq8, tk8, dd8 = port(tr, m)
            eq12, tk12, dd12 = port(tr, m, None, 12)
            ents = [tr["ent"][m]]; exts = [tr["ext"][m]]; rets = [tr["ret"][m]]; sfs = [tr["sf"][m]]; slots = [tr["coin"][m]]
            print(f"   baseline@12: $10 -> {eq12:.2f} ({tk12} taken, DD {dd12 * 100:.0f}%)")
            for lv, sname in fz["addons"].items():
                a = ad[(lv, sname)]
                if a is None: continue
                ma = np.ones(len(a["ent"]), bool) if name != "J1 design FINAL" else inwin(a["ent"], T_J1)
                ra = a["ret"][ma] / a["sf"][ma]; sa = cl(ra, a["ent"][ma])
                ee = np.concatenate([tr["ent"][m], a["ent"][ma]]); xx = np.concatenate([tr["ext"][m], a["ext"][ma]])
                rr = np.concatenate([tr["ret"][m], a["ret"][ma]]); ss = np.concatenate([tr["sf"][m], a["sf"][ma]])
                sl = np.concatenate([tr["coin"][m], a["coin"][ma] + 100])
                eqc, tkc, ddc = money(ee, xx, rr, ss, sl, 12)
                print(f"   ADD-ON {lv}/{sname}: stand-alone n {sa['n']} win {np.mean(ra > 0) * 100:.0f}% R {sa['mean']:+.3f} (t {sa['t']:+.2f}) | baseline+add-ons@12 $10 -> {eqc:.2f} ({tkc} taken, DD {ddc * 100:.0f}%)")
                r_[("add", lv)] = (sa, eqc, tkc, ddc, eq12)
        res[name] = r_
    print("\n# PASS CHECKS")
    if do_rules:
        for rule in ("skip", "size"):
            wins = 0; ok_dd = True
            for name, r_ in res.items():
                e = r_[rule][0]; b = r_["base"][0]; wins += e > b; ok_dd &= (r_[rule][2] - r_["base"][2]) <= 0.10
            print(f"  {rule.upper()}: $10 above baseline in {wins}/3 judges; drawdown within +10 points everywhere: {ok_dd}")
        kept = np.concatenate([R_of(data[n][0])[data[n][1] & ~masks(data[n][0], fz)[0]] for n in data]); ek = np.concatenate([data[n][0]["ent"][data[n][1] & ~masks(data[n][0], fz)[0]] for n in data])
        rem = np.concatenate([R_of(data[n][0])[data[n][1] & masks(data[n][0], fz)[0]] for n in data]); er = np.concatenate([data[n][0]["ent"][data[n][1] & masks(data[n][0], fz)[0]] for n in data])
        if len(rem) > 1:
            sk, sr = cl(kept, ek), cl(rem, er); diff = sk["mean"] - sr["mean"]; se = np.hypot(sk["se"], sr["se"])
            print(f"  SKIP pooled kept {sk['mean']:+.3f} (n {sk['n']}) vs removed {sr['mean']:+.3f} (n {sr['n']}): diff {diff:+.3f}, t {diff / se:+.2f}")
    else:
        print("  no danger/bonus group was accepted on SELECTION: SKIP and SIZE are not evaluated")
    for lv in fz["addons"]:
        pos = 0; wins = 0
        allr = []; alle = []
        for name, r_ in res.items():
            k = ("add", lv)
            if k in r_: pos += r_[k][0]["mean"] > 0; wins += r_[k][1] > r_[k][4]
        print(f"  ADD-ON {lv}: net R > 0 in {pos}/3 judges; baseline+add-ons@12 above baseline@12 in {wins}/3")
    pickle.dump(res, open(os.path.join(P5.OUT, "r5_judge.pkl"), "wb"))


if __name__ == "__main__":
    {"select": select, "judge": judge}[sys.argv[1]]()
