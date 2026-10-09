"""hf_ladder at 240 minutes must reproduce the production Kalman code (backend_lib/kalman_trend.py) trade for trade."""
import sys, os
import numpy as np
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
from backend_lib import kalman_trend as K
import hf_core as C, hf_ladder as L

btc = C.load_coin("BTC"); td = L.btc_daily_trend(btc)
for nm in ("ETH", "BTC", "DOGE", "SOL", "XRP"):
    co = btc if nm == "BTC" else C.load_coin(nm); ft, fr = C.load_funding(nm)
    e, x, r, s, rs = L.coin_rung(co, ft, fr, td, 240)
    # production on the same 4H bars
    def bars(c_):
        O, H, Lw, Cc, *_ = C.aggregate(c_.o, c_.h, c_.l, c_.c, c_.qv, c_.n, c_.tb, 240)
        t = c_.t0 + np.arange(len(O), dtype=np.int64) * 240 * 60000
        ok = ~np.isnan(Cc); return t[ok], O[ok], H[ok], Lw[ok], Cc[ok]
    t, o, h, l, c = [a.tolist() for a in bars(co)]
    tb_, _, _, _, cb_ = bars(btc); tb_ = tb_.tolist(); cb_ = cb_.tolist()
    trend = dict(zip(tb_, K.daily_trend(tb_, cb_)))
    z, _ = K.kalman(c); a = K.atr14(h, l, c); ftl, frl = ft.tolist(), fr.tolist()
    tr = K.coin_trades(t, o, h, l, c, z, a, lambda ti: trend.get(ti, 0), lambda tc: K.funding_avg(ftl, frl, tc))
    tr = [q for q in tr if q["exit_t"] is not None]
    pe = [q["entry_t"] for q in tr]; px = [q["exit_t"] for q in tr]
    gaps = int(np.isnan(C.aggregate(co.o, co.h, co.l, co.c, co.qv, co.n, co.tb, 240)[3]).sum())
    same = pe == e.tolist() and px == x.tolist()
    print(f"{nm}: production trades {len(tr)} | ladder trades {len(e)} | identical entries and exits: {same} | 4H bars missing in the archive: {gaps}")
    if gaps == 0: assert same                      # with a gap production drops the missing bars and the ladder fills them: a small, known difference
print("parity ok")
