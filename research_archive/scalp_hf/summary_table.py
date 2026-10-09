"""Builds the markdown table of README.md from the saved outputs (DEV selection log + frozen-rule evaluations)."""
import re, json
fr = json.load(open("stage2_frozen.json")); sel = json.load(open("stage1_dev_select.json"))
dev = open("stage2_dev_out.txt").read().split("\n## ")[1:]
def dev_row(fam):
    cfg = fr[fam]["cfg"]
    for b in dev:
        if b.startswith(fam + " "):
            for ln in b.splitlines():
                if f"stop {cfg['stop']:.0f}xATR5, " + (f"target {cfg['tp']}R" if cfg["tp"] else "no target") + ", taker" in ln:
                    m = re.search(r"net\s+(-?[\d.]+) bps/trade.*?\$10 -> \$\s*([\d.]+) \((\d+) trades", ln); return m.groups()
def ev(seg, which, fam):
    txt = open(f"stage2_eval_{which}_{seg}.txt").read()
    for b in txt.split("\n## ")[1:]:
        if b.startswith(fam + " "):
            m = re.search(r"taker execution \(the pass test\): n\s+(\d+) \| net\s+(-?[\d.]+) bps/trade.*?\$10 -> \$\s*([\d.]+) \((\d+) trades", b)
            return m.group(2), m.group(3), m.group(4)
    return None
rows = ["| family (best DEV cell) | best gross edge DEV | DEV | VAL | FINAL | OLD 2021-22 | UNSEEN coins |", "|---|---|---|---|---|---|---|"]
for fam, b in sel["best"].items():
    cells = [f"{fam} `{b['var']}` h={b['h']}", f"{b['mean']:+.1f} bps (t {b['t']:.1f})"]
    d = dev_row(fam); cells.append(f"{d[0]} bps, ${d[1]} ({d[2]} tr)")
    for seg, which in (("VAL", "design"), ("FINAL", "design"), ("OLD", "design"), ("FINAL", "unseen")):
        r = ev(seg, which, fam); cells.append(f"{r[0]} bps, ${r[1]} ({r[2]} tr)" if r else "n/a")
    rows.append("| " + " | ".join(cells) + " |")
open("summary_table.md", "w").write("\n".join(rows) + "\n"); print("\n".join(rows))
