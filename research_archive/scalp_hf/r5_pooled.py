"""Round 5: the pooled checks of PREREG_round5.md (J1+J2+J3 in one portfolio / one t-test) and a plain description of the frozen bonus groups
in the judges. Nothing is selected here; all rules are the frozen ones from r5_frozen.json (committed before the judges ran)."""
import json, os
import numpy as np
import hf_core as C
import r5_prep as P5
import r5_run as R5

fz = json.load(open(os.path.join(R5.HERE, "r5_frozen.json")))
full = {"J1": R5.load("design"), "J2": R5.load("unseen"), "J3": R5.load("holdout2")}
rows = {}
for j, d in full.items():
    tr = d["trades"]; m = R5.inwin(tr["ent"], R5.T_J1) if j == "J1" else np.ones(len(tr["ent"]), bool)
    dang, bon, mult = R5.masks(tr, fz)
    rows[j] = (tr, m, bon, mult, d["addons"][("AVWAP", "S3")])

def cat(key, off):
    out = []
    for j, (tr, m, bon, mult, ad) in rows.items():
        o = int(j[1]) * 1000
        out.append(tr[key][m] if key != "coin" else tr[key][m] + o)
    return np.concatenate(out)

ent = cat("ent", 0); ext = cat("ext", 0); ret = cat("ret", 0); sf = cat("sf", 0); coin = cat("coin", 0)
mult = np.concatenate([rows[j][3][rows[j][1]] for j in rows]); bon = np.concatenate([rows[j][2][rows[j][1]] for j in rows])
R = ret / sf
print("# POOLED J1+J2+J3 (one chronological portfolio, 30 coins, 0.5% base risk)")
for lab, mo, mu in (("baseline@8 (production)", 8, np.ones(len(R))), ("SIZE@8 (bonus x1.5)", 8, mult)):
    eq, tk, dd = R5.money(ent, ext, ret, sf / mu, coin, mo)
    print(f"  {lab:26s} $10 -> {eq:7.2f}  ({tk} taken of {len(R)}, DD {dd * 100:.0f}%)")
# add-ons
ae, ax, ar, asf, ac = [], [], [], [], []
for j, (tr, m, bon_, mult_, ad) in rows.items():
    mm = R5.inwin(ad["ent"], R5.T_J1) if j == "J1" else np.ones(len(ad["ent"]), bool)
    o = int(j[1]) * 1000
    ae.append(ad["ent"][mm]); ax.append(ad["ext"][mm]); ar.append(ad["ret"][mm]); asf.append(ad["sf"][mm]); ac.append(ad["coin"][mm] + o + 100)
ae, ax, ar, asf, ac = map(np.concatenate, (ae, ax, ar, asf, ac))
eq12, tk12, dd12 = R5.money(ent, ext, ret, sf, coin, 12)
eqc, tkc, ddc = R5.money(np.concatenate([ent, ae]), np.concatenate([ext, ax]), np.concatenate([ret, ar]), np.concatenate([sf, asf]), np.concatenate([coin, ac]), 12)
print(f"  baseline@12                $10 -> {eq12:7.2f}  ({tk12} taken of {len(R)}, DD {dd12 * 100:.0f}%)")
print(f"  baseline+AVWAP add-ons@12  $10 -> {eqc:7.2f}  ({tkc} taken of {len(R) + len(ar)}, DD {ddc * 100:.0f}%)")
sa = C.cluster_stats(ar / asf, ae // C.DAY)
print(f"  add-ons pooled stand-alone: n {sa['n']} net R {sa['mean']:+.3f} t {sa['t']:+.2f}")
print("\n# The frozen bonus groups (young trend MAT low OR coiled range RNG low) vs everything else, in each judge (descriptive; not re-selected)")
print(f"  {'set':4s} {'bonus n':>7s} {'bonus R':>8s} {'win%':>5s} | {'other n':>7s} {'other R':>8s} {'win%':>5s} | diff t")
for j, (tr, m, bon_, mult_, ad) in list(rows.items()) + [("ALL", (None, None, bon, None, None))]:
    if j == "ALL": r_ = R; e_ = ent; b_ = bon
    else: r_ = (tr["ret"] / tr["sf"])[m]; e_ = tr["ent"][m]; b_ = bon_[m]
    sb, so = R5.cl(r_[b_], e_[b_]), R5.cl(r_[~b_], e_[~b_])
    diff = sb["mean"] - so["mean"]; t = diff / np.hypot(sb["se"], so["se"])
    print(f"  {j:4s} {sb['n']:7d} {sb['mean']:+8.3f} {np.mean(r_[b_] > 0) * 100:5.0f} | {so['n']:7d} {so['mean']:+8.3f} {np.mean(r_[~b_] > 0) * 100:5.0f} | {diff:+.3f} (t {t:+.2f})")
print("\n# $10 on the bonus-group trades alone vs the other trades alone (one portfolio per judge, 8 slots)")
for j, (tr, m, bon_, mult_, ad) in rows.items():
    eb, tb, db = R5.port(tr, m & bon_); eo, to, do = R5.port(tr, m & ~bon_)
    print(f"  {j}: bonus-only $10 -> {eb:6.2f} ({tb} taken, DD {db * 100:.0f}%) | others-only $10 -> {eo:6.2f} ({to} taken, DD {do * 100:.0f}%)")
