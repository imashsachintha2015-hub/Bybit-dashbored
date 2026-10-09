"""Monthly trade counts of the final funding-aware Kalman trend system (BASE + SHORT_FUND + LONG_FUND_CAP), using exactly the
portfolio rules of the $10 result (0.5% risk, max 8 open, one position per coin), 2020-01 .. 2026-09, all coins."""
import pickle, time, collections
import numpy as np
import funding as FU
from ls_improve import trades
E = pickle.load(open("ls_entries.pkl", "rb"))
for r in E:
    r["favg"] = None; ft, fcum = FU.series(r["sym"])
    if ft:
        b = int(np.searchsorted(ft, r["t"] + 4 * 3600000, side="right"))
        if b >= 9: r["favg"] = (fcum[b] - fcum[b - 9]) / 9
E2 = [r for r in E if not (r["side"] == "L" and r["favg"] is not None and r["favg"] > 0.0003)]
cand = trades(E2, {"SHORT_FUND"})                     # (te, sym, R, tx, t_signal, side), one per coin already
openp = []; taken = []; eq = 10.0
for te, sym, R, tx, t0, sd in sorted(cand):
    for p in sorted([p for p in openp if p[0] <= te]): openp.remove(p); eq += p[1]
    if len(openp) >= 8 or any(p[2] == sym for p in openp) or eq < 1: continue
    openp.append((tx, eq * 0.005 * R, sym)); taken.append((te, sym, R, tx, sd))
ym = lambda ms_: time.strftime("%Y-%m", time.gmtime(ms_ / 1000))
months = [f"{y}-{m:02d}" for y in range(2020, 2027) for m in range(1, 13) if (y, m) <= (2026, 9)]   # price data ends 2026-09-30
assert all(ym(x[0]) in months for x in taken), "a trade falls outside the month list"
cnt = collections.Counter(ym(x[0]) for x in taken); cl = collections.Counter(ym(x[0]) for x in taken if x[4] == "L"); cs = collections.Counter(ym(x[0]) for x in taken if x[4] == "S")
win = collections.Counter(ym(x[0]) for x in taken if x[2] > 0)
print(f"trades taken 2020-01 .. 2026-09 (data to 2026-09-30): {len(taken)} over {len(months)} months (signals before the 8-position limit: {len(cand)})")
print(f"average per month: {len(taken)/len(months):.1f} trades ({sum(cl.values())/len(months):.1f} long, {sum(cs.values())/len(months):.1f} short), "
      f"median month {np.median([cnt[m] for m in months]):.0f}, quietest {min(cnt[m] for m in months)}, busiest {max(cnt[m] for m in months)}")
print("quietest months: " + ", ".join(f"{m} ({cnt[m]})" for m in sorted(months, key=lambda m: cnt[m])[:5])
      + " | months with fewer than 10 trades: " + str(sum(1 for m in months if cnt[m] < 10)))
hold = [(x[3] - x[0]) / 86400000 for x in taken]
print(f"average holding time {np.mean(hold):.1f} days (median {np.median(hold):.1f}); winners {sum(1 for x in taken if x[2] > 0)/len(taken)*100:.0f}% of trades")
print("\nper year: trades per month on average (long / short), winning trades per month")
for y in range(2020, 2027):
    ms_ = [m for m in months if m.startswith(str(y))]
    tot = sum(cnt[m] for m in ms_); L = sum(cl[m] for m in ms_); S = sum(cs[m] for m in ms_); W = sum(win[m] for m in ms_)
    print(f"  {y}: {tot/len(ms_):5.1f} per month ({L/len(ms_):4.1f} long / {S/len(ms_):4.1f} short) | winners {W/len(ms_):4.1f} per month | {len(ms_)} months, {tot} trades")
print("\nlast 12 months:")
for m in months[-12:]: print(f"  {m}: {cnt[m]:3d} trades ({cl[m]} long, {cs[m]} short), {win[m]} winners")
