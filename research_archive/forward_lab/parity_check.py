"""Check that backend_lib/amd_fvg.py (live forward test) reproduces the backtest engine
(disc_reference.py, family AMD) trade-for-trade. Usage: python parity_check.py <folder with SYM_1H.json OKX rows>
Result when added (2026-09-30): 1,107 reference trades on 33 coins x 900 days, 0 mismatches."""
import json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(HERE, "..", ".."))
import disc_reference as disc
from backend_lib import amd_fvg as M
P = dict(fam="AMD", **{k: v for k, v in M.PRIMARY.items() if k != "name"})
folder = sys.argv[1]; tot = mism = 0
for f in sorted(os.listdir(folder)):
    if not f.endswith("_1H.json"): continue
    rows = json.load(open(os.path.join(folder, f)))
    if len(rows) < 500: continue
    for inv in (False, True):
        A = disc.prep(disc.load(os.path.join(folder, f), inv), f.split("_")[0]); A["side"] = "S" if inv else "L"; disc.A_FEE[0] = M.FEE_LIMIT
        ref = {(t, round(r, 4)) for t, s, r in disc.scan(A, P)}
        mine = {(x["fill_t"], x["R"]) for x in M.scan(M.prep([tuple(r) for r in rows], inv), M.PRIMARY, "S" if inv else "L") if x["status"] in ("CLOSED", "OPEN")}
        tot += len(ref); mism += len(ref ^ mine)
print("reference trades", tot, "mismatches", mism)
