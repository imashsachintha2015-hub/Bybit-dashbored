"""EXPLORATORY follow-up (after dt_run_out.txt): what should the improved Kalman system do in downtrends - short, or stand aside?
Kalman + BTC filter trades (kal_trades.pkl) re-costed with REAL funding; long+short vs long-only (flat when BTC trends down)."""
import pickle, collections
import numpy as np
from kal_improve import pick, eqs, year, FEE, FUND_BAR
import funding as FU
G = pickle.load(open("kal_trades.pkl", "rb"))
x = pick(G, (1e-4, 1.0, "z", "btc")); rows = []
for r in x:
    rp = (FEE + FUND_BAR * r["bars"]) / (r["Rg"] - r["R"]); f, _ = FU.paid(r["sym"], r["side"], r["te"], r["tx"])
    rows.append(dict(r, R=r["Rg"] - FEE / rp - f / rp))
both = rows; longs = [r for r in rows if r["side"] == "L"]
print("=== Kalman + BTC filter, REAL funding: long + short vs long-only (stand aside in BTC downtrends) ===")
for lab, a_, b_ in (("2020-2026", 2020, 2026), ("2022 (bear year)", 2022, 2022), ("2024-2026", 2024, 2026), ("2025-2026", 2025, 2026)):
    sel = lambda xs: [r for r in xs if a_ <= year(r["t"]) <= b_]
    print(f"  {lab:17s} long+short {eqs(sel(both), 0.005, 8)} | long-only {eqs(sel(longs), 0.005, 8)}")
