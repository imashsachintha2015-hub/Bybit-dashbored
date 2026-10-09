"""markdown table of the round-2 reporting cells (best DEV cell per scenario x regime x side, frozen, run once)"""
import os, pickle
import hf2_setups as S
import hf2_eval as E
d = pickle.load(open(os.path.join(S.OUT, "final_results.pkl"), "rb"))
print("| Situation | Regime | Side | Frozen cell (setup, TF, filter, exit) | DEV R | VAL trades / R / $10 | FINAL trades / R / $10 (maxDD) | UNSEEN trades / R / $10 | OLD trades / $10 |")
print("|---|---|---|---|---|---|---|---|---|")
for r, res in d["report"]:
    c = r["cell"]; nm = f"{S.SETUPS[c[0]]} {c[2]}m {c[4]} {S.EXITS[c[5]]}"
    def f(seg): s = res[seg]; return f"{s['st']['n']} / {s['st']['mean']:+.2f} / ${s['eq']:.2f}"
    fi = res["FINAL"]
    print(f"| {r['scenario']} | {r['regime']} | {'long' if r['side'] == 1 else 'short'} | {nm} | {r['dev_R']:+.2f} | {f('VAL')} | "
          f"{fi['st']['n']} / {fi['st']['mean']:+.2f} / ${fi['eq']:.2f} ({fi['mdd'] * 100:.0f}%) | {f('UNSEEN')} | {res['OLD']['st']['n']} / ${res['OLD']['eq']:.2f} |")
fin = [res["FINAL"]["eq"] for _, res in d["report"]]
print(f"\nFINAL $10 results above $10: {sum(x > 10 for x in fin)} of {len(fin)}; passes: {sum(res['pass'] for _, res in d['report'])}")
