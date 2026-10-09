"""Round 5 follow-up (exploratory, after the judges): are the SIZE and ADD-ON gains just more risk? Pooled J1+J2+J3, one portfolio.
Baseline at higher base risk vs the frozen add-on / size rules, compared at similar max drawdown."""
import json, os
import numpy as np
import hf_core as C
import r5_run as R5

fz = json.load(open(os.path.join(R5.HERE, "r5_frozen.json")))
S = {"J1": R5.load("design"), "J2": R5.load("unseen"), "J3": R5.load("holdout2")}
E, X, Rt, F_, Co, Mu, AE, AX, AR, AF, AC = ([] for _ in range(11))
for j, d in S.items():
    tr = d["trades"]; m = R5.inwin(tr["ent"], R5.T_J1) if j == "J1" else np.ones(len(tr["ent"]), bool)
    _, _, mult = R5.masks(tr, fz); o = int(j[1]) * 1000
    E.append(tr["ent"][m]); X.append(tr["ext"][m]); Rt.append(tr["ret"][m]); F_.append(tr["sf"][m]); Co.append(tr["coin"][m] + o); Mu.append(mult[m])
    ad = d["addons"][("AVWAP", "S3")]; mm = R5.inwin(ad["ent"], R5.T_J1) if j == "J1" else np.ones(len(ad["ent"]), bool)
    AE.append(ad["ent"][mm]); AX.append(ad["ext"][mm]); AR.append(ad["ret"][mm]); AF.append(ad["sf"][mm]); AC.append(ad["coin"][mm] + o + 100)
E, X, Rt, F_, Co, Mu, AE, AX, AR, AF, AC = map(np.concatenate, (E, X, Rt, F_, Co, Mu, AE, AX, AR, AF, AC))
print("# pooled J1+J2+J3, $10 start; trades taken in brackets")
print(f"  {'system':44s} {'$10 ->':>8s} {'DD':>5s}")
for risk in (0.005, 0.0065, 0.008, 0.01):
    eq, tk, dd = R5.money(E, X, Rt, F_, Co, 8, risk)
    print(f"  baseline@8, risk {risk * 100:.2f}%{'':27s} {eq:8.2f} {dd * 100:4.0f}%  ({tk})")
for risk in (0.005, 0.0065):
    eq, tk, dd = R5.money(E, X, Rt, F_, Co, 12, risk)
    print(f"  baseline@12, risk {risk * 100:.2f}%{'':26s} {eq:8.2f} {dd * 100:4.0f}%  ({tk})")
eq, tk, dd = R5.money(E, X, Rt, F_ / Mu, Co, 8, 0.005); print(f"  SIZE (bonus x1.5), risk 0.50%{'':15s} {eq:8.2f} {dd * 100:4.0f}%  ({tk})")
eq, tk, dd = R5.money(np.concatenate([E, AE]), np.concatenate([X, AX]), np.concatenate([Rt, AR]), np.concatenate([F_, AF]), np.concatenate([Co, AC]), 12, 0.005)
print(f"  baseline + AVWAP add-ons@12, risk 0.50%{'':5s} {eq:8.2f} {dd * 100:4.0f}%  ({tk})")
