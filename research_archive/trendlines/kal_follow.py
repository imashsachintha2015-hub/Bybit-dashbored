"""Follow-up checks on kal_improve.py (EXPLORATORY: designed after seeing kal_improve_out.txt, so not an out-of-sample test).
(a) How much do results depend on a few huge winners?  (b) The BTC-trend filter (the most consistent slice, 7/7 years)
year by year against the baseline, and a factor-level walk-forward: adopt the filter in year Y only if it beat the baseline over all
earlier years.  (c) Portfolio settings for the baseline and the BTC-filtered version."""
import pickle, collections, time, calendar
import numpy as np
from kal_improve import pick, st_, eqs, year, BASE
G = pickle.load(open("kal_trades.pkl", "rb"))
A0, A1 = (calendar.timegm(time.strptime(d, "%Y-%m-%d")) * 1000 for d in ("2020-01-01", "2021-06-01"))
base = pick(G, BASE); btcv = pick(G, (1e-4, 1.0, "z", "btc"))
print("=== (a) dependence on the biggest winners ===")
for lab, x in (("baseline 2020-21 (pre-registered window)", [r for r in base if A0 <= r["t"] < A1]), ("baseline 2020-2026", base), ("BTC-filtered 2020-2026", btcv)):
    R = np.array(sorted(r["R"] for r in x)); n = len(R); k1 = max(1, n // 100)
    s = st_(x); s_wo = st_([r for r in x if r["R"] < R[-1]])
    print(f"  {lab:42s} n={n} avg {R.mean():+.3f}R (t={s[2]:+.1f}) | median {np.median(R):+.3f}R | without the single best trade {R[:-1].mean():+.3f}R (t={s_wo[2]:+.1f}) "
          f"| without the top 1% ({k1} trades) {R[:-k1].mean():+.3f}R | best trade {R[-1]:+.1f}R")
print("\n=== (b) only trade in the direction of BTC's 1D trend vs baseline, by year (avg net R, trades) ===")
yrs = range(2020, 2027); adopt_wf = []; base_wf = []
for y in yrs:
    b = [r["R"] for r in base if year(r["t"]) == y]; f = [r["R"] for r in btcv if year(r["t"]) == y]
    prior_b = [r for r in base if year(r["t"]) < y]; prior_f = [r for r in btcv if year(r["t"]) < y]
    adopt = bool(prior_b) and np.mean([r["R"] for r in prior_f]) > np.mean([r["R"] for r in prior_b])
    if y >= 2021:
        adopt_wf += [r for r in (btcv if adopt else base) if year(r["t"]) == y]; base_wf += [r for r in base if year(r["t"]) == y]
    print(f"  {y}: baseline {np.mean(b):+.3f} ({len(b)}) | BTC-filtered {np.mean(f):+.3f} ({len(f)}) | {'filter better' if np.mean(f) > np.mean(b) else 'baseline better'}"
          + (f" | walk-forward adopts filter: {adopt}" if y >= 2021 else ""))
for lab, x in (("factor walk-forward 2021-2026", adopt_wf), ("baseline 2021-2026", base_wf)):
    s = st_(x); print(f"  {lab:30s} n={s[0]} avg {s[1]:+.3f}R t={s[2]:+.1f}")
print("\n=== (c) $10 portfolios 2020-2026 ===")
for lab, x in (("baseline", base), ("BTC-filtered", btcv)):
    x = sorted(x, key=lambda r: r["te"])
    for risk, mx in ((0.01, 3), (0.005, 8), (0.0033, 12)):
        print(f"  {lab:13s} risk {risk*100:.2f}% max {mx:2d} open: $10 -> {eqs(x, risk, mx)}")
    for a_, b_, lab2 in ((2022, 2026, "2022-2026 only"), (2025, 2026, "2025-2026 only")):
        xx = [r for r in x if a_ <= year(r["t"]) <= b_]
        print(f"  {lab:13s} {lab2}, risk 0.5% max 8: $10 -> {eqs(xx, 0.005, 8)}")
