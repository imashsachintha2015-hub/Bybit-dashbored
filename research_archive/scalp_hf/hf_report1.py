"""Stage 1 report: gross edge per cell and horizon in one segment, the screen, and the DEV-only selection (PREREG_scalp_hf.md).
usage: python3 hf_report1.py PKL SEG [select.json]"""
import sys, json, pickle
import numpy as np
import hf_core as C
from hf_stage1 import horizons

pkl, seg = sys.argv[1], sys.argv[2]
D = pickle.load(open(pkl, "rb"))
rows = []
for (fam, var), d in sorted(D.items(), key=lambda kv: (kv[0][0], str(kv[0][1]))):
    m = C.seg_mask(d["t"], seg)
    hz = horizons(fam)
    for k, h in enumerate(hz):
        st = C.cluster_stats(d["g"][m, k], d["t"][m] // C.DAY)
        rows.append(dict(fam=fam, var=var, h=h, **st))
print(f"# Stage 1, segment {seg}: gross mean bps per event (entry at the next open, exit at the close h minutes later), day-clustered t; K = {len(rows)} cells")
print("# break-even: 14 bps (taker round trip), 4 bps (optimistic maker round trip)")
fams = []
for r in rows:
    if r["fam"] not in fams: fams.append(r["fam"])
sel = {"screen": [], "best": {}}
for fam in fams:
    fr = [r for r in rows if r["fam"] == fam]
    print(f"\n## {fam}  ({len(fr)} cells, events per cell: median {int(np.median([r['n'] for r in fr]))})")
    print(f"{'variant':44s} {'h':>4s} {'n':>8s} {'mean bps':>9s} {'t':>7s}")
    for r in sorted(fr, key=lambda r: -(r["t"] if r["t"] == r["t"] else -99))[:6]:
        print(f"{str(r['var']):44s} {r['h']:4d} {r['n']:8d} {r['mean']:9.2f} {r['t']:7.2f}")
    ok = [r for r in fr if r["n"] >= 300 and r["t"] == r["t"]]
    if ok:
        b = max(ok, key=lambda r: r["t"]); sel["best"][fam] = dict(var=b["var"], h=b["h"], n=b["n"], mean=b["mean"], t=b["t"])
    else:
        print("  too few events (no cell with n >= 300)")
    for r in fr:
        if r["n"] >= 300 and r["mean"] >= 4 and r["t"] >= 3.6: sel["screen"].append(dict(fam=fam, var=r["var"], h=r["h"], n=r["n"], mean=r["mean"], t=r["t"]))
print("\n## screen (DEV gross >= 4 bps, t >= 3.6, n >= 300):", len(sel["screen"]), "cells")
for s in sel["screen"]: print("  ", s)
print("\n## best cell per family by DEV t (n >= 300) -> Stage 2 reporting:")
for fam, b in sel["best"].items(): print(f"  {fam:9s} {str(b['var']):40s} h={b['h']:<3d} n={b['n']:7d} mean {b['mean']:6.2f} bps  t {b['t']:6.2f}")
if len(sys.argv) > 3:
    json.dump(sel, open(sys.argv[3], "w"), default=lambda o: list(o) if isinstance(o, tuple) else str(o), indent=1)
